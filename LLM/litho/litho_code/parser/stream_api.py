"""
stream_api.py - 流式输出API实现

为SiliconFlow模型API提供流式输出能力，支持流式和非流式模式
"""

import json
import os
import requests
import time
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

# 添加流式输出控制标志
ENABLE_STREAMING = False  # 设置为 False 则默认使用非流式模式

# 创建HTTP会话和重试策略
session = requests.Session()
session.trust_env = False
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

def _default_litho_api_url():
    return os.getenv("WEB_FABGPT_VL_API_BASE_URL", "https://api.siliconflow.cn/v1/chat/completions")


# API 相关配置
API_URL = os.getenv("WEB_FABGPT_LITHO_LLM_API_URL", _default_litho_api_url())
SILICON_API_KEY = os.getenv(
    "WEB_FABGPT_LITHO_LLM_API_KEY",
    os.getenv("WEB_FABGPT_SILICONFLOW_API_KEY", ""),
)
siliconflow_model = os.getenv(
    "WEB_FABGPT_LITHO_LLM_MODEL",
    os.getenv("WEB_FABGPT_VL_MODEL", "Qwen/Qwen2.5-VL-72B-Instruct"),
)

LITHO_OPERATION_HINTS = (
    "simulate",
    "optimize",
    "evaluate",
    "仿真",
    "模拟",
    "优化",
    "评估",
    "mask",
    "glp",
    "printed",
    "输出格式",
)

LITHO_GREETING_HINTS = (
    "你好",
    "您好",
    "hi",
    "hello",
    "你是谁",
    "介绍一下你自己",
    "你能做什么",
    "你会什么",
    "有什么功能",
    "能帮我做什么",
)

LITHO_CAPABILITY_INTRO = """您好！我是浙江大学开发的光刻工艺助手，专注于 55nm 工艺节点下的半导体光刻流程分析与优化。

我目前主要支持三类任务：
1. 前向仿真（simulate）
   用已有 mask 文件模拟 printedNom / printedMax / printedMin 等成像结果。
2. 掩模优化（optimize）
   支持 simpleilt、levelset、multilevel、curvmulti、neural_ilt 等优化模型。
3. 结果评估（evaluate）
   对 mask 与 target 的匹配效果做误差和质量评估。

常见输入方式包括：
- “我想对 M1_test1.glp 做 optimize，使用 simpleilt”
- “请模拟某个 mask 的 printedMax 成像结果”
- “帮我评估 mask 和 target 的匹配情况”

如果您已经有文件，可以直接上传；如果您有明确操作目标，也可以直接把需求发给我。"""


def is_capability_or_greeting_query(user_input):
    if not user_input:
        return False

    normalized = user_input.strip().lower()
    has_operation_hint = any(hint in normalized for hint in LITHO_OPERATION_HINTS)
    has_greeting_hint = any(hint in normalized for hint in LITHO_GREETING_HINTS)
    return has_greeting_hint and not has_operation_hint

class StreamingResponseHandler:
    """处理流式响应的工具类"""
    
    def __init__(self):
        self.full_response = ""
        self.start_time = time.time()
    
    def on_chunk(self, chunk):
        """处理接收到的文本块，返回处理后的文本块"""
        self.full_response += chunk
        return chunk
    
    def on_complete(self):
        """完成处理时调用，返回总响应时间和完整响应"""
        elapsed_time = time.time() - self.start_time
        return self.full_response, elapsed_time

def call_siliconflow_api(system_prompt, user_prompt, max_tokens=2048, model=siliconflow_model):
    """
    调用SiliconFlow API并返回非流式响应（兼容现有函数）
    
    Args:
        system_prompt: 系统提示
        user_prompt: 用户提示
        max_tokens: 最大生成令牌数
        model: 模型名称
    
    Returns:
        str: API响应的content部分
    """
    # 创建请求头
    headers = {
        "Authorization": f"Bearer {SILICON_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # 请求负载
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0,  # 确保输出的一致性
        "top_p": 0.5,
        "stream": False  # 非流式模式
    }
    
    try:
        # 发送请求并获取响应
        response = session.post(
            API_URL, 
            headers=headers, 
            json=payload,
            stream=False,
            timeout=30  # 设置超时时间
        )
        response.raise_for_status()
        
        # 解析JSON响应
        result = response.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        return content
    except Exception as e:
        print(f"SiliconFlow API请求失败: {e}")
        return f"API请求错误: {str(e)}"

