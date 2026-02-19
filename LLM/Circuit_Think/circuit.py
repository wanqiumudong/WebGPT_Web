from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS
import requests
from datetime import datetime
import json
import time
import logging
import os
import uuid
import random
from enum import Enum

# --- 本地模型相关导入 ---
import torch
from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor, BitsAndBytesConfig
from PIL import Image
import warnings
from werkzeug.utils import secure_filename
from transformers import TextIteratorStreamer
from threading import Thread

warnings.filterwarnings("ignore")

# =============== 资源清理配置 ===============
# 中止请求的超时清理机制
ABORT_CLEANUP_INTERVAL = 300  # 5分钟清理间隔
ABORT_TIMEOUT = 3600  # 1小时后自动清理未处理的中止请求

# 心跳检测配置
HEARTBEAT_INTERVAL = 30  # 30秒心跳间隔
CONNECTION_TIMEOUT = 90  # 90秒无心跳则认为连接断开

# =============== 多GPU配置 ===============
GPU_ID = int(os.environ.get('GPU_ID', '5'))  # 默认使用GPU 5
SERVICE_PORT = int(os.environ.get('SERVICE_PORT', '5007'))  # 默认端口5007

# 设置CUDA设备
# torch.cuda.set_device(GPU_ID)
os.environ['CUDA_VISIBLE_DEVICES'] = str(GPU_ID)

# =============== 推理模式配置 ===============
class InferenceMode(Enum):
    VLLM = "vllm"           # vLLM高性能推理
    QUANTIZED_8BIT = "8bit"  # 8bit量化
    NORMAL_16BIT = "16bit"   # 正常16bit推理

# 在这里选择你想要的推理模式
DESIRED_MODE = InferenceMode.VLLM

# 优先级顺序：如果首选模式失败，按此顺序尝试其他模式
FALLBACK_MODES = [
    InferenceMode.QUANTIZED_8BIT,
    InferenceMode.NORMAL_16BIT,
    InferenceMode.VLLM
]

# =============== 配置常量 ===============
MODEL_PATH = "/data/yphu/Web-FabGPT/LLM/Circuit_Think/huggingface-200/"
ORIGINAL_API_URL = "http://10.98.64.22:8080"  # 修正为本地服务器地址
BACKEND_URL = f"http://10.98.193.46:{SERVICE_PORT}"

# API配置 - 用于正常对话
API_URL = "https://api.siliconflow.cn/v1/chat/completions"
API_KEY = "sk-irsugzjxawzpmljctfsqjfcwziklolujvvrfznyojlzymksg"
MODEL_NAME = "Qwen/Qwen2.5-72B-Instruct"

# API请求头配置
api_headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}"
}

# 统一的文件存储配置 - 所有GPU实例共享
UPLOAD_BASE_DIR = "./files_shared"  # 统一存储目录，所有GPU实例共享

# 创建必要目录
os.makedirs(UPLOAD_BASE_DIR, exist_ok=True)

# =============== 全局变量 ===============
CURRENT_MODE = DESIRED_MODE
model = None
processor = None
vllm_engine = None
vllm_sampling_params = None

# 用户文件存储：{user_id: {conversation_id: {filename: file_info}}}
user_uploaded_files = {}

# =============== 日志配置 ===============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f"circuitthink_gpu{GPU_ID}.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(f"CircuitThink-Service-GPU{GPU_ID}")

logger.info(f"初始化Circuit服务 - GPU {GPU_ID}, 端口 {SERVICE_PORT}")
logger.info(f"文件存储目录: {UPLOAD_BASE_DIR}")
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# vLLM相关导入
try:
    from vllm import LLM, SamplingParams
    VLLM_AVAILABLE = True
    logger.info("vLLM版本可用，支持Qwen2.5-VL")
except ImportError:
    VLLM_AVAILABLE = False
    logger.warning("vLLM未安装，vLLM模式不可用")

# 检查qwen-vl-utils
try:
    import qwen_vl_utils
    QWEN_VL_UTILS_AVAILABLE = True
    logger.info("qwen-vl-utils可用")
except ImportError:
    QWEN_VL_UTILS_AVAILABLE = False
    if VLLM_AVAILABLE:
        logger.warning("缺少qwen-vl-utils，vLLM可能无法正确处理图片")

# =============== 工具函数 ===============
def generate_safe_message_id():
    """生成安全的32位整数范围内的messageId"""
    timestamp = int(time.time())
    # 使用时间戳的后7位+4位随机数，确保在int32范围内
    safe_id = (timestamp % 10000000) * 10000 + random.randint(1000, 9999)
    # 确保不超过int32最大值 2147483647
    if safe_id > 2147483647:
        # 如果超出范围，使用更简单的方式：当前秒数 + 随机数
        safe_id = (timestamp % 1000000) * 1000 + random.randint(100, 999)
    return safe_id

def convert_user_id_to_int(user_id):
    """将字符串userId转换为整数，以兼容Java后端"""
    if isinstance(user_id, int):
        return user_id
    elif isinstance(user_id, str):
        # 如果是字符串，尝试转换为数字
        if user_id.isdigit():
            return int(user_id)
        else:
            # 如果是非数字字符串，计算hash值并取模得到正整数
            import hashlib
            hash_value = int(hashlib.md5(user_id.encode()).hexdigest(), 16)
            return hash_value % 2147483647  # 确保在int32范围内
    else:
        # 默认返回1
        return 1

def preprocess_image_for_vllm(image_path, max_size=1024):
    """智能压缩图片以控制token使用"""
    from PIL import Image
    
    image = Image.open(image_path)
    width, height = image.size
    
    # 如果图片过大，等比例缩放
    if max(width, height) > max_size:
        ratio = max_size / max(width, height)
        new_width = int(width * ratio)
        new_height = int(height * ratio)
        image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        logger.info(f"图片已从{width}×{height}缩放至{new_width}×{new_height}")
        
        # 保存处理后的图片，覆盖原文件
        image.save(image_path, quality=90, optimize=True)
        return image
    
    return image

def check_file_type_by_suffix(filename):
    if not filename:
        return "unknown"
    file_extension = os.path.splitext(filename)[1].lower()
    if file_extension in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp']:
        return file_extension[1:]
    elif file_extension == '.glp':
        return 'glp'
    elif file_extension == '.zip':
        return 'zip'
    else:
        return 'unknown'

