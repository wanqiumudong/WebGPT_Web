import os
import sys
# sys.path.append(".")
# sys.path.append(os.path.dirname(__file__))
import time
import math
import multiprocessing as mp

import numpy as np
import cv2
import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as func

from ..root_path import openilt_root_path
from ..pycommon.settings import *
from ..pycommon import utils as common
from ..pycommon import glp as glp
from ..pylitho import simple as lithosim
# import pylitho.exact as lithosim

from ..pyilt import initializer as initializer
from ..pyilt import evaluation as evaluation

class LevelSetCfg: 
    def __init__(self, config): 
        # Read the config from file or a given dict
        if isinstance(config, dict): 
            self._config = config
        elif isinstance(config, str): 
            self._config = common.parseConfig(config)
        required = ["Iterations", "TargetDensity", "SigmoidSteepness", "WeightEPE", "WeightPVBL2", "WeightPVBand", "StepSize", 
                    "TileSizeX", "TileSizeY", "OffsetX", "OffsetY", "ILTSizeX", "ILTSizeY"]
        for key in required: 
            assert key in self._config, f"[SimpleILT]: Cannot find the config {key}."
        intfields = ["Iterations", "TileSizeX", "TileSizeY", "OffsetX", "OffsetY", "ILTSizeX", "ILTSizeY"]
        for key in intfields: 
            self._config[key] = int(self._config[key])
        floatfields = ["TargetDensity", "SigmoidSteepness", "WeightEPE", "WeightPVBL2", "WeightPVBand", "StepSize"]
        for key in floatfields: 
            self._config[key] = float(self._config[key])
    
    def __getitem__(self, key): 
        return self._config[key]
    

def gradImage(image): 
    GRAD_STEPSIZE = 1.0
    image = image.view([-1, 1, image.shape[-2], image.shape[-1]])
    padded = func.pad(image, (1, 1, 1, 1), mode='replicate')[:, 0].detach()
    gradX = (padded[:, 2:, 1:-1] - padded[:, :-2, 1:-1]) / (2.0 * GRAD_STEPSIZE)
    gradY = (padded[:, 1:-1, 2:] - padded[:, 1:-1, :-2]) / (2.0 * GRAD_STEPSIZE)
    return gradX.view(image.shape), gradY.view(image.shape)
    
class _Binarize(torch.autograd.Function): 
    @staticmethod
    def forward(ctx, levelset): 
        ctx.save_for_backward(levelset)
        mask = torch.zeros_like(levelset)
        mask[levelset < 0] = 1.0
        return mask
    
    @staticmethod
    def backward(ctx, grad_output): 
        levelset, = ctx.saved_tensors
        gradX, gradY = gradImage(levelset)
        l2norm = torch.sqrt(gradX**2 + gradY**2)
        return -l2norm * grad_output
    
class Binarize(nn.Module): 
    def __init__(self): 
        super(Binarize, self).__init__()
        pass

    def forward(self, levelset): 
        return _Binarize.apply(levelset)

class LevelSet(nn.Module): 
    def __init__(self, lithosim): 
        super(LevelSet, self).__init__()
        self._binarize = Binarize()
        self._lithosim = lithosim
        # self.add_module("binary", self._binarize)
        # self.add_module("lithosim", self._lithosim)

    def forward(self, params): 
        mask = self._binarize(params)
        printedNom, printedMax, printedMin = self._lithosim(mask)
        return mask, printedNom, printedMax, printedMin


class LevelSetILT: 
    def __init__(self, config=LevelSetCfg(openilt_root_path+"./config/pylevelset2048.txt"), lithosim=lithosim.LithoSim(openilt_root_path+"./config/lithosimple.txt"), device=DEVICE, multigpu=False): 
        super(LevelSetILT, self).__init__()
        self._config = config
        self._device = device
        # LevelSet
        self._levelset = LevelSet(lithosim).to(DEVICE)
        if multigpu: 
            self._levelset = nn.DataParallel(self._levelset)
        # Filter
        self._filter = torch.zeros([self._config["TileSizeX"], self._config["TileSizeY"]], dtype=REALTYPE, device=self._device)
        self._filter[self._config["OffsetX"]:self._config["OffsetX"]+self._config["ILTSizeX"], \
                     self._config["OffsetY"]:self._config["OffsetY"]+self._config["ILTSizeY"]] = 1
    def simulate(self, mask):
        
        # if not isinstance(params, torch.Tensor): 
        #     params = torch.tensor(params, dtype=REALTYPE, device=self._device)
        # mask = torch.sigmoid(self._config["SigmoidSteepness"] * params) * self._filter
        # mask += torch.sigmoid(self._config["SigmoidSteepness"] * params) * (1.0 - self._filter)

        printedNom, printedMax, printedMin = self._levelset._lithosim(mask)
        return printedNom, printedMax, printedMin

    def solve(self, target, params, curv=None, verbose=0): 
        # Initialize
        backup = params
        params = params.clone().detach().requires_grad_(True)

        # Optimizer 
        # opt = optim.SGD([params], lr=self._config["StepSize"])
        opt = optim.Adam([params], lr=self._config["StepSize"])
        
        # Optimization process
        lossMin, l2Min, pvbMin = 1e12, 1e12, 1e12
        bestParams = None
        bestMask = None
        for idx in range(self._config["Iterations"]): 
            mask, printedNom, printedMax, printedMin = self._levelset(params * self._filter + backup * (1.0 - self._filter))
            l2loss = func.mse_loss(printedNom, target, reduction="sum")
            pvbl2 = func.mse_loss(printedMax, target, reduction="sum") + func.mse_loss(printedMin, target, reduction="sum")
            pvbloss = func.mse_loss(printedMax, printedMin, reduction="sum")
            pvband = torch.sum((printedMax >= self._config["TargetDensity"]) != (printedMin >= self._config["TargetDensity"]))
            loss = l2loss + self._config["WeightPVBL2"] * pvbl2 + self._config["WeightPVBand"] * pvbloss
            if not curv is None: 
                kernelCurv = torch.tensor([[-1.0/16, 5.0/16, -1.0/16], [5.0/16, -1.0, 5.0/16], [-1.0/16, 5.0/16, -1.0/16]], dtype=REALTYPE, device=DEVICE)
                curvature = func.conv2d(mask[None, None, :, :], kernelCurv[None, None, :, :])[0, 0]
                losscurv = func.mse_loss(curvature, torch.zeros_like(curvature), reduction="sum")
                loss += curv * losscurv
            if verbose == 1: 
                print(f"[Iteration {idx}]: L2 = {l2loss.item():.0f}; PVBand: {pvband.item():.0f}")

            if bestParams is None or bestMask is None or loss.item() < lossMin: 
                lossMin, l2Min, pvbMin = loss.item(), l2loss.item(), pvband.item()
                bestParams = params.detach().clone()
                bestMask = mask.detach().clone()
            
            opt.zero_grad()
            loss.backward()
            opt.step()
        
        return l2Min, pvbMin, bestParams, bestMask