def call_siliconflow_api_streaming(system_prompt, user_prompt, max_tokens=2048, model=siliconflow_model, streaming=None):
    """
    调用SiliconFlow API并返回流式或非流式响应
    
    Args:
        system_prompt: 系统提示
        user_prompt: 用户提示
        max_tokens: 最大生成令牌数
        model: 模型名称
        streaming: 是否使用流式输出，None则使用全局设置
    
    Returns:
        requests.Response: 流式响应对象，或JSON结果（非流式）
    """
    # 确定是否使用流式输出
    use_streaming = ENABLE_STREAMING if streaming is None else streaming
    
    # 创建请求头
    headers = {
        "Authorization": f"Bearer {SILICON_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # 请求负载
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0,  # 确保输出的一致性
        "top_p": 0.5,
        "stream": use_streaming  # 根据参数决定是否启用流式响应
    }
    
    try:
        # 发送请求并获取响应
        response = session.post(
            API_URL, 
            headers=headers, 
            json=payload,
            stream=use_streaming,  # 根据参数决定是否启用流式响应
            timeout=30  # 设置超时时间
        )
        response.raise_for_status()
        
        # 对于非流式响应，直接返回JSON结果
        if not use_streaming:
            return response.json()
        
        # 对于流式响应，返回原始响应对象
        return response
    except Exception as e:
        print(f"SiliconFlow API请求失败: {e}")
        return None

def process_streaming_response(response, yield_function=None):
    """
    处理流式或非流式响应
    
    Args:
        response: 请求的响应对象或JSON结果
        yield_function: 可选的回调函数，用于将每个文本块传递给调用者
    
    Returns:
        generator: 生成器，产生每个文本块(流式)或完整响应(非流式)
    """
    # 检查响应是否为字典(非流式JSON响应)
    if isinstance(response, dict):
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
        if yield_function:
            yield_function(content)
        yield content
        return
    
    # 以下是原来的流式处理逻辑
    handler = StreamingResponseHandler()
    
    try:
        for line in response.iter_lines():
            if line:
                line = line.decode('utf-8')
                
                # 跳过以 "data: " 开头的前缀
                if line.startswith("data: "):
                    line = line[6:]
                
                # 跳过结束信号
                if line == "[DONE]":
                    break
                
                try:
                    # 解析JSON数据
                    json_data = json.loads(line)
                    delta = json_data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    
                    if delta:
                        # 处理文本块
                        processed_chunk = handler.on_chunk(delta)
                        
                        # 调用回调函数(如果提供)
                        if yield_function:
                            yield_function(processed_chunk)
                        
                        # 产生文本块
                        yield processed_chunk
                except json.JSONDecodeError:
                    continue
        
        # 完成处理
        full_response, elapsed_time = handler.on_complete()
        print(f"流式响应完成，耗时: {elapsed_time:.2f}秒，总长度: {len(full_response)}字符")
    except Exception as e:
        error_msg = f"处理流式响应时出错: {e}"
        print(error_msg)
        yield error_msg

def extract_complete_response(response):
    """
    从流式或非流式响应中提取完整的响应文本
    
    Args:
        response: 请求的响应对象或JSON结果
    
    Returns:
        str: 完整的响应文本
    """
    # 检查响应是否为字典(非流式JSON响应) 
    if isinstance(response, dict):
        return response.get("choices", [{}])[0].get("message", {}).get("content", "")
    
    # 以下是原来的流式处理逻辑
    full_text = ""
    
    try:
        for chunk in process_streaming_response(response):
            full_text += chunk
        return full_text
    except Exception as e:
        print(f"提取完整响应时出错: {e}")
        return f"提取响应出错: {str(e)}"

# 添加与llm_parser.py兼容的函数，使用非流式API
# def analyze_user_intent(user_input, model=siliconflow_model):
#     """
#     使用非流式API分析用户意图（与原llm_parser兼容）
    