def proxy_to_original_backend(original_endpoint, method="GET", data=None, params=None, headers=None):
    """代理请求到原始8080后端"""
    try:
        url = f"{ORIGINAL_API_URL}/{original_endpoint}"
        default_headers = {"Content-Type": "application/json"}
        if headers:
            default_headers.update(headers)
            
        logger.info(f"代理请求到原始后端: URL={url}, 方法={method}")
        # 添加调试日志，记录发送的数据
        if data and method.upper() == "POST":
            logger.info(f"发送数据: {json.dumps(data, indent=2, ensure_ascii=False)}")
        
        if method.upper() == "GET":
            response = requests.get(url, params=params, headers=default_headers, timeout=10)
        elif method.upper() == "POST":
            response = requests.post(url, json=data, headers=default_headers, timeout=10)
        else:
            logger.error(f"不支持的HTTP方法: {method}")
            return None
            
        if 200 <= response.status_code < 300:
            try:
                return response.json()
            except json.JSONDecodeError:
                return response.text
        else:
            logger.error(f"原始后端返回错误: {response.status_code}, URL: {url}")
            # 添加详细的错误响应日志
            try:
                error_response = response.text
                logger.error(f"错误响应内容: {error_response}")
            except:
                logger.error("无法获取错误响应内容")
            return None
    except Exception as e:
        logger.error(f"代理到原始后端时出错: {str(e)}, URL: {original_endpoint}")
        return None

def ensure_session_exists(session_id, user_id, user_message=""):
    """确保会话存在，不存在则创建"""
    session_check = proxy_to_original_backend(f"session/get?sessionId={session_id}")
    if not session_check:
        logger.warning(f"会话 {session_id} 不存在,尝试创建")
        new_session = {
            "sessionId": session_id,
            "createTime": datetime.now().isoformat(),
            "header": user_message[:8] if user_message else "CircuitThink会话",
            "lastActive": datetime.now().isoformat(),
            "modelId": 5,
            "status": 1,
            "userId": convert_user_id_to_int(user_id)  # 修复：转换userId为整数
        }
        
        result = proxy_to_original_backend("session/add", method="POST", data=new_session)
        if not result:
            logger.error(f"创建新会话 {session_id} 失败")
            return False
        logger.info(f"已创建新CircuitThink会话: {session_id}")
    return True

class MESSAGE_TYPE:
    USER = 1  # 用户消息
    BOT = 0   # 机器人消息

# =============== API相关函数 ===============
def generate_with_api_retry(payload, max_retries=3, stream=False):
    """带重试机制的API调用，支持流式响应"""
    for attempt in range(max_retries):
        try:
            response = requests.post(API_URL, headers=api_headers, json=payload, stream=stream)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                logger.warning(f"API请求失败，{wait_time}秒后重试... 错误：{str(e)}")
                time.sleep(wait_time)
            else:
                raise
    return None

# =============== 文件处理函数 ===============
def save_uploaded_file(file_data, user_id, conversation_id, filename):
    """保存上传的文件到统一目录结构"""
    # 创建用户和对话专用目录
    user_dir = os.path.join(UPLOAD_BASE_DIR, secure_filename(user_id))
    conv_dir = os.path.join(user_dir, secure_filename(conversation_id))
    os.makedirs(conv_dir, exist_ok=True)
    
    # 生成唯一文件名
    timestamp = str(int(time.time()))
    safe_filename = secure_filename(filename)
    name, ext = os.path.splitext(safe_filename)
    unique_filename = f"{name}_{timestamp}{ext}"
    
    file_path = os.path.join(conv_dir, unique_filename)
    with open(file_path, 'wb') as f:
        f.write(file_data)
    
    logger.info(f"文件已保存到: {file_path}")
    return file_path, unique_filename

def get_latest_uploaded_image(user_id, conversation_id):
    """获取指定用户对话中最新上传的图像路径"""
    # 首先尝试从内存中获取（当前实例）
    if user_id in user_uploaded_files and conversation_id in user_uploaded_files[user_id]:
        files = user_uploaded_files[user_id][conversation_id]
        image_files = [f for f in files.values() if f.get('type', '').startswith('image/')]
        if image_files:
            latest_file = max(image_files, key=lambda x: x.get('upload_time', 0))
            file_path = latest_file.get('saved_path')
            if file_path and os.path.exists(file_path):
                return file_path
    
    # 如果内存中没有找到，从文件系统中查找（跨实例兼容）
    base_upload_dir = "/data/yphu/Web-FabGPT/LLM/Circuit_Think/uploaded_files"
    conv_dir = os.path.join(base_upload_dir, user_id, conversation_id)
    
    if not os.path.exists(conv_dir):
        return None
    
    # 查找最新的图像文件
    image_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp')
    image_files = []
    
    try:
        for filename in os.listdir(conv_dir):
            if filename.lower().endswith(image_extensions):
                file_path = os.path.join(conv_dir, filename)
                if os.path.isfile(file_path):
                    mtime = os.path.getmtime(file_path)
                    image_files.append((file_path, mtime))
        
        if not image_files:
            return None
        
        # 返回最新的图像文件
        latest_file_path = max(image_files, key=lambda x: x[1])[0]
        return latest_file_path
        
    except Exception as e:
        logger.error(f"从文件系统查找图像失败: {e}")
        return None