def levelset_opt(target_path,out_image_root_path,**kwargs): 
    output_mask_path=os.path.join(out_image_root_path,os.path.basename(target_path).split(".")[0]+"_levelset_mask.png")
    SCALE = 1
    l2s = []
    pvbs = []
    epes = []
    shots = []
    runtimes = []
    targetsAll = []
    paramsAll = []
    cfg   = LevelSetCfg(openilt_root_path+"./config/pylevelset2048.txt")
    litho = lithosim.LithoSim(openilt_root_path+"./config/lithosimple.txt")
    solver = LevelSetILT(cfg, litho)
    # for idx in range(1, 11): 
    design = glp.Design(target_path, down=SCALE)
    design.center(cfg["TileSizeX"], cfg["TileSizeY"], cfg["OffsetX"], cfg["OffsetY"])
    target, params = initializer.LevelSetInitTorch().run(design, cfg["TileSizeX"], cfg["TileSizeY"], cfg["OffsetX"], cfg["OffsetY"])
        
    begin = time.time()
    l2, pvb, bestParams, bestMask = solver.solve(target, params, curv=None)
    runtime = time.time() - begin
        
    ref = glp.Design(target_path, down=1)
    ref.center(cfg["TileSizeX"]*SCALE, cfg["TileSizeY"]*SCALE, cfg["OffsetX"]*SCALE, cfg["OffsetY"]*SCALE)
    target, params = initializer.LevelSetInitTorch().run(ref, cfg["TileSizeX"]*SCALE, cfg["TileSizeY"]*SCALE, cfg["OffsetX"]*SCALE, cfg["OffsetY"]*SCALE)
    # l2, pvb, epe, shot = evaluation.evaluate(bestMask, target, litho, scale=SCALE, shots=True)
    cv2.imwrite(output_mask_path, (bestMask * 255).detach().cpu().numpy())

    # print(f"Result: L2 {l2:.0f}; PVBand {pvb:.0f}; EPE {epe:.0f}; Shot: {shot:.0f}; SolveTime: {runtime:.2f}s")
    output_image_path=None
    if kwargs.get("output_result",True):
        # 这里得看看printedNom要不要乘255
        
        output_image_path=os.path.join(out_image_root_path,os.path.basename(target_path).split(".")[0]+"_levelset_printed.png")
        printedNom, printedMax, printedMin = solver.simulate(bestMask)
        output_format=kwargs.get("output_format","printedNom")
        if output_format=="printedNom":
            cv2.imwrite(output_image_path, (printedNom * 255).detach().cpu().numpy())
        elif output_format=="printedMax":
            cv2.imwrite(output_image_path, (printedMax * 255).detach().cpu().numpy())
        elif output_format=="printedMin":
            cv2.imwrite(output_image_path, (printedMin * 255).detach().cpu().numpy())

        
    output_message=None
    if kwargs.get("output_metrics",True):
        l2, pvb, epe, shot = evaluation.evaluate(bestMask, target, litho, scale=SCALE, shots=True)
        print(f"Result: L2 {l2:.0f}; PVBand {pvb:.0f}; EPE {epe:.0f}; Shot: {shot:.0f}; SolveTime: {runtime:.2f}s")
        output_message=f"Result: L2 {l2:.0f}; PVBand {pvb:.0f}; EPE {epe:.0f}; Shot: {shot:.0f}; SolveTime: {runtime:.2f}s"
        
    # output_image_path=output_image_path
    return output_mask_path,output_image_path,output_message


if __name__ == "__main__": 
    serial()
    # parallel()