#     Args:
#         user_input: 用户输入
#         model: 模型名称
    
#     Returns:
#         str: 分析结果
#     """
#     system_prompt = """你是来自浙江大学的光刻工艺助手，专精于55nm工艺节点下的半导体光刻制程，使用193nm波长、环形照明系统进行前向仿真、掩模优化和结果评估。

# 当用户询问你的功能或能力时，不要仅返回格式化的"需求理解"，而是应该提供详细的功能介绍，例如：

# "我是浙江大学开发的光刻工艺助手，专注于55nm工艺节点的半导体制造技术。我的主要特点和功能包括：

# 1. 工艺规格：
#    - 工艺节点：55nm（满足现代集成电路制造需求）
#    - 光源：193nm波长ArF准分子激光（行业标准光刻光源）
#    - 照明系统：环形照明（提升图像对比度和分辨率）

# 2. 核心功能：
#    - 前向仿真(simulate)：使用霍普金斯衍射模型，模拟掩模在晶圆上的成像效果
   
#    - 掩模优化(optimize)：支持多种先进算法改进掩模设计
#      * simpleilt：经典ILT算法，适合一般场景
#      * levelset：水平集算法，通过演化掩模边界提高质量
#      * multilevel：多级分辨率优化，平衡计算效率与精度
#      * curvmulti：曲线重定向算法，生成高质量曲线掩模
#      * neural_ilt：神经网络辅助ILT，结合机器学习预测掩模
   
#    - 结果评估(evaluate)：计算多种指标评估掩模质量
#      * L2误差：测量与目标图像的整体偏差
#      * PV带：工艺窗口评估指标
#      * EPE：边缘放置误差测量
#      * 其他业界标准指标

# 3. 文件支持：处理.glp格式掩模文件和标准图像格式(.png等)"

# 此时，禁止输出额外的commands命令和"-- xx"等选项

# 如果用户明确想要执行光刻操作，请严格按以下结构组织回应：
# 让我理解一下您的需求：
# 1. 您想要执行的操作：[操作名称]
# 2. 需要的参数：[详细说明所需参数]

# 请保证回答的丰富性，提供光刻技术的专业背景和上下文，帮助用户全面了解系统功能和特点。
# """

#     user_prompt = f"""Available commands and parameters:

# - simulate: Forward process simulation
#     Required: --mask [image_file]
#     Optional: --output_format [printedNom/printedMax/printedMin], --tile_sizeX [int], --tile_sizeY [int]

# - optimize: Optimization process
#     Required: --target [image_file], --model [SimpleILT(经典的ILT优化过程)/LevelSet(通过演化掩模边界来提高掩模质量)/MultiLevel(结合多级分辨率掩模优化来提高掩模质量)/CurvMulti(通过结合曲线设计重定向和可微分形态学算子，直接生成高质量的曲线掩模)/Neural-ILT(通过神经网络进行掩模预测和ILT校正)] , only one model can be selected
#     Optional: --output_result [True/False], --output_format [printedNom(经过nominal过程的显影图像)/printedMax(经过max过程的显影图像)/printedMin((经过min过程的显影图像))], 
#             --output_metrics [True/False], --tile_sizeX(处理块的X方向长度) [int], --tile_sizeY(处理块的Y方向长度) [int]

# - evaluate: Evaluation process
#     Required: --mask [image_file], --target [image_file]

# Please analyze this user input: "{user_input}"
# If the user is asking about functionalities or capabilities, provide a comprehensive response about the system.
# """
    
#     # 使用非流式API调用
#     return call_siliconflow_api(system_prompt, user_prompt, max_tokens=1024, model=model)

# def get_action_and_params(prompt, analysis="", max_tokens=2048, model=siliconflow_model):
#     """
#     使用非流式API生成操作和参数（与原llm_parser兼容）
    
#     Args:
#         prompt: 用户输入
#         analysis: 用户意图分析
#         max_tokens: 最大生成令牌数
#         model: 模型名称
    
#     Returns:
#         str: 格式化的命令字符串
#     """
#     system_prompt = """
# 你是来自浙江大学的光刻工艺助手，专精于55nm工艺节点下的半导体光刻制程，使用193nm波长、环形照明系统进行前向仿真、掩模优化和结果评估。你既能解析和执行用户指令，也能详细回答用户关于光刻工艺的各种问题。

