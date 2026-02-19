import sys

# openilt_root_path="/data/Web-FabGPT/LLM/litho_code/thirdparty/OpenILT/"
# sys.path.append(openilt_root_path)
from ..root_path import openilt_root_path
import time

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as func


from ..pycommon.settings import *
from ..pycommon import utils as common
from ..pycommon import glp as glp
from ..pylitho import simple as lithosim
# import pylitho.exact as lithosim

from ..pyilt import initializer as initializer
from ..pyilt import evaluation as evaluation



class SimpleCfg: 
    def __init__(self, config): 
        # Read the config from file or a given dict
        if isinstance(config, dict): 
            self._config = config
        elif isinstance(config, str): 
            self._config = common.parseConfig(config)
        required = ["Iterations", "TargetDensity", "SigmoidSteepness", "WeightEPE", "WeightPVBand", "WeightPVBL2", "StepSize", 
                    "TileSizeX", "TileSizeY", "OffsetX", "OffsetY", "ILTSizeX", "ILTSizeY"]
        for key in required: 
            assert key in self._config, f"[SimpleILT]: Cannot find the config {key}."
        intfields = ["Iterations", "TileSizeX", "TileSizeY", "OffsetX", "OffsetY", "ILTSizeX", "ILTSizeY"]
        for key in intfields: 
            self._config[key] = int(self._config[key])
        floatfields = ["TargetDensity", "SigmoidSteepness", "WeightEPE", "WeightPVBand", "WeightPVBL2", "StepSize"]
        for key in floatfields: 
            self._config[key] = float(self._config[key])
    
    def __getitem__(self, key): 
        return self._config[key]

class SimpleILT: 
    def __init__(self, config=SimpleCfg(openilt_root_path+"./config/simpleilt2048.txt"), lithosim=lithosim.LithoSim(openilt_root_path+"./config/lithosimple.txt"), device=DEVICE, multigpu=False): 
        super(SimpleILT, self).__init__()
        self._config = config
        self._device = device
        # Lithosim
        self._lithosim = lithosim.to(DEVICE)
        if multigpu: 
            self._lithosim = nn.DataParallel(self._lithosim)
        # Filter
        self._filter = torch.zeros([self._config["TileSizeX"], self._config["TileSizeY"]], dtype=REALTYPE, device=self._device)
        self._filter[self._config["OffsetX"]:self._config["OffsetX"]+self._config["ILTSizeX"], \
                     self._config["OffsetY"]:self._config["OffsetY"]+self._config["ILTSizeY"]] = 1
    
    
    def simulate(self, mask):
        
        # if not isinstance(params, torch.Tensor): 
        #     params = torch.tensor(params, dtype=REALTYPE, device=self._device)
        # mask = torch.sigmoid(self._config["SigmoidSteepness"] * params) * self._filter
        # mask += torch.sigmoid(self._config["SigmoidSteepness"] * params) * (1.0 - self._filter)
        printedNom, printedMax, printedMin = self._lithosim(mask)
        return printedNom, printedMax, printedMin
        
    def solve(self, target, params, curv=None, verbose=0): 
        # Initialize
        if not isinstance(target, torch.Tensor): 
            target = torch.tensor(target, dtype=REALTYPE, device=self._device)
        if not isinstance(params, torch.Tensor): 
            params = torch.tensor(params, dtype=REALTYPE, device=self._device)
        backup = params
        params = params.clone().detach().requires_grad_(True)

        # Optimizer 
        opt = optim.SGD([params], lr=self._config["StepSize"])
        # opt = optim.Adam([params], lr=self._config["StepSize"])

        # Optimization process
        lossMin, l2Min, pvbMin = 1e12, 1e12, 1e12
        bestParams = None
        bestMask = None
        for idx in range(self._config["Iterations"]): 
            mask = torch.sigmoid(self._config["SigmoidSteepness"] * params) * self._filter
            mask += torch.sigmoid(self._config["SigmoidSteepness"] * backup) * (1.0 - self._filter)
            printedNom, printedMax, printedMin = self._lithosim(mask)
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


