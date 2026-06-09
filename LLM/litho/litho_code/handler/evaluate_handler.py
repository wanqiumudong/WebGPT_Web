from ..interface.evaluate_interface import *


def evaluate_handler(args):
    print(f"evaluate for mask: {args.mask}")
    print(f"evaluate for target: {args.target}")

    result=evaluate(args.mask,args.target)
    output_message=result

    
    # data = {
    #     "title": "litho_evaluate",
    # }
    

    # content=[]
    # content.append({"type": "paragraph", "text": output_message})
    
    content=output_message
    
    
    # data["content"]=content
    # return data
    return content
    