# 当用户明确要执行光刻操作时，请按以下要求返回：
# Output must be in valid JSON format with 'action' and 'parameters' fields.
# If the action cannot be recognized or the user just want to ask related information, return {"action": null, "parameters": {}}.
# If the parameters are not provided enough or if any parameter is provided with multiple values(e.g., optimize --model ["simplilt", "levelset"]), return {"action": null, "parameters": {}}.
# When it comes to model, your response should be in lower case and replace - with _. 

# 当用户询问你的功能或能力时，请通过返回 {"action": null, "parameters": {}} 让其他函数处理，不要在这里直接回答。
# """

#     user_prompt = f"""Available commands and parameters（适用于55nm工艺节点，193nm波长光刻系统）:

# - simulate: Forward process simulation
#     Required: --mask [image_file]
#     Optional: --output_format [printedNom/printedMax/printedMin], --tile_sizeX [int], --tile_sizeY [int]

# - optimize: Optimization process
#     Required: --target [image_file], --model [simpleilt/levelset/multilevel/curvmulti/neural_ilt]   Only one option in the parameter candidates can be selected, and its characters (including capitalization and underscores) must match a candidate choice in the list exactly. In other words, the characters should be in lower case.
#     Optional: --output_result [True/False], --output_format [printedNom/printedMax/printedMin], 
#                 --output_metrics [True/False], --tile_sizeX [int], --tile_sizeY [int]

# - evaluate: Evaluation process
#     Required: --mask [image_file], --target [image_file]

# Parse this user input and return JSON: "{prompt}"

# The analysis of the user input is: "{analysis}"
# """
    
#     # 使用非流式API调用
#     response_text = call_siliconflow_api(system_prompt, user_prompt, max_tokens=max_tokens, model=model)
    
#     # 清理响应，移除可能的代码块标记
#     response = response_text.strip("`")
#     if response.startswith("json"):
#         response = response[4:]
    
#     # 尝试解析JSON
#     try:
#         result = json.loads(response)
#         return format_command(result)
#     except json.JSONDecodeError:
#         print("当提取指令时，未检测到有效的JSON!")
#         return ""