# =============== 系统提示词函数 ===============
def get_system_prompt(has_image=False):
    """根据是否有图片返回对应的系统提示词"""
    if not has_image:
        # 正常对话的prompt
        return """你是电路大模型，基础模型为Circuit-Think。作为电路设计智能助手，你由浙江大学开发。你专门帮助用户解决电路设计、电路分析、电路图像识别和转换等相关问题。

你的专业领域包括：
1. 电路设计和分析
2. 电路图像识别和解读  
3. 电路原理图绘制建议
4. 电路仿真和测试建议
5. 电路故障诊断和维修

当用户询问电路设计问题时，请提供专业且易懂的解答和建议。
请用专业但易懂的语言回答用户的电路相关问题。"""
    
    # 有图片时的prompt
    base_prompt = """你是电路大模型，基础模型为Circuit-Think。作为电路设计智能助手，你由浙江大学开发。你专门帮助用户解决电路设计、电路分析、电路图像识别和转换等相关问题。

你的专业领域包括：
1. 电路设计和分析
2. 电路图像识别和解读  
3. 电路原理图绘制建议
4. 电路仿真和测试建议
5. 电路故障诊断和维修

当用户询问电路设计问题时，请提供专业且易懂的解答和建议。
请用专业但易懂的语言回答用户的电路相关问题。"""

    return base_prompt + """
**首先，请注意用户上传的图片是否为电路图像，如果非电路图像，请直接说明：我被开发用于解析电路图像，暂时不支持其他图像的解析**
**关注用户的具体问题，根据用户问题决定如何分析电路图像。**

当用户询问电路设计问题时，请提供专业且易懂的解答和建议。
当用户上传电路图像时，你需要根据用户的具体问题来分析图像：

1. **如果用户明确提出了特定问题**，请直接针对用户问题分析电路图像并直接回答。

2. **如果用户要求生成网表或没有明确问题**，则按照以下格式生成网表：
   - 首先在思考并输出生成网表的推理过程，最终给出答案
   - 在推理过程中，完成识别Port（格式：name（统一为Port）[[bbox]]）、器件（格式：name（type）[[bbox]]）和等电位连接关系（格式：node<数字>：[器件name.端口, 器件name.端口...]）这三步具体的可量化的内容
   - 在输出<answer>中，通过网表的形式完整描述电路的结构，每一行必须用**列表**的形式包括，格式为：[器件名 节点1 节点2 节点3 器件类型]或[端口名 节点1 节点2]

部分器件类型及端口说明：
- NMOS/PMOS，三个端口：栅极(G)、漏极(D)、源极(S)
- PNP/NPN，三个端口：基极(B)、集电极(C)、发射极(E)
- Resistor/Capacitor/Inductor，无需标注端口
- Diode/CurrentSource/VoltageSource，两个端口：正极(+)、负极(-)
- Port为电路的输入/输出端口，包括电源、地和信号端口

下面为一个例子，对于一个反相器电路，对应的识别格式输出为：
<think>
<Port>
VDD(Port)[[160, 31, 200, 46]]
GND(Port)[[167, 259, 196, 287]]
Vin(Port)[[63, 159, 77, 173]]
Vout(Port)[[228, 159, 242, 173]]
</Port>
<Device>
M2(PMOS)[[144, 69, 186, 114]]
M1(NMOS)[[144, 197, 186, 244]]
</Device>
<Connection>
node1: [VDD, M2.S]
node2: [M2.G, Vin, M1.G]
node3: [M2.D, Vout, M1.D]
node4: [M1.S, GND]
</Connection>
</think>

<answer>
[VDD node1 0]
[Vin node2 0]
[GND node4 0]
[Vout node3 0]
[M2 node2 node3 node1 PMOS]
[M1 node2 node3 node4 NMOS]
</answer>

请在其中添加你的思考文本，并用专业但易懂的语言回答用户的电路相关问题。"""

# =============== 通用工具函数 ===============
def warmup_model():
    """模型预热"""
    if model and processor:
        logger.info("正在预热模型...")
        try:
            dummy_inputs = processor(
                text=["Hello"],
                images=None,
                padding=True,
                return_tensors="pt",
            ).to(model.device)
            
            with torch.no_grad():
                model.generate(
                    **dummy_inputs,
                    max_new_tokens=10,
                    do_sample=False,
                    pad_token_id=processor.tokenizer.eos_token_id
                )
            
            torch.cuda.empty_cache()
            logger.info("模型预热完成")
        except Exception as e:
            logger.warning(f"模型预热失败: {e}")

def setup_memory_optimization():
    """设置内存优化"""
    if torch.cuda.is_available():
        try:
            torch.cuda.set_per_process_memory_fraction(0.9)
        except AttributeError:
            try:
                torch.cuda.memory._set_memory_fraction(0.9)
            except AttributeError:
                logger.warning("无法设置GPU内存分片，跳过此优化")
        
        torch.backends.cudnn.benchmark = True
        torch.backends.cudnn.deterministic = False
        torch.cuda.empty_cache()
        logger.info("内存优化已配置")

# =============== 模式1: vLLM推理 ===============
def load_vllm_model(model_path):
    """加载vLLM模型"""
    global vllm_engine, vllm_sampling_params
    
    if not VLLM_AVAILABLE:
        logger.error("vLLM未安装，请运行: pip install vllm>=0.8.0")
        return False
    
    if not QWEN_VL_UTILS_AVAILABLE:
        logger.error("缺少qwen-vl-utils，请运行: pip install qwen-vl-utils[decord]")
        return False
    
    try:
        gpu_count = torch.cuda.device_count()
        logger.info(f"使用vLLM模式，检测到 {gpu_count} 个GPU")
        
        tensor_parallel_size = 1
        gpu_memory_util = 0.6  # 提高GPU显存利用率
        max_model_len = 6144  # 进一步增加到16K，以支持更长的对话
        
        logger.info(f"使用平衡配置: gpu_util={gpu_memory_util}, max_len={max_model_len}")
        
        vllm_engine = LLM(
            model=model_path,
            tensor_parallel_size=tensor_parallel_size,
            gpu_memory_utilization=gpu_memory_util,
            dtype="bfloat16",
            trust_remote_code=True,
            max_model_len=max_model_len,
            enforce_eager=True,
            disable_log_stats=False,
            # 新增这些参数减少显存使用
            max_num_seqs=2,  # 限制并发序列
            enable_chunked_prefill=True,  # 启用分块预填充
        )
        
        vllm_sampling_params = SamplingParams(
            temperature=0.7,
            top_p=0.9,
            top_k=50,
            max_tokens=2048,  # 从4096降到2048
            stop_token_ids=[151643, 151645, 151646],
            repetition_penalty=1.05,
        )
        
        logger.info("vLLM VL模型加载成功! (平衡配置)")
        return True
        
    except Exception as e:
        logger.error(f"vLLM模型加载失败: {str(e)}")
        if "out of memory" in str(e).lower():
            logger.warning("vLLM显存不足，建议降低gpu_memory_utilization")
        return False

def generate_with_vllm(messages, images=None):
    """vLLM生成函数"""
    global vllm_engine, vllm_sampling_params
    
    if not vllm_engine:
        raise RuntimeError("vLLM引擎未初始化")
    
    try:
        vllm_messages = build_vllm_messages(messages, images)
        
        outputs = vllm_engine.chat(
            messages=vllm_messages,
            sampling_params=vllm_sampling_params,
            use_tqdm=False
        )
        
        if outputs and len(outputs) > 0:
            if hasattr(outputs[0], 'outputs') and len(outputs[0].outputs) > 0:
                return outputs[0].outputs[0].text.strip()
            elif hasattr(outputs[0], 'text'):
                return outputs[0].text.strip()
            else:
                return str(outputs[0]).strip()
        else:
            return "vLLM生成失败：无输出"
            
    except Exception as e:
        logger.error(f"vLLM生成出错: {str(e)}")
        return f"vLLM生成出错: {str(e)}"

