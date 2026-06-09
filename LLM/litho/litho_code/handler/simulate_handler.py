from ..interface.simulate_interface import *
from  ..utils.utils import image_to_base64
from ..config.settings import get_output_image_path
from ..config.settings import get_backend_url
import os
# model_choices = [simpleilt_sim, levelset_sim, multilevel_sim, curvmulti_sim, nerul-ilt_sim]
# model_choices = {"simpleilt":simpleilt_sim}
def simulate_handler(args):
    print(f"Simulate for mask: {args.mask}")
    # print(f"Using model: {args.model}")
    # Add optimization logic here
    # print(f"output_metrics: {args.output_metrics}")
    print(f"tile_sizeX: {args.tile_sizeX}")
    print(f"tile_sizeY: {args.tile_sizeY}")
    print(f"output_format: {args.output_format}")
    
    kwargs={
        'tile_sizeX': args.tile_sizeX,
        'tile_sizeY': args.tile_sizeY,
        'output_format': args.output_format
    }
    # output_image_root_path="/data/Web-FabGPT/LLM/litho/litho_code/output_image/"
    output_image_root_path=get_output_image_path() 
    result=simpleilt_sim(args.mask, output_image_root_path, **kwargs)
        
    output_image_path=result

    
    # data = {
    #     "title": "litho_simulate",
    # }
    # # 暂不支持glp格式的mask
    # content=[]
    # content.append({"type": "paragraph", "text": "这是模拟得到的print图像"})
    # content.append({"type": "image", "data": image_to_base64(output_image_path), "caption": "图1：模拟得到的print图像"})
    # data["content"]=content
    # return data
    

    # 暂不支持glp格式的mask
    content=""
    content+="这是模拟得到的显影图像\n"
    backend_url=get_backend_url()
    image_url = f"{backend_url}/static/output/"  + os.path.basename(output_image_path)
    content+= f'<img src="{image_url}" alt="image" width="300" height="300"> \n'
    return content
    