# 流式版本的函数
def analyze_user_intent_streaming(user_input, model=siliconflow_model, streaming=True):
    """
    使用流式API分析用户意图
    
    Args:
        user_input: 用户输入
        model: 模型名称
        streaming: 是否使用流式输出，默认为True
    
    Returns:
        generator: 流式响应生成器
    """
    if is_capability_or_greeting_query(user_input):
        yield LITHO_CAPABILITY_INTRO
        return

    system_prompt = """你是来自浙江大学的光刻工艺助手，专精于55nm工艺节点下的半导体光刻制程，使用193nm波长、环形照明系统进行前向仿真、掩模优化和结果评估。

当用户询问你的功能或能力时，且未说明执行功能操作时，应该提供详细的功能介绍，并在回复最后简要说明使用方法，例如：

"我是浙江大学开发的光刻工艺助手，专注于55nm工艺节点的半导体制造技术。

1. 支持的工艺规格：
   - 工艺节点：55nm
   - 光源：193nm波长ArF准分子激光
   - 照明系统：环形照明

2. 核心功能：
   - 前向仿真(simulate)：使用霍普金斯衍射模型，模拟掩模在晶圆上的成像效果
   
   - 掩模优化(optimize)：支持多种先进算法改进掩模设计
     * simpleilt(支持glp格式)：经典ILT算法，适合一般场景
     * levelset(支持glp格式)：水平集算法，通过演化掩模边界提高质量
     * multilevel(支持glp格式)：多级分辨率优化，平衡计算效率与精度
     * curvmulti(支持glp格式)：曲线重定向算法，生成高质量曲线掩模
     * neural_ilt(支持png格式)：神经网络辅助ILT，结合机器学习预测掩模
   
   - 结果评估(evaluate)：计算多种指标评估掩模质量
     * L2误差：测量与目标图像的整体偏差
     * PV带：工艺窗口评估指标
     * EPE：边缘放置误差测量
     * 其他业界标准指标

3. 文件支持：处理.glp格式掩模文件和标准图像格式(.png等)

您可以通过如下示例指令来使用我的功能：
- 「我想使用前向仿真获取[掩模文件名]的成像效果」
- 「我想对[掩模文件名]进行掩模优化，使用[模型名称]模型」
- 「我想评估掩模[掩模文件名]对目标图像[目标图像文件名]的匹配程度」

在掩模优化中，您可指定的可选参数有：
- 「输出格式：printedNom(经过nominal过程的显影图像)/printedMax(经过max过程的显影图像)/printedMin((经过min过程的显影图像))，默认为printedNom」
- 「是否输出优化指标(默认为False)」
- 「处理块的X和Y维度大小(默认为2048x2048)」

禁止输出额外的commands命令和"-- xx"等选项。

对于明确请求执行光刻操作的指令，请直接分析需求并生成指令，不要重复介绍你的功能和特点。请严格按以下结构组织回应：
让我理解一下您的需求：
1. 您想要执行的操作：[操作名称] [简要技术背景描述]
2. 需要的参数：
   * 必需参数：
      * [参数1]：[参数值]
      * [参数2]：[参数值]
      ...
   * 可选参数建议：
      * [参数1]：[简单说明]
      * [参数2]：[简单说明]
      ...

请帮助用户全面了解系统功能和特点，同时不要询问用户额外参数调整的问题。
"""

    user_prompt = f"""Available commands and parameters:

- simulate: Forward process simulation
    Required: --mask [image_file]
    Optional: --output_format [printedNom/printedMax/printedMin], --tile_sizeX [int], --tile_sizeY [int]

- optimize: Optimization process
    Required: --target [image_file], --model [simpleilt/levelset/multilevel/curvmulti/neural_ilt], --output_result [True]
    Optional: --output_format [printedNom/printedMax/printedMin], --output_metrics [True/False], --tile_sizeX [int], --tile_sizeY [int]

- evaluate: Evaluation process
    Required: --mask [image_file], --target [image_file]
    [Please note that there are no optional parameters here]

Please analyze this user input: "{user_input}"
"""

#     user_prompt = f"""Available commands and parameters:

# - simulate: Forward process simulation
#     Required: --mask [image_file]
#     Optional: --output_format [printedNom/printedMax/printedMin], --tile_sizeX [int], --tile_sizeY [int]

# - optimize: Optimization process
#     Required: --target [image_file], --model [SimpleILT(经典的ILT优化过程)/LevelSet(通过演化掩模边界来提高掩模质量)/MultiLevel(结合多级分辨率掩模优化来提高掩模质量)/CurvMulti(通过结合曲线设计重定向和可微分形态学算子，直接生成高质量的曲线掩模)/Neural-ILT(通过神经网络进行掩模预测和ILT校正)] , only one model can be selected
#     Optional: --output_result [True/False], --output_format [printedNom(经过nominal过程的显影图像)/printedMax(经过max过程的显影图像)/printedMin((经过min过程的显影图像))], 
#             --output_metrics [True/False], --tile_sizeX(处理块的X方向长度) [int], --tile_sizeY(处理块的Y方向长度) [int]

# - evaluate: Evaluation process
#     Required: --mask [image_file], --target [image_file]

# Please analyze this user input: "{user_input}"
# If the user is asking about functionalities or capabilities, provide a comprehensive response about the system.
# """
    
    # 获取流式响应
    response = call_siliconflow_api_streaming(system_prompt, user_prompt, max_tokens=1024, streaming=streaming)
    
    if response is None:
        yield "API请求失败，请稍后再试。"
        return
    
    # 处理流式响应
    for chunk in process_streaming_response(response):
        yield chunk

