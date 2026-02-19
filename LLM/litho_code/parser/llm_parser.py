from langchain_community.llms import Tongyi
import json
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

session = requests.Session()
adapter = HTTPAdapter(
    pool_connections=20,
    pool_maxsize=40,
    max_retries=Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST"]
    )
)
session.mount("https://", adapter)
session.mount("http://", adapter)

# API 相关配置
API_URL = "https://api.siliconflow.cn/v1/chat/completions"

use_siliconflow = True
SILICON_API_KEY = "sk-hhngtklpccdtjuiqajxlfnwberpjleiznbernfrtebzkhddp"
# siliconflow_model = "Pro/deepseek-ai/DeepSeek-V3-1226"
siliconflow_model = "Pro/deepseek-ai/DeepSeek-V3"
# siliconflow_model = "Qwen/Qwen2.5-VL-72B-Instruct"

# siliconflow调用
def get_action_and_params_siliconflow(prompt, max_tokens=2048, model=siliconflow_model, use_rag=False, query=None, analysis=""):
    """使用 SiliconFlow API 调用通义千问模型，生成命令 JSON，逻辑与 get_action_and_params 一致"""
    
    # 创建请求头
    headers = {
        "Authorization": f"Bearer {SILICON_API_KEY}",
        "Content-Type": "application/json"
    }
    
    system_prompt = """
你是来自浙江大学的光刻工艺助手，专精于55nm工艺节点下的半导体光刻制程，使用193nm波长、环形照明系统进行前向模拟、掩模优化和结果评估。你既能解析和执行用户指令，也能详细回答用户关于光刻工艺的各种问题。

当用户明确要执行光刻操作时，请按以下要求返回：
Output must be in valid JSON format with 'action' and 'parameters' fields.
If the action cannot be recognized or the user just want to ask related information, return {"action": null, "parameters": {}}.
If the parameters are not provided enough or if any parameter is provided with multiple values(e.g., optimize --model ["simplilt", "levelset"]), return {"action": null, "parameters": {}}.
When it comes to model, your response should be in lower case and replace - with _. 

当用户询问你的功能或能力时，请通过返回 {"action": null, "parameters": {}} 让其他函数处理，不要在这里直接回答。
"""

    user_prompt = f"""Available commands and parameters（适用于55nm工艺节点，193nm波长光刻系统）:

- simulate: Forward process simulation
    Required: --mask [image_file]
    Optional: --output_format [printedNom/printedMax/printedMin], --tile_sizeX [int], --tile_sizeY [int]

- optimize: Optimization process
    Required: --target [image_file], --model [simpleilt/levelset/multilevel/curvmulti/neural_ilt]   Only one option in the parameter candidates can be selected, and its characters (including capitalization and underscores) must match a candidate choice in the list exactly. In other words, the characters should be in lower case.
    Optional: --output_result [True/False], --output_format [printedNom/printedMax/printedMin], 
                --output_metrics [True/False], --tile_sizeX [int], --tile_sizeY [int]

- evaluate: Evaluation process
    Required: --mask [image_file], --target [image_file]

Parse this user input and return JSON: "{prompt}"

The analysis of the user input is: "{analysis}"
"""
    
    # 请求负载
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0,  # 保持确定性输出
        "top_p": 0.5
    }
    
    try:
        response = session.post(API_URL, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()
        raw_content = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        
        # 处理返回内容，与 get_action_and_params 的处理方式一致
        response = raw_content.strip("`")
        if response.startswith("json"):
            response = response[4:]
        
        try:
            result = json.loads(response)
        except json.JSONDecodeError:
            print("当提取指令时，未检测到有效的JSON!")
            result = {"action": None, "parameters": {}}  # 修正这里，将 null 改为 None
        
        return result
    
    except requests.exceptions.Timeout:
        print("通义千问 API请求超时")
        return {"action": None, "parameters": {}}
    except requests.exceptions.RequestException as e:
        error_message = f"通义千问请求失败: {e}"
        if hasattr(e.response, 'text'):
            error_message += f"\nAPI 返回的错误详情: {e.response.text}"
        print(error_message)
        return {"action": None, "parameters": {}}
    except ValueError as e:
        print(f"通义千问 JSON解析错误: {e}")
        return {"action": None, "parameters": {}}

def analyze_user_intent_siliconflow(user_input, model=siliconflow_model):
    """使用 SiliconFlow API 分析用户意图，逻辑与 analyze_user_intent 一致"""
    
    # 创建请求头
    headers = {
        "Authorization": f"Bearer {SILICON_API_KEY}",
        "Content-Type": "application/json"
    }
    
    system_prompt = """你是来自浙江大学的光刻工艺助手，专精于55nm工艺节点下的半导体光刻制程，使用193nm波长、环形照明系统进行前向模拟、掩模优化和结果评估。

当用户询问你的功能或能力时，不要仅返回格式化的"需求理解"，而是应该提供详细的功能介绍，例如：

"我是浙江大学开发的光刻工艺助手，专注于55nm工艺节点的半导体制造技术。我的主要特点和功能包括：

1. 工艺规格：
   - 工艺节点：55nm（满足现代集成电路制造需求）
   - 光源：193nm波长ArF准分子激光（行业标准光刻光源）
   - 照明系统：环形照明（提升图像对比度和分辨率）

2. 核心功能：
   - 前向模拟(simulate)：使用霍普金斯衍射模型，模拟掩模在晶圆上的打印效果
   
   - 掩模优化(optimize)：支持多种先进算法改进掩模设计
     * simpleilt：经典ILT算法，适合一般场景
     * levelset：水平集算法，通过演化掩模边界提高质量
     * multilevel：多级分辨率优化，平衡计算效率与精度
     * curvmulti：曲线重定向算法，生成高质量曲线掩模
     * neural_ilt：神经网络辅助ILT，结合机器学习预测掩模
   
   - 结果评估(evaluate)：计算多种指标评估掩模质量
     * L2误差：测量与目标图像的整体偏差
     * PV带：工艺窗口评估指标
     * EPE：边缘放置误差测量
     * 其他业界标准指标

3. 文件支持：处理.glp格式掩模文件和标准图像格式(.png等)"

此时，禁止输出额外的commands命令和"-- xx"等选项

如果用户明确想要执行光刻操作，请严格按以下结构组织回应：
让我理解一下您的需求：
1. 您想要执行的操作：[操作名称]
2. 需要的参数：[详细说明所需参数]

请保证回答的丰富性，提供光刻技术的专业背景和上下文，帮助用户全面了解系统功能和特点。
"""
    
    # 用户提示
    user_prompt = f"""Available commands and parameters:

- simulate: Forward process simulation
    Required: --mask [image_file]
    Optional: --output_format [printedNom/printedMax/printedMin], --tile_sizeX [int], --tile_sizeY [int]

- optimize: Optimization process
    Required: --target [image_file], --model [SimpleILT(经典的ILT优化过程)/LevelSet(通过演化掩模边界来提高掩模质量)/MultiLevel(结合多级分辨率掩模优化来提高掩模质量)/CurvMulti(通过结合曲线设计重定向和可微分形态学算子，直接生成高质量的曲线掩模)/Neural-ILT(通过神经网络进行掩模预测和ILT校正)] , only one model can be selected
    Optional: --output_result [True/False], --output_format [printedNom(经过nominal过程的显影图像)/printedMax(经过max过程的显影图像)/printedMin((经过min过程的显影图像))], 
            --output_metrics [True/False], --tile_sizeX(处理块的X方向长度) [int], --tile_sizeY(处理块的Y方向长度) [int]

- evaluate: Evaluation process
    Required: --mask [image_file], --target [image_file]

Please analyze this user input: "{user_input}"
If the user is asking about functionalities or capabilities, provide a comprehensive response about the system.
"""
    
    # 请求负载
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 1024,
        "temperature": 0,
        "top_p": 0.5
    }
    
    try:
        response = session.post(API_URL, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()
        analysis = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        return analysis
    
    except requests.exceptions.Timeout:
        print("通义千问分析 API请求超时")
        return "API请求超时，请稍后再试。"
    except requests.exceptions.RequestException as e:
        error_message = f"通义千问分析请求失败: {e}"
        if hasattr(e.response, 'text'):
            error_message += f"\nAPI 返回的错误详情: {e.response.text}"
        print(error_message)
        return error_message
    except ValueError as e:
        print(f"通义千问分析 JSON解析错误: {e}")
        return f"处理API响应时出错: {str(e)}"

# 基于langchain的Tongyi模型
def create_llm():
    return Tongyi(
        model="qwen-max",
        temperature=0,
        max_tokens=None,
        streaming=False,
        api_key='sk-5e04724bd43c48df9598b540d66be587'
    )

def get_action_and_params(user_input, analysis="", verbose=False):
    """获取操作和参数，返回命令字符串"""
    if use_siliconflow:
        result = get_action_and_params_siliconflow(user_input, analysis=analysis)
    else:
        llm = create_llm()

        system_prompt = """You are a command parser that analyzes user input and determines the appropriate action and parameters. If the user want to execute lithography operations, Actions could be in one of \{simulate, optimize, evaluate\}
Output must be in valid JSON format with 'action' and 'parameters' fields.
If the action cannot be recognized or the user just want to ask related information, return {"action": null, "parameters": {}}.
If the parameters are not provided enough or if any parameter is provided with multiple values(e.g., optimize --model ["simplilt", "levelset"]), return {"action": null, "parameters": {}}.
When it comes to model, your response should be in lower case and replace - with _. 
"""

        user_prompt = f"""Available commands and parameters:

- simulate: Forward process simulation
    Required: --mask [image_file]
    Optional: --output_format [printedNom/printedMax/printedMin], --tile_sizeX [int], --tile_sizeY [int]

- optimize: Optimization process
    Required: --target [image_file], --model [simpleilt/levelset/multilevel/curvmulti/neural_ilt]   Only one option in the parameter candidates can be selected, and its characters (including capitalization and underscores) must match a candidate choice in the list exactly. In other words, the charactars should be in lower case.
    Optional: --output_result [True/False], --output_format [printedNom/printedMax/printedMin], 
                --output_metrics [True/False], --tile_sizeX [int], --tile_sizeY [int]

- evaluate: Evaluation process
    Required: --mask [image_file], --target [image_file]


Parse this user input and return JSON: "{user_input}"

The analysis of the user input is: "{analysis}"
"""

        full_prompt = f"{system_prompt}\n\n{user_prompt}"
        response = llm.invoke(full_prompt)
        response = response.strip("`")
        if response.startswith("json"):
            response = response[4:]
        if verbose:
            print(f"response after process: {response}")
        try:
            result = json.loads(response)
        except json.JSONDecodeError:
            if verbose:
                print("When extracting instructions, No valid Json detected!")
            result = {"action": None, "parameters": {}}
    
    # 将JSON结果转换为命令字符串
    return format_command(result)

def format_command(json_result):
    """将JSON结果格式化为命令字符串"""
    if not json_result or json_result.get('action') is None:
        return ""
    
    action = json_result['action']
    params = json_result['parameters']
    
    # 开始构建命令
    cmd_parts = [action]
    
    # 添加每个参数及其值
    for key, value in params.items():
        if isinstance(key, str):
            key = key.strip("-")
        
        # 处理布尔值或True/False字符串
        if isinstance(value, bool) or (isinstance(value, str) and value.lower() in ['true', 'false']):
            # 对于output_metrics, output_result等参数，只有当值为True时才添加，不需要值
            if value is True or (isinstance(value, str) and value.lower() == 'true'):
                cmd_parts.append(f"--{key}")
        else:
            cmd_parts.append(f"--{key} {value}")
    
    # 用空格连接所有部分
    return " ".join(cmd_parts)

def analyze_user_intent(user_input):
    """分析用户输入并解释推理过程"""
    if use_siliconflow:
        return analyze_user_intent_siliconflow(user_input)
    else:
        llm = create_llm()
        
        system_prompt = """You are an assistant analyzing user requests for lithography operations.
Analyze the user's request and explain your understanding in a friendly, conversational Chinese tone.
If you think the user wants to execute lithography operations, Structure your response like:

让我理解一下您的需求：
1. 您想要执行的操作：[操作名称]
2. 需要的参数：[详细说明所需参数]

Available operations:
- simulate: Forward simulation (前向模拟)
- optimize: Mask optimization (掩模优化)
- evaluate: Result evaluation (结果评估)

Else If you think the user wants to ask related information about lithography operations, you can tell him the related information you known. 
Else If you think the user wants to ask irrelevant things, you can just respond with a friendly message.
"""

        user_prompt = f"""Available commands and parameters:

- simulate: Forward process simulation
    Required: --mask [image_file]
    Optional: --output_format [printedNom/printedMax/printedMin], --tile_sizeX [int], --tile_sizeY [int]

- optimize: Optimization process
    Required: --target [image_file], --model [SimpleILT(经典的ILT优化过程)/LevelSet(通过演化掩模边界来提高掩模质量)/MultiLevel(结合多级分辨率掩模优化来提高掩模质量)/CurvMulti(通过结合曲线设计重定向和可微分形态学算子，直接生成高质量的曲线掩模)/Neural-ILT(通过神经网络进行掩模预测和ILT校正)] ,only one model can be selected
    Optional: --output_result [True/False], --output_format [printedNom(经过nominal过程的显影图像)/printedMax(经过max过程的显影图像)/printedMin((经过min过程的显影图像))], 
            --output_metrics [True/False], --tile_sizeX(处理块的X方向长度) [int], --tile_sizeY(处理块的Y方向长度) [int]

- evaluate: Evaluation process
    Required: --mask [image_file], --target [image_file]

If parameter arguments are provided with multiple options, you should detect it, provide a additional friendly message to the user and tell the next llm not to generate actually action. 

Please analyze this user input: "{user_input}"
Output your analysis shortly without giving reasons.
"""

        full_prompt = f"{system_prompt}\n\n{user_prompt}"
        analysis = llm.invoke(full_prompt)
        return analysis

if __name__ == "__main__":
    # Test examples
    test_inputs = [
        "我想用 input_mask.glp 这个mask文件来模拟打印，输出 printedMax 格式的图像",
        "优化 target_image.png，使用 multilevel 模型，并输出评估指标",
        "评估 optimized_mask.glp 和 target_image.png",
        "今天天气怎么样？"
    ]

    for input_text in test_inputs:
        if use_siliconflow:
            # Use SiliconFlow implementation
            analysis = analyze_user_intent_siliconflow(input_text)
            json_result = get_action_and_params_siliconflow(input_text, analysis=analysis)
            cmd_str = format_command(json_result)
        else:
            # Use original Tongyi implementation
            analysis = analyze_user_intent(input_text)
            cmd_str = get_action_and_params(input_text, analysis=analysis)

        print(f"\nInput: {input_text}")
        print(f"Analysis: {analysis}")
        if use_siliconflow:
            print(f"Result JSON: {json.dumps(json_result, ensure_ascii=False, indent=2)}")
        print(f"Command String: {cmd_str}")