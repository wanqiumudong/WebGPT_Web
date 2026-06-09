import sys
# openilt_root_path="/data/Web-FabGPT/LLM/litho/litho_code/thirdparty/OpenILT/"
from ..root_path import openilt_root_path
# sys.path.append(openilt_root_path)


def parseConfig(filename): 
    with open(filename, "r") as fin: 
        lines = fin.readlines()
    results = {}
    for line in lines: 
        splited = line.strip().split()
        if len(splited) >= 2: 
            key = splited[0]
            value = splited[1]
            results[key] = value
            if key == "KernelDir": 
                # import pdb;pdb.set_trace()
                results[key] = openilt_root_path + results[key]
    return results
        