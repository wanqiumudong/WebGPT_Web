from ..interface.optimize_interface import *
from  ..utils.utils import image_to_base64
from ..config.settings import get_output_image_path
from ..config.settings import get_backend_url
import os

# model_choices = [simpleilt_opt, levelset_opt, multilevel_opt, curvmulti_opt, neural_ilt_opt]
model_choices = {"simpleilt":simpleilt_opt,
                 "neural_ilt":neural_ilt_opt,
                 "levelset":levelset_opt,
                 "multilevel":multilevel_opt,
                 "curvmulti":curvmulti_opt}

def optimize_handler(args):
    print(f"Optimizing for target: {args.target}")
    print(f"Using model: {args.model}")
    print(f"Output result: {args.output_result}")
    # Add optimization logic here
    print(f"output_metrics: {args.output_metrics}")
    print(f"tile_sizeX: {args.tile_sizeX}")
    print(f"tile_sizeY: {args.tile_sizeY}")
    print(f"output_format: {args.output_format}")
    
    kwargs={
        'output_result': args.output_result,
        'output_metrics': args.output_metrics,
        'tile_sizeX': args.tile_sizeX,
        'tile_sizeY': args.tile_sizeY,
        'output_format': args.output_format
    }
    
    # output_image_root_path="/data/Web-FabGPT/LLM/litho/litho_code/output_image/"
    output_image_root_path=get_output_image_path()
    # import pdb;pdb.set_trace()
    result=model_choices[args.model](args.target, output_image_root_path, **kwargs)
    
    
    
    output_mask_path,output_image_path,output_message=result
    
    # data = {
    #     "title": "litho_optimize",
    # }
    
    # content=[]
    # content.append({"type": "paragraph", "text": "这是优化过后的mask图像"})
    # content.append({"type": "image", "data": image_to_base64(output_mask_path), "caption": "图1：优化后的mask图像"})
    
    # if kwargs.get("output_result",True):
    #     content.append({"type": "paragraph", "text": "这是优化过后的mask的print图像"})
    #     content.append({"type": "image", "data": image_to_base64(output_image_path), "caption": "图2：优化后的print图像"})
    # if kwargs.get("output_metrics",True):
    #     content.append({"type": "paragraph", "text": output_message})
    
    # data["content"]=content
    # return data


    backend_url=get_backend_url()
    # os.path.basename(target_path)
    mask_url = f"{backend_url}/static/output/"  + os.path.basename(output_mask_path)
    content=""
    content+= "这是优化过后的掩模图像\n"
    content+= f'<img src="{mask_url}" alt="image" width="300" height="300"> \n'
    
    
    if kwargs.get("output_result",True):
        content+="这是优化过后的掩模对应的显影图像\n"
        image_url = f"{backend_url}/static/output/"  + os.path.basename(output_image_path)
        content+= f'<img src="{image_url}" alt="image" width="300" height="300"> \n'
    if kwargs.get("output_metrics",True):
        content+=output_message
    
    return content
    