def simpleilt_opt(target_path,out_image_root_path,**kwargs): 
    # output_image_path="/data/Web-FabGPT/LLM/litho_code/output_image/MOSAIC_test.png"
    import os
    output_mask_path=os.path.join(out_image_root_path,os.path.basename(target_path).split(".")[0]+"_simpleilt_mask.png")
    SCALE = 1
    cfg   = SimpleCfg(openilt_root_path+"./config/simpleilt2048.txt")
    litho = lithosim.LithoSim(openilt_root_path+"./config/lithosimple.txt")
    solver = SimpleILT(cfg, litho)
    design = glp.Design(target_path, down=SCALE)
    design.center(cfg["TileSizeX"], cfg["TileSizeY"], cfg["OffsetX"], cfg["OffsetY"])
    
    ### target 2048*2048
    target, params = initializer.PixelInit().run(design, cfg["TileSizeX"], cfg["TileSizeY"], cfg["OffsetX"], cfg["OffsetY"])
    
    begin = time.time()
    
    ### bestMask 2048*2048
    l2, pvb, bestParams, bestMask = solver.solve(target, params, curv=None)
    runtime = time.time() - begin
    
    ref = glp.Design(target_path, down=1)
    ref.center(cfg["TileSizeX"]*SCALE, cfg["TileSizeY"]*SCALE, cfg["OffsetX"]*SCALE, cfg["OffsetY"]*SCALE)
    target, params = initializer.PixelInit().run(ref, cfg["TileSizeX"]*SCALE, cfg["TileSizeY"]*SCALE, cfg["OffsetX"]*SCALE, cfg["OffsetY"]*SCALE)
    
    cv2.imwrite(output_mask_path, (bestMask * 255).detach().cpu().numpy())
    # l2, pvb, epe, shot = evaluation.evaluate(bestMask, target, litho, scale=SCALE, shots=True)
    # print(f"Result: L2 {l2:.0f}; PVBand {pvb:.0f}; EPE {epe:.0f}; Shot: {shot:.0f}; SolveTime: {runtime:.2f}s")
    

    output_image_path=None
    if kwargs.get("output_result",True):
        # 这里得看看printedNom要不要乘255
        # import pdb;pdb.set_trace()
        output_image_path=os.path.join(out_image_root_path,os.path.basename(target_path).split(".")[0]+"_simpleilt_printed.png")
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
    

# def simpleilt_list(target_list): 
#     SCALE = 1
#     l2s = []
#     pvbs = []
#     epes = []
#     shots = []
#     runtimes = []
#     cfg   = SimpleCfg("./config/simpleilt2048.txt")
#     litho = lithosim.LithoSim("./config/lithosimple.txt")
#     solver = SimpleILT(cfg, litho)
#     for idx,target_path in enumerate(target_list): 
#         design = glp.Design(target_path, down=SCALE)
#         design.center(cfg["TileSizeX"], cfg["TileSizeY"], cfg["OffsetX"], cfg["OffsetY"])
#         target, params = initializer.PixelInit().run(design, cfg["TileSizeX"], cfg["TileSizeY"], cfg["OffsetX"], cfg["OffsetY"])
        
#         begin = time.time()
#         l2, pvb, bestParams, bestMask = solver.solve(target, params, curv=None)
#         runtime = time.time() - begin
        
#         ref = glp.Design(target_path, down=1)
#         ref.center(cfg["TileSizeX"]*SCALE, cfg["TileSizeY"]*SCALE, cfg["OffsetX"]*SCALE, cfg["OffsetY"]*SCALE)
#         target, params = initializer.PixelInit().run(ref, cfg["TileSizeX"]*SCALE, cfg["TileSizeY"]*SCALE, cfg["OffsetX"]*SCALE, cfg["OffsetY"]*SCALE)
#         l2, pvb, epe, shot = evaluation.evaluate(bestMask, target, litho, scale=SCALE, shots=True)
#         cv2.imwrite(f"./tmp/MOSAIC_test{idx}.png", (bestMask * 255).detach().cpu().numpy())

#         print(f"[Testcase {idx}] [{target_path}]: L2 {l2:.0f}; PVBand {pvb:.0f}; EPE {epe:.0f}; Shot: {shot:.0f}; SolveTime: {runtime:.2f}s")

#         l2s.append(l2)
#         pvbs.append(pvb)
#         epes.append(epe)
#         shots.append(shot)
#         runtimes.append(runtime)
    
#     print(f"[Result]: L2 {np.mean(l2s):.0f}; PVBand {np.mean(pvbs):.0f}; EPE {np.mean(epes):.1f}; Shot {np.mean(shots):.1f}; SolveTime {np.mean(runtimes):.2f}s")



def simpleilt_sim(maskfile,out_image_root_path,**kwargs): 
    import os
    output_image_path=os.path.join(out_image_root_path,os.path.basename(maskfile).split(".")[0]+"_printed.png")
    
    
    if maskfile[-4:] == ".png": 
        target = cv2.imread(maskfile)[:, :, 0] / 255
        target = cv2.resize(target, (2048, 2048))
        mask=torch.from_numpy(target).to(DEVICE)
    else: 
        ref = glp.Design(maskfile, down=1)
        ref.center(2048, 2048, 0, 0)
        target = ref.mat(2048, 2048, 0, 0)
    
        mask, params = initializer.PixelInit().run(target, 2048,2048,0,0)
    
    cfg   = SimpleCfg(openilt_root_path+"./config/simpleilt2048.txt")
    litho = lithosim.LithoSim(openilt_root_path+"./config/lithosimple.txt")
    solver = SimpleILT(cfg, litho)
    printedNom, printedMax, printedMin = solver._lithosim(mask)
    
    

    
    # 这里得看看printedNom要不要乘255  

    printedNom, printedMax, printedMin = solver.simulate(mask)
    output_format=kwargs.get("output_format","printedNom")
    if output_format=="printedNom":
        cv2.imwrite(output_image_path, (printedNom * 255).detach().cpu().numpy())
    elif output_format=="printedMax":
        cv2.imwrite(output_image_path, (printedMax * 255).detach().cpu().numpy())
    elif output_format=="printedMin":
        cv2.imwrite(output_image_path, (printedMin * 255).detach().cpu().numpy())


    return output_image_path


if __name__ == "__main__": 
    serial()
    
    # parallel()