def build_vllm_messages(messages, images=None):
    """构建vLLM支持的多模态消息格式"""
    vllm_messages = []
    
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        
        if role == "system":
            vllm_messages.append({"role": "system", "content": content})
        elif role == "user":
            if isinstance(content, list):
                vllm_messages.append({"role": "user", "content": content})
            else:
                user_content = []
                
                if images and len(images) > 0:
                    for image_path in images:
                        if image_path and os.path.exists(image_path):
                            import base64
                            with open(image_path, "rb") as f:
                                encoded_image = base64.b64encode(f.read()).decode("utf-8")
                            
                            user_content.append({
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{encoded_image}"
                                }
                            })
                
                if content:
                    user_content.append({
                        "type": "text", 
                        "text": content
                    })
                
                vllm_messages.append({
                    "role": "user", 
                    "content": user_content if user_content else content
                })
        elif role == "assistant":
            vllm_messages.append({"role": "assistant", "content": content})
    
    return vllm_messages

# =============== 模式2: 8bit量化推理 ===============
def load_8bit_model(model_path):
    """加载8bit量化模型"""
    global model, processor
    logger.info("使用8bit量化模式加载模型...")
    
    try:
        quantization_config = BitsAndBytesConfig(
            load_in_8bit=True,
            llm_int8_threshold=6.0,
            llm_int8_has_fp16_weight=False
        )
        
        model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            model_path,
            quantization_config=quantization_config,
            device_map=f"cuda:{GPU_ID}",
            trust_remote_code=True,
            low_cpu_mem_usage=True,
            use_cache=True
        )
        
        processor = AutoProcessor.from_pretrained(
            model_path,
            trust_remote_code=True
        )
        
        model.eval()
        warmup_model()
        
        logger.info("8bit量化模型加载成功!")
        return True
        
    except Exception as e:
        logger.error(f"8bit量化模型加载失败: {str(e)}")
        return False

# =============== 模式3: 正常16bit推理 ===============
def load_16bit_model(model_path):
    """加载正常16bit模型"""
    global model, processor
    logger.info("使用正常16bit模式加载模型...")
    
    try:
        model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            model_path,
            torch_dtype=torch.bfloat16,
            device_map=f"cuda:{GPU_ID}",
            trust_remote_code=True,
            low_cpu_mem_usage=True,
            use_cache=True
        )
        
        try:
            model = torch.compile(model, mode="reduce-overhead")
            logger.info("模型已编译优化")
        except Exception as e:
            logger.warning(f"torch.compile不可用: {e}")
        
        processor = AutoProcessor.from_pretrained(
            model_path,
            trust_remote_code=True
        )
        
        model.eval()
        warmup_model()
        
        logger.info("16bit模型加载成功!")
        return True
        
    except Exception as e:
        logger.error(f"16bit模型加载失败: {str(e)}")
        return False

# =============== 统一的模型加载接口 ===============
def load_model_by_mode(model_path, mode: InferenceMode):
    """根据模式加载模型"""
    global CURRENT_MODE
    CURRENT_MODE = mode
    
    logger.info(f"正在加载模型，模式: {mode.value}")
    
    if mode == InferenceMode.VLLM:
        return load_vllm_model(model_path)
    elif mode == InferenceMode.QUANTIZED_8BIT:
        return load_8bit_model(model_path)
    elif mode == InferenceMode.NORMAL_16BIT:
        return load_16bit_model(model_path)
    else:
        logger.error(f"未知的推理模式: {mode}")
        return False

# =============== 统一的生成接口 ===============
def stream_generate_with_local_model(user_conversation_history, user_message, uploaded_image_path=None, max_tokens=4096):
    """本地模型流式生成（用于图片处理）"""
    global CURRENT_MODE
    
    has_image = uploaded_image_path and os.path.exists(uploaded_image_path)
    system_prompt = get_system_prompt(has_image)
    
    messages = [{"role": "system", "content": system_prompt}]
    
    if user_conversation_history:
        # 如果有图像输入，进一步减少对话历史以节省token
        if uploaded_image_path and os.path.exists(uploaded_image_path):
            history_limit = 3  # 有图像时只保留最近3轮对话
        else:
            history_limit = 6  # 纯文本时保留6轮对话
            
        recent_history = user_conversation_history[-history_limit:] if len(user_conversation_history) > history_limit else user_conversation_history
        messages.extend(recent_history)
    
    messages.append({"role": "user", "content": user_message or "你好"})
    
    if CURRENT_MODE == InferenceMode.VLLM:
        try:
            image_paths = []
            if has_image:
                # 图片预处理
                preprocess_image_for_vllm(uploaded_image_path, max_size=768)  # 减小到768以节省token
                image_paths.append(uploaded_image_path)
            
            result = generate_with_vllm(messages, image_paths)
            
            # 模拟流式输出
            chunk_size = 8
            for i in range(0, len(result), chunk_size):
                chunk = result[i:i + chunk_size]
                yield chunk
                time.sleep(0.02)
                
        except Exception as e:
            error_msg = str(e)
            logger.error(f"vLLM生成出错: {error_msg}")
            
            # 如果是token长度问题，尝试降级到其他模式
            if "longer than the maximum model length" in error_msg or "max_model_len" in error_msg:
                logger.warning("检测到token长度超限，自动降级到16bit模式")
                original_mode = CURRENT_MODE
                CURRENT_MODE = InferenceMode.NORMAL_16BIT
                
                # 递归调用，使用降级模式
                try:
                    for chunk in stream_generate_with_local_model(user_conversation_history, user_message, uploaded_image_path, max_tokens):
                        yield chunk
                    return
                except Exception as fallback_error:
                    logger.error(f"降级模式也失败: {str(fallback_error)}")
                finally:
                    CURRENT_MODE = original_mode  # 恢复原始模式
            
            yield f"[vLLM生成出错: {error_msg}，请尝试减少对话历史或简化问题]"
    
    else:
        try:
            current_message_content = []
            
            if has_image:
                # 图片预处理
                processed_image = preprocess_image_for_vllm(uploaded_image_path, max_size=768)  # 减小到768
                image = processed_image.convert('RGB')
                current_message_content.append({"type": "image", "image": image})
            
            current_message_content.append({"type": "text", "text": user_message or "你好"})
            messages[-1] = {"role": "user", "content": current_message_content}
            
            text = processor.apply_chat_template(
                messages, 
                tokenize=False, 
                add_generation_prompt=True
            )
            
            images = []
            if has_image:
                processed_image = preprocess_image_for_vllm(uploaded_image_path, max_size=768)  # 减小到768
                image = processed_image.convert('RGB')
                images.append(image)
            
            inputs = processor(
                text=[text],
                images=images if images else None,
                padding=True,
                return_tensors="pt",
            ).to(model.device)
            
            with torch.no_grad():
                with torch.inference_mode():
                    streamer = TextIteratorStreamer(
                        processor.tokenizer, 
                        skip_prompt=True, 
                        skip_special_tokens=True,
                        clean_up_tokenization_spaces=False
                    )
                    
                    generation_kwargs = {
                        **inputs,
                        "max_new_tokens": max_tokens,
                        "pad_token_id": processor.tokenizer.eos_token_id,
                        "do_sample": True,
                        "temperature": 0.7,
                        "top_p": 0.9,
                        "top_k": 50,
                        "repetition_penalty": 1.05,
                        "use_cache": True,
                        "streamer": streamer
                    }
                    
                    generation_thread = Thread(target=model.generate, kwargs=generation_kwargs)
                    generation_thread.start()
                    
                    for new_text in streamer:
                        if new_text:
                            yield new_text
                    
                    generation_thread.join()
                    
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                        
        except Exception as e:
            logger.error(f"生成出错: {str(e)}")
            yield f"[生成出错: {str(e)}]"