def get_action_and_params_streaming(prompt, max_tokens=2048, model=siliconflow_model, analysis="", streaming=True):
    """
    使用流式API生成操作和参数
    
    Args:
        prompt: 用户输入
        max_tokens: 最大生成令牌数
        model: 模型名称
        analysis: 用户意图分析
        streaming: 是否使用流式输出，默认为True
    
    Returns:
        generator: 流式响应生成器
    """
    if is_capability_or_greeting_query(prompt):
        yield '{"action": null, "parameters": {}}'
        return

    system_prompt = """
你是来自浙江大学的光刻工艺助手，专精于55nm工艺节点下的半导体光刻制程，使用193nm波长、环形照明系统进行前向仿真、掩模优化和结果评估。你既能解析和执行用户指令，也能详细回答用户关于光刻工艺的各种问题。

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
    Required: --target [image_file], --model [simpleilt/levelset/multilevel/curvmulti/neural_ilt]   Only one option in the parameter candidates can be selected, and its characters (including capitalization and underscores) must match a candidate choice in the list exactly. In other words, the characters should be in lower case. ----output_result
    Optional: --output_format [printedNom/printedMax/printedMin], --output_metrics [True/False], --tile_sizeX [int], --tile_sizeY [int]

- evaluate: Evaluation process
    Required: --mask [image_file], --target [image_file]

Parse this user input and return JSON: "{prompt}"

The analysis of the user input is: "{analysis}"
"""

#     user_prompt = f"""Available commands and parameters（适用于55nm工艺节点，193nm波长光刻系统）:

# - simulate: Forward process simulation
#     Required: --mask [image_file]
#     Optional: --output_format [printedNom/printedMax/printedMin], --tile_sizeX [int], --tile_sizeY [int]

# - optimize: Optimization process
#     Required: --target [image_file], --model [simpleilt/levelset/multilevel/curvmulti/neural_ilt]   Only one option in the parameter candidates can be selected, and its characters (including capitalization and underscores) must match a candidate choice in the list exactly. In other words, the characters should be in lower case.
#     Optional: --output_result [True/False], --output_format [printedNom/printedMax/printedMin], 
#                 --output_metrics [True/False], --tile_sizeX [int], --tile_sizeY [int]

# - evaluate: Evaluation process
#     Required: --mask [image_file], --target [image_file]

# Parse this user input and return JSON: "{prompt}"

# The analysis of the user input is: "{analysis}"
# """
    
    # 获取流式响应
    response = call_siliconflow_api_streaming(system_prompt, user_prompt, max_tokens, streaming=streaming)
    
    if response is None:
        yield "API请求失败，请稍后再试。"
        return
    
    # 处理流式响应
    for chunk in process_streaming_response(response):
        yield chunk

def get_command_from_streaming_response(response_text):
    """
    从流式或非流式响应文本中提取命令
    
    Args:
        response_text: 流式或非流式响应的完整文本
    
    Returns:
        str: 格式化的命令字符串
    """
    # 清理响应，移除可能的代码块标记
    response = response_text.strip("`")
    if response.startswith("json"):
        response = response[4:]
    
    # 尝试解析JSON
    try:
        result = json.loads(response)
    except json.JSONDecodeError:
        print("当提取指令时，未检测到有效的JSON!")
        return ""
    
    # 格式化命令
    return format_command(result)

def format_command(json_result):
    """将JSON结果格式化为命令字符串"""
    if not json_result or json_result.get('action') is None:
        return ""
    
    action = json_result['action']
    params = json_result['parameters']
    
    # 开始构建命令
    cmd_parts = [action]
    
    # 如果是optimize命令，确保添加output_result参数
    if action == "optimize" and "output_result" not in params:
        params["output_result"] = True
    
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

# def format_command(json_result):
#     """将JSON结果格式化为命令字符串"""
#     if not json_result or json_result.get('action') is None:
#         return ""
    
#     action = json_result['action']
#     params = json_result['parameters']
    
#     # 开始构建命令
#     cmd_parts = [action]
    
#     # 添加每个参数及其值
#     for key, value in params.items():
#         if isinstance(key, str):
#             key = key.strip("-")
        
#         # 处理布尔值或True/False字符串
#         if isinstance(value, bool) or (isinstance(value, str) and value.lower() in ['true', 'false']):
#             # 对于output_metrics, output_result等参数，只有当值为True时才添加，不需要值
#             if value is True or (isinstance(value, str) and value.lower() == 'true'):
#                 cmd_parts.append(f"--{key}")
#         else:
#             cmd_parts.append(f"--{key} {value}")
    
#     # 用空格连接所有部分
#     return " ".join(cmd_parts)