def stream_generate_with_api(user_conversation_history, user_message):
    """使用API进行流式生成（用于正常对话）"""
    system_prompt = get_system_prompt(has_image=False)
    
    messages = [{"role": "system", "content": system_prompt}]
    
    if user_conversation_history:
        recent_history = user_conversation_history[-8:] if len(user_conversation_history) > 8 else user_conversation_history
        messages.extend(recent_history)
    
    messages.append({"role": "user", "content": user_message})
    
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "temperature": 0.7,
        "top_p": 0.9,
        "stream": True
    }
    
    logger.info("使用API进行流式对话生成")
    response = generate_with_api_retry(payload, stream=True)
    
    if not response or response.status_code != 200:
        yield "[API请求失败，请稍后重试]"
        return
    
    try:
        for line in response.iter_lines():
            if line:
                line = line.decode('utf-8')
                
                if line.startswith("data: "):
                    line = line[6:]
                    
                    if line == "[DONE]":
                        break
                    
                    try:
                        chunk_data = json.loads(line)
                        delta = chunk_data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        
                        if delta:
                            yield delta
                    except json.JSONDecodeError:
                        continue
    except Exception as e:
        logger.error(f"API流式响应处理异常: {str(e)}")
        yield f"[API流式响应处理异常: {str(e)}]"

# =============== Flask应用配置 ===============
app = Flask(__name__)
CORS(app)

# =============== 路由定义 ===============

@app.route('/files/<user_id>/<conversation_id>/<filename>')
def serve_uploaded_file(user_id, conversation_id, filename):
    """统一的文件访问接口"""
    try:
        safe_user_id = secure_filename(user_id)
        safe_conversation_id = secure_filename(conversation_id)
        safe_filename = secure_filename(filename)
        
        file_dir = os.path.join(UPLOAD_BASE_DIR, safe_user_id, safe_conversation_id)
        file_path = os.path.join(file_dir, safe_filename)
        
        if os.path.exists(file_path):
            response = send_from_directory(file_dir, safe_filename)
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
            return response
        
        logger.error(f"文件不存在: {file_path}")
        return jsonify({"error": "文件不存在"}), 404
        
    except Exception as e:
        logger.error(f"文件服务错误: {str(e)}")
        return jsonify({"error": "服务器内部错误"}), 500

@app.route('/stream_generate', methods=['POST'])
def stream_generate_response():
    try:
        data = json.loads(request.data)
        user_message = data.get('message', '')
        user_id = data.get('user_id', 'default')
        conversation_id = data.get('conversation_id', user_id)
        
        if not conversation_id or conversation_id.strip() == '':
            logger.error("无效的请求标识符")
            return jsonify({"error": "无效的请求标识符"}), 400
        
        if not ensure_session_exists(conversation_id, user_id, user_message):
            return jsonify({"error": "创建会话失败"}), 500
        
        # =============== 修复：改进历史消息获取逻辑 ===============
        messages_history = []
        try:
            msgs = proxy_to_original_backend(f"message/list-by-session?sessionId={conversation_id}")
            
            if msgs:
                logger.info(f"从数据库获取到 {len(msgs)} 条原始消息")
                
                # 修复1: 放宽过滤条件，支持多种modelId
                # 不仅仅获取modelId==5，也获取modelId==0或空的消息
                circuit_msgs = [msg for msg in msgs if msg.get('modelId') in [0, 5, None]]
                
                logger.info(f"过滤后得到 {len(circuit_msgs)} 条CircuitThink相关消息")
                
                for msg in circuit_msgs:
                    user_type = msg.get('userType')
                    # 🔧 修复：兼容多种userType格式
                    if user_type in [MESSAGE_TYPE.USER, 'user', '1', 1]:
                        messages_history.append({"role": "user", "content": msg.get('content', '')})
                    elif user_type in [MESSAGE_TYPE.BOT, 'bot', '0', 0]:
                        messages_history.append({"role": "assistant", "content": msg.get('content', '')})
                    else:
                        logger.warning(f"未识别的userType: {user_type} (类型: {type(user_type)})")
                
                # 保持最近8轮对话
                if len(messages_history) > 16:  # 8轮对话 = 16条消息
                    messages_history = messages_history[-16:]
                    
                logger.info(f"最终加载了 {len(messages_history)} 条历史消息")
                
                # 调试日志：显示最近几条消息的内容摘要
                if messages_history:
                    recent_summary = []
                    for i, msg in enumerate(messages_history[-4:]):  # 显示最近4条
                        content_preview = msg['content'][:50] + '...' if len(msg['content']) > 50 else msg['content']
                        recent_summary.append(f"{msg['role']}: {content_preview}")
                    logger.info(f"最近消息摘要: {recent_summary}")
            else:
                logger.warning(f"从原始后端获取会话 {conversation_id} 的消息失败或为空")
        except Exception as e:
            logger.error(f"获取历史消息时出错: {str(e)}")

        # =============== 修复图片分析判断逻辑 =============== 
        uploaded_image_path = get_latest_uploaded_image(user_id, conversation_id)
        has_image = uploaded_image_path and os.path.exists(uploaded_image_path)
        
        if uploaded_image_path:
            logger.info(f"找到上传的图片: {uploaded_image_path}")
        else:
            logger.info(f"未找到上传的图片 (用户: {user_id}, 对话: {conversation_id})")
        
        # 检查是否刚刚上传了图片
        # 通过检查图片的修改时间来判断是否是刚上传的
        is_fresh_upload = False
        if has_image:
            try:
                # 获取图片文件的创建时间
                file_mtime = os.path.getmtime(uploaded_image_path)
                current_time = time.time()
                # 如果图片是在最近30秒内创建的，认为是刚上传的（增加时间窗口）
                time_diff = current_time - file_mtime
                is_fresh_upload = time_diff < 30  # 30秒内的上传认为是新上传
                
                logger.info(f"图片时间检查: file_time={file_mtime}, current_time={current_time}, diff={time_diff:.1f}s, is_fresh={is_fresh_upload}")
            except Exception as e:
                logger.error(f"检查图片时间失败: {e}")
                is_fresh_upload = False
        
        # 只有刚上传的图片才使用vLLM分析，其他情况一律使用API
        is_image_analysis = is_fresh_upload
        
        logger.info(f"最终判断: has_image={has_image}, is_fresh_upload={is_fresh_upload}, use_vllm={is_image_analysis}")
        
        def generate():
            request_id = f"{conversation_id}_{str(time.time())}"
            connection_id = f"circuit_{user_id}_{conversation_id}_{int(time.time())}"
            inference_type = "local_model" if is_image_analysis else "api"
            logger.info(f"开始流式输出,请求ID: {request_id}, 连接ID: {connection_id}, 推理类型: {inference_type}, 用户消息: {user_message[:20]}...")
            logger.info(f"传入生成函数的历史消息数量: {len(messages_history)}")
            
            # 注册连接
            register_connection(connection_id, user_id, conversation_id)
            
            aborted_streams = getattr(app, 'aborted_streams', set())
            if not hasattr(app, 'aborted_streams'):
                app.aborted_streams = set()
            
            start_chunk = {
                "chunk": "",
                "is_complete": False,
                "start_streaming": True,
                "request_id": request_id,
                "connection_id": connection_id,
                "conversation_id": conversation_id,
                "inference_type": inference_type
            }
            
            yield f"data: {json.dumps(start_chunk, ensure_ascii=False)}\n\n"
            
            try:
                chunk_count = 0
                full_response = ""  # 新增：记录完整响应
                
                if is_image_analysis:
                    logger.info("检测到图片分析请求，使用huggingface200本地模型 - 不使用历史上下文")
                    generation_stream = stream_generate_with_local_model(
                        None, user_message, uploaded_image_path  # 传递None作为历史，只使用图片
                    )
                else:
                    logger.info("正常对话请求，使用API进行对话")
                    generation_stream = stream_generate_with_api(
                        messages_history, user_message
                    )
                
                for chunk in generation_stream:
                    if request_id in app.aborted_streams:
                        logger.info(f"请求 {request_id} 已被中止")
                        app.aborted_streams.discard(request_id)
                        # 清理时间戳记录
                        if hasattr(app, 'aborted_timestamps'):
                            app.aborted_timestamps.pop(request_id, None)
                        abort_complete = {
                            "chunk": "\n\n[回答已中止]",
                            "is_complete": True,
                            "aborted": True,
                            "conversation_id": conversation_id
                        }
                        yield f"data: {json.dumps(abort_complete)}\n\n"
                        return
                    
                    if chunk:
                        full_response += chunk  # 累积完整响应
                        chunk_count += 1
                        logger.debug(f"发送chunk {chunk_count}: {repr(chunk)}")
                        
                        data = json.dumps({
                            "chunk": chunk,
                            "is_complete": False,
                            "conversation_id": conversation_id
                        }, ensure_ascii=False)
                        yield f"data: {data}\n\n"
                
                # ✅ 参考TCAD和Chatbot的做法：后端只负责流式输出，不自动保存消息
                # 消息保存由前端统一处理，保持架构一致性
                
                yield f"data: {json.dumps({'is_complete': True, 'conversation_id': conversation_id})}\n\n"
                logger.info(f"响应生成完成，总共发送了 {chunk_count} 个chunks，推理类型: {inference_type}")
                logger.info(f"完整响应长度: {len(full_response)} 字符")
                
            except GeneratorExit:
                logger.info(f"客户端断开连接,请求ID: {request_id}")
                app.aborted_streams.discard(request_id)
                # 同时清理时间戳记录
                if hasattr(app, 'aborted_timestamps'):
                    app.aborted_timestamps.pop(request_id, None)
                # 移除连接
                remove_connection(connection_id)
                
            except Exception as e:
                logger.error(f"流式响应处理异常:{str(e)}")
                error_data = json.dumps({
                    "error": str(e),
                    "is_complete": True,
                    "conversation_id": conversation_id
                })
                yield f"data: {error_data}\n\n"
        
        response = Response(generate(), mimetype='text/event-stream')
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['Connection'] = 'keep-alive'
        response.headers['X-Accel-Buffering'] = 'no'
        return response
        
    except Exception as e:
        logger.error(f"API调用异常:{str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/uploadFile', methods=['POST'])
def upload_image():
    """文件上传接口"""
    try:
        file = request.files['file']
        conversation_id = request.form.get('conversation_id', '')
        user_id = request.form.get('user_id', 'anonymous')
        
        file_type = check_file_type_by_suffix(file.filename)
        
        # 只允许支持的图片格式
        if file_type in ["png", "jpg", "jpeg"]:
            if conversation_id:  # CircuitThink上传
                timestamp = int(time.time())
                unique_id = str(uuid.uuid4())[:8]
                file_extension = os.path.splitext(file.filename)[1]
                actual_filename = f"circuit_{timestamp}_{unique_id}{file_extension}"
                
                # 保存文件 - 注意这里返回的是实际保存的文件名
                file_path, saved_filename = save_uploaded_file(file.read(), user_id, conversation_id, actual_filename)
                
                # 保存文件信息 - 使用实际保存的文件名
                if user_id not in user_uploaded_files:
                    user_uploaded_files[user_id] = {}
                if conversation_id not in user_uploaded_files[user_id]:
                    user_uploaded_files[user_id][conversation_id] = {}
                
                user_uploaded_files[user_id][conversation_id][saved_filename] = {
                    'original_name': file.filename,
                    'saved_path': file_path,
                    'type': file.content_type or file.mimetype or 'image/png',
                    'upload_time': time.time(),
                    'conversation_id': conversation_id
                }
                
                # 构建访问URL - 使用实际保存的文件名
                image_url = f"{BACKEND_URL}/files/{secure_filename(user_id)}/{secure_filename(conversation_id)}/{saved_filename}"
                
                html_content = f'<img src="{image_url}" alt="{file.filename}" style="max-width: 400px; max-height: 400px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">'
                
                logger.info(f"生成的图片URL: {image_url}")
                logger.info(f"实际保存的文件名: {saved_filename}")
                
                return jsonify({"role": "assistant", "content": html_content}), 200
        else:
            return jsonify({'error': '只支持PNG、JPG、JPEG格式的图片文件'}), 400
        
        return jsonify({'message': 'Upload Success'}), 200

    except Exception as e:
        logger.error(f'上传错误: {e}')
        return jsonify({'error': 'Internal Server Error', 'details': str(e)}), 500

@app.route('/abort_stream', methods=['POST'])
def abort_stream():
    """处理中止流式输出的请求"""
    try:
        data = request.json
        request_id = data.get('request_id')
        
        if not request_id:
            return jsonify({'error': '缺少请求ID'}), 400
        
        if not hasattr(app, 'aborted_streams'):
            app.aborted_streams = set()
        
        # 添加时间戳记录，用于自动清理
        if not hasattr(app, 'aborted_timestamps'):
            app.aborted_timestamps = {}
        
        app.aborted_streams.add(request_id)
        app.aborted_timestamps[request_id] = time.time()
        
        return jsonify({
            'message': f"已标记请求 {request_id} 为中止状态",
            'success': True
        }), 200
    except Exception as e:
        logger.error(f'中止流式输出时出错: {e}')
        return jsonify({'error': '中止失败', 'success': False}), 500

def cleanup_expired_aborts():
    """定期清理过期的中止请求，防止内存泄漏"""
    while True:
        try:
            time.sleep(ABORT_CLEANUP_INTERVAL)
            
            if not hasattr(app, 'aborted_streams') or not hasattr(app, 'aborted_timestamps'):
                continue
                
            current_time = time.time()
            expired_requests = []
            
            # 找出过期的请求
            for request_id, timestamp in list(app.aborted_timestamps.items()):
                if current_time - timestamp > ABORT_TIMEOUT:
                    expired_requests.append(request_id)
            
            # 清理过期的请求
            for request_id in expired_requests:
                app.aborted_streams.discard(request_id)
                app.aborted_timestamps.pop(request_id, None)
            
            if expired_requests:
                logger.info(f"清理了 {len(expired_requests)} 个过期的中止请求")
                
        except Exception as e:
            logger.error(f"清理过期中止请求时出错: {e}")

# 启动清理线程
import threading
cleanup_thread = threading.Thread(target=cleanup_expired_aborts, daemon=True)
cleanup_thread.start()

# =============== 连接管理和心跳检测 ===============
# 活跃连接管理
active_connections = {}  # {connection_id: {'last_seen': timestamp, 'user_id': str, 'conversation_id': str}}
connections_lock = threading.RLock()

def register_connection(connection_id, user_id, conversation_id):
    """注册新的活跃连接"""
    with connections_lock:
        active_connections[connection_id] = {
            'last_seen': time.time(),
            'user_id': user_id,
            'conversation_id': conversation_id,
            'status': 'active'
        }
        logger.info(f"注册新连接: {connection_id} (用户: {user_id})")

def update_connection_heartbeat(connection_id):
    """更新连接的心跳时间"""
    with connections_lock:
        if connection_id in active_connections:
            active_connections[connection_id]['last_seen'] = time.time()
            return True
    return False

def remove_connection(connection_id):
    """移除连接"""
    with connections_lock:
        if connection_id in active_connections:
            conn_info = active_connections.pop(connection_id)
            logger.info(f"移除连接: {connection_id} (用户: {conn_info.get('user_id', 'unknown')})")

def check_connection_health():
    """定期检查连接健康状态"""
    while True:
        try:
            time.sleep(HEARTBEAT_INTERVAL)
            current_time = time.time()
            expired_connections = []
            
            with connections_lock:
                for conn_id, conn_info in list(active_connections.items()):
                    if current_time - conn_info['last_seen'] > CONNECTION_TIMEOUT:
                        expired_connections.append(conn_id)
            
            # 清理过期连接
            for conn_id in expired_connections:
                remove_connection(conn_id)
                logger.warning(f"连接 {conn_id} 超时，已自动清理")
            
            if active_connections:
                logger.debug(f"当前活跃连接数: {len(active_connections)}")
                
        except Exception as e:
            logger.error(f"检查连接健康状态时出错: {e}")

# 启动连接健康检查线程
health_check_thread = threading.Thread(target=check_connection_health, daemon=True)
health_check_thread.start()

@app.route('/heartbeat', methods=['POST'])
def heartbeat():
    """心跳接口，客户端定期调用以维持连接状态"""
    try:
        data = request.json or {}
        connection_id = data.get('connection_id')
        user_id = data.get('user_id', 'anonymous')
        conversation_id = data.get('conversation_id', 'default')
        
        if not connection_id:
            return jsonify({'error': '缺少连接ID'}), 400
        
        # 更新心跳或注册新连接
        if not update_connection_heartbeat(connection_id):
            register_connection(connection_id, user_id, conversation_id)
        
        return jsonify({
            'status': 'ok',
            'server_time': time.time(),
            'connection_id': connection_id,
            'active_connections': len(active_connections)
        }), 200
        
    except Exception as e:
        logger.error(f'心跳检测出错: {e}')
        return jsonify({'error': '心跳检测失败'}), 500

@app.route('/connections', methods=['GET'])
def get_active_connections():
    """获取当前活跃连接信息"""
    try:
        with connections_lock:
            connections_info = {}
            current_time = time.time()
            
            for conn_id, conn_info in active_connections.items():
                connections_info[conn_id] = {
                    'user_id': conn_info['user_id'],
                    'conversation_id': conn_info['conversation_id'],
                    'last_seen_ago': current_time - conn_info['last_seen'],
                    'status': conn_info['status']
                }
        
        return jsonify({
            'total_connections': len(connections_info),
            'connections': connections_info
        }), 200
        
    except Exception as e:
        logger.error(f'获取连接信息出错: {e}')
        return jsonify({'error': '获取连接信息失败'}), 500

@app.route('/add_message', methods=['POST'])
def add_message():
    try:
        message_data = request.json
        
        required_fields = ['content', 'sessionId', 'userType', 'userId']
        missing_fields = [field for field in required_fields if field not in message_data]
        
        if missing_fields:
            return jsonify({'error': f"消息缺少必要字段: {missing_fields}"}), 400
        
        session_id = message_data.get('sessionId')
        
        if not ensure_session_exists(session_id, message_data.get('userId', 'default')):
            return jsonify({"error": "无法创建会话"}), 500
        
        # 修复3: 智能设置modelId
        if 'modelId' not in message_data:
            # 根据消息类型和内容智能设置modelId
            if message_data.get('userType') == MESSAGE_TYPE.USER:
                # 用户消息：检查是否为图片分析请求
                content = message_data.get('content', '')
                if any(keyword in content for keyword in ['分析', '识别', '网表', '电路图']):
                    message_data['modelId'] = 5  # CircuitThink图片分析
                else:
                    message_data['modelId'] = 0  # 普通对话
            else:
                # 助手消息：保持一致性
                message_data['modelId'] = 5  # CircuitThink专用
        
        if 'messageId' not in message_data or not isinstance(message_data.get('messageId'), int):
            try:
                existing_id = message_data.get('messageId')
                if isinstance(existing_id, (int, float)) and 0 < existing_id <= 2147483647:
                    message_data['messageId'] = int(existing_id)
                else:
                    message_data['messageId'] = generate_safe_message_id()
            except (ValueError, TypeError):
                message_data['messageId'] = generate_safe_message_id()
        
        if 'timestamp' not in message_data or not message_data['timestamp']:
            message_data['timestamp'] = datetime.now().isoformat()
        
        result = proxy_to_original_backend("message/add", method="POST", data=message_data)
        
        if result:
            logger.info(f"成功添加消息，ID: {message_data['messageId']}, modelId: {message_data.get('modelId')}, 类型: {message_data.get('userType')}")
            return jsonify({'success': True, 'message_id': message_data['messageId']}), 201
        else:
            return jsonify({'error': "插入消息失败"}), 500
        
    except Exception as e:
        logger.error(f"添加消息时出错: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/update_session', methods=['POST'])
def update_session():
    """更新会话信息"""
    try:
        session_data = request.json
        
        if 'sessionId' not in session_data:
            return jsonify({'error': "缺少sessionId字段"}), 400
        
        session_id = session_data['sessionId']
        
        if not isinstance(session_id, str) or len(session_id) < 5:
            return jsonify({'error': "无效的sessionId格式"}), 400
        
        session_data['modelId'] = 5  # CircuitThink专用
        
        existing_session = proxy_to_original_backend(f"session/get?sessionId={session_id}")
        
        if not existing_session:
            session_data['createTime'] = session_data.get('createTime', datetime.now().isoformat())
            result = proxy_to_original_backend("session/add", method="POST", data=session_data)
        else:
            session_data['lastActive'] = datetime.now().isoformat()
            result = proxy_to_original_backend("session/update", method="POST", data=session_data)
        
        if result:
            return jsonify({'success': True, 'session_id': session_id}), 200
        else:
            return jsonify({'error': '更新会话失败'}), 500
    
    except Exception as e:
        logger.error(f"更新会话时出错: {str(e)}")
        return jsonify({'error': str(e), 'errorType': type(e).__name__}), 500

@app.route('/switch_mode', methods=['POST'])
def switch_inference_mode():
    """切换推理模式的接口"""
    try:
        data = request.json
        mode_str = data.get('mode', '').lower()
        
        mode_map = {
            'vllm': InferenceMode.VLLM,
            '8bit': InferenceMode.QUANTIZED_8BIT,
            '16bit': InferenceMode.NORMAL_16BIT
        }
        
        if mode_str not in mode_map:
            return jsonify({
                'error': f'无效的模式: {mode_str}',
                'available_modes': list(mode_map.keys())
            }), 400
        
        new_mode = mode_map[mode_str]
        success = load_model_by_mode(MODEL_PATH, new_mode)
        
        if success:
            return jsonify({
                'success': True,
                'current_mode': new_mode.value,
                'message': f'已切换到 {new_mode.value} 模式'
            }), 200
        else:
            return jsonify({
                'success': False,
                'error': f'切换到 {new_mode.value} 模式失败'
            }), 500
            
    except Exception as e:
        logger.error(f"切换模式时出错: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/current_mode', methods=['GET'])
def get_current_mode():
    """获取当前推理模式"""
    return jsonify({
        'current_mode': CURRENT_MODE.value,
        'available_modes': ['vllm', '8bit', '16bit'],
        'api_model': MODEL_NAME
    })

@app.route('/health', methods=['GET'])
def health_check():
    """健康检查接口"""
    return jsonify({
        'status': 'healthy',
        'service': 'CircuitThink',
        'current_mode': CURRENT_MODE.value,
        'model_path': MODEL_PATH,
        'api_model': MODEL_NAME,
        'upload_base_dir': UPLOAD_BASE_DIR,
        'backend_url': BACKEND_URL,
        'supported_formats': ['png', 'jpg', 'jpeg'],
        'timestamp': datetime.now().isoformat()
    }), 200

# =============== 主函数 ===============
if __name__ == '__main__':
    if torch.cuda.is_available():
        try:
            # 因为设置了CUDA_VISIBLE_DEVICES，现在只能看到一个GPU，索引为0
            torch.cuda.set_device(0)
            print(f"成功设置CUDA设备: {torch.cuda.get_device_name(0)}")
        except Exception as e:
            print(f"设置CUDA设备失败: {e}")

    if DESIRED_MODE == InferenceMode.VLLM and not QWEN_VL_UTILS_AVAILABLE:
        logger.error("vLLM模式需要安装qwen-vl-utils")
        logger.error("请运行: pip install qwen-vl-utils[decord]")
        logger.error("然后重新启动服务")
        exit(1)
    
    setup_memory_optimization()
    
    success = False
    attempted_modes = []
    
    logger.info(f"尝试加载用户指定的模式: {DESIRED_MODE.value}")
    success = load_model_by_mode(MODEL_PATH, DESIRED_MODE)
    attempted_modes.append(DESIRED_MODE.value)
    
    if not success:
        logger.warning(f"用户指定的模式 {DESIRED_MODE.value} 加载失败，尝试回退模式...")
        
        for fallback_mode in FALLBACK_MODES:
            if fallback_mode != DESIRED_MODE:
                logger.info(f"尝试回退模式: {fallback_mode.value}")
                success = load_model_by_mode(MODEL_PATH, fallback_mode)
                attempted_modes.append(fallback_mode.value)
                if success:
                    logger.info(f"成功使用回退模式: {fallback_mode.value}")
                    break
    
    if not success:
        logger.error(f"所有模式都加载失败，已尝试: {attempted_modes}")
        logger.error("请检查模型路径和依赖安装")
        exit(1)
    
    logger.info("=" * 60)
    logger.info("CircuitThink混合推理服务已启动")
    logger.info(f"本地模型推理模式: {CURRENT_MODE.value}")
    logger.info(f"API模型: {MODEL_NAME}")
    logger.info("图片处理: 使用本地huggingface200模型")
    logger.info("文本对话: 使用API流式输出")
    logger.info(f"运行于 http://10.98.193.46:{SERVICE_PORT} - GPU {GPU_ID}")
    logger.info("支持格式: PNG, JPG, JPEG")
    logger.info("并行支持: 多用户并发电路分析")
    logger.info("=" * 60)
    
    print("✅ Circuit 服务启动完成")
    print(f"📍 服务地址: http://10.98.193.46:{SERVICE_PORT} - GPU {GPU_ID}")
    print("📋 主要端点: /stream_generate")
    print("🔗 状态检查: http://10.98.193.46:5007/health")
    print("📷 支持格式: PNG, JPG, JPEG")
    
    app.run(debug=False, host='0.0.0.0', port=SERVICE_PORT, threaded=True)