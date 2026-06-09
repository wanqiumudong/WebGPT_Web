from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS
import requests
from datetime import datetime
import json
import time
import logging
import os
import uuid
import base64
from pathlib import Path
from enum import Enum
from urllib.parse import quote
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- 历史本地模型兼容导入（当前正式运行为 API-only） ---
import torch
from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor, BitsAndBytesConfig
from transformers.generation.configuration_utils import GenerationConfig
from PIL import Image
import warnings
from werkzeug.utils import secure_filename
from transformers import TextIteratorStreamer
from threading import Thread

warnings.filterwarnings("ignore")


def ensure_qwen25_vl_generation_config_patch():
    """兼容当前 transformers 版本下 Qwen2.5-VL text_config 为 dict 的情况。"""
    if getattr(GenerationConfig, "_web_fabgpt_qwen25_vl_patch", False):
        return

    original_from_model_config = GenerationConfig.from_model_config.__func__

    def patched_from_model_config(cls, model_config):
        config_dict = model_config.to_dict()
        config_dict.pop("_from_model_config", None)
        config_dict = {key: value for key, value in config_dict.items() if value is not None}

        generation_config = cls.from_dict(config_dict, return_unused_kwargs=False, _from_model_config=True)

        decoder_config = model_config.get_text_config(decoder=True)
        if decoder_config is not model_config:
            default_generation_config = GenerationConfig()
            decoder_config_dict = (
                decoder_config if isinstance(decoder_config, dict) else decoder_config.to_dict()
            )
            for attr in generation_config.to_dict().keys():
                is_unset = getattr(generation_config, attr) == getattr(default_generation_config, attr)
                if attr in decoder_config_dict and is_unset:
                    setattr(generation_config, attr, decoder_config_dict[attr])

        if generation_config.return_dict_in_generate is False:
            if any(
                getattr(generation_config, extra_output_flag, False)
                for extra_output_flag in generation_config.extra_output_flags
            ):
                generation_config.return_dict_in_generate = True

        generation_config._original_object_hash = hash(generation_config)
        return generation_config

    GenerationConfig.from_model_config = classmethod(patched_from_model_config)
    GenerationConfig._web_fabgpt_qwen25_vl_patch = True
    GenerationConfig._web_fabgpt_qwen25_vl_patch_original = original_from_model_config

# 确保本地服务调用不经过系统代理（避免127.0.0.1:11888等代理干扰）
DEFAULT_SERVICE_HOST = os.environ.get("WEB_FABGPT_HOST", "10.98.193.46")
_local_no_proxy = f"127.0.0.1,localhost,{DEFAULT_SERVICE_HOST}"
os.environ["NO_PROXY"] = ",".join(
    filter(None, [os.environ.get("NO_PROXY", ""), _local_no_proxy])
)
os.environ["no_proxy"] = os.environ["NO_PROXY"]

# =============== 历史推理模式配置（保留兼容，不作为正式运行主链路） ===============
class InferenceMode(Enum):
    VLLM = "vllm"           # vLLM高性能推理
    QUANTIZED_8BIT = "8bit"  # 8bit量化
    NORMAL_16BIT = "16bit"   # 正常16bit推理

MODE_NAME_TO_ENUM = {
    "vllm": InferenceMode.VLLM,
    "8bit": InferenceMode.QUANTIZED_8BIT,
    "16bit": InferenceMode.NORMAL_16BIT,
}
DESIRED_MODE = MODE_NAME_TO_ENUM.get(
    os.environ.get("WEB_FABGPT_CIRCUIT_RUN_MODE", "16bit").lower(),
    InferenceMode.NORMAL_16BIT,
)

# 优先级顺序：如果首选模式失败，按此顺序尝试其他模式
FALLBACK_MODES = [
    InferenceMode.QUANTIZED_8BIT,
    InferenceMode.NORMAL_16BIT,
    InferenceMode.VLLM
]

# =============== 配置常量 ===============
CURRENT_DIR = Path(__file__).resolve().parent
SERVICE_HOST = DEFAULT_SERVICE_HOST
BACKEND_PORT = int(os.environ.get("WEB_FABGPT_BACKEND_PORT", "5107"))
CIRCUIT_PORT = int(os.environ.get("WEB_FABGPT_CIRCUIT_PORT", "5105"))
ORIGINAL_API_URL = f"http://{SERVICE_HOST}:{BACKEND_PORT}"
BACKEND_URL = f"http://{SERVICE_HOST}:{CIRCUIT_PORT}"

# API配置（OpenAI兼容，默认对齐 TCAD_Agent 的 ohmygpt + Gemini Flash Lite）
DEFAULT_OHMYGPT_BASE_URL = "https://api.ohmygpt.com/v1/chat/completions"
DEFAULT_API_MODEL = "gemini-3.1-flash-lite-preview"

API_URL = os.environ.get(
    "WEB_FABGPT_CIRCUIT_API_URL",
    os.environ.get(
        "WEB_FABGPT_VL_API_BASE_URL",
        os.environ.get("WEB_FABGPT_LLM_API_URL", DEFAULT_OHMYGPT_BASE_URL),
    ),
)
API_KEY = os.environ.get(
    "WEB_FABGPT_CIRCUIT_API_KEY",
    os.environ.get(
        "WEB_FABGPT_SILICONFLOW_API_KEY",
        os.environ.get("WEB_FABGPT_LLM_API_KEY", ""),
    ),
)
MODEL_NAME = os.environ.get(
    "WEB_FABGPT_CIRCUIT_MODEL",
    os.environ.get("WEB_FABGPT_VL_MODEL", DEFAULT_API_MODEL),
)
CURRENT_PROVIDER = os.environ.get("WEB_FABGPT_CIRCUIT_PROVIDER", "local").lower()
MODEL_PATH = os.environ.get(
    "WEB_FABGPT_CIRCUIT_MODEL_PATH",
    str(CURRENT_DIR / "local_models" / "global_step_700_actor" / "huggingface"),
)
CIRCUIT_IMAGE_FIXED_PROMPT = os.environ.get(
    "WEB_FABGPT_CIRCUIT_IMAGE_FIXED_PROMPT",
    "请分析这张电路图",
)
CIRCUIT_MAX_NEW_TOKENS = int(
    os.environ.get("WEB_FABGPT_CIRCUIT_MAX_NEW_TOKENS", "16384")
)
HYBRID_IMAGE_LOCAL_TEXT_API = os.environ.get(
    "WEB_FABGPT_CIRCUIT_HYBRID_ROUTING", "1"
).lower() in {"1", "true", "yes", "on"}


def resolve_local_model_path(path_str):
    """允许传 actor 根目录，自动解析到 huggingface 导出目录。"""
    candidate = Path(path_str).expanduser().resolve()
    config_path = candidate / "config.json"
    if config_path.exists():
        return str(candidate)

    hf_candidate = candidate / "huggingface"
    hf_config_path = hf_candidate / "config.json"
    if hf_config_path.exists():
        logger.info(f"检测到actor根目录，自动切换模型目录到: {hf_candidate}")
        return str(hf_candidate)

    return str(candidate)

# API请求头配置
api_headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}"
}

local_session = requests.Session()
local_session.trust_env = False

external_api_session = requests.Session()
external_api_session.trust_env = True

shared_adapter = HTTPAdapter(
    pool_connections=20,
    pool_maxsize=40,
    max_retries=Retry(total=2, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504]),
)
local_session.mount("https://", shared_adapter)
local_session.mount("http://", shared_adapter)
external_api_session.mount("https://", shared_adapter)
external_api_session.mount("http://", shared_adapter)

# 统一的文件存储配置
UPLOAD_BASE_DIR = str(CURRENT_DIR / "files")  # 统一存储结构: files/{user_id}/{conversation_id}/

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
# 记录“本轮待消费的上传图片”，只在首次图像分析请求中走本地模型。
pending_uploaded_images = {}
# 会话级兜底缓存，避免 5107 历史接口异常时丢失上下文。
conversation_message_cache = {}

# =============== 日志配置 ===============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("circuitthink.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("CircuitThink-Service")
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
def check_file_type_by_suffix(filename):
    """根据文件后缀检查文件类型"""
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
        
        if method.upper() == "GET":
            response = local_session.get(url, params=params, headers=default_headers, timeout=10)
        elif method.upper() == "POST":
            response = local_session.post(url, json=data, headers=default_headers, timeout=10)
        else:
            logger.error(f"不支持的HTTP方法: {method}")
            return None
            
        if 200 <= response.status_code < 300:
            try:
                return response.json()
            except ValueError:
                return response.text
        else:
            logger.error(f"原始后端返回错误: {response.status_code}, URL: {url}")
            return None
    except Exception as e:
        logger.error(f"代理到原始后端时出错: {str(e)}, URL: {original_endpoint}")
        return None

def normalize_user_id(user_id):
    """兼容字符串用户ID，确保写入后端时为整数。
    传入用户名时，尝试从8080后端解析到真实userId。
    """
    try:
        return int(user_id)
    except (TypeError, ValueError):
        pass

    if isinstance(user_id, str):
        username = user_id.strip()
        if username:
            try:
                user_info = proxy_to_original_backend(
                    f"user/get-by-name?username={quote(username)}"
                )
                if isinstance(user_info, dict):
                    for key in ("userId", "user_id", "id"):
                        if key in user_info:
                            try:
                                return int(user_info[key])
                            except (TypeError, ValueError):
                                continue
            except Exception:
                pass

    return 1


MAX_BACKEND_MESSAGE_ID = 2_000_000_000
_last_backend_message_id = 0


def normalize_backend_message_id(message_id=None):
    """后端 messageId 只能是 32 位 int，避免直接传毫秒时间戳导致 500。"""
    global _last_backend_message_id

    try:
        candidate = int(message_id)
    except (TypeError, ValueError):
        candidate = 0

    if 0 < candidate <= 2147483647:
        _last_backend_message_id = max(_last_backend_message_id, candidate)
        return candidate

    candidate = int(time.time() * 1000) % MAX_BACKEND_MESSAGE_ID
    if candidate <= 0:
        candidate = 1
    if candidate <= _last_backend_message_id:
        candidate = _last_backend_message_id + 1
    if candidate > 2147483647:
        candidate = candidate % MAX_BACKEND_MESSAGE_ID
        if candidate <= 0:
            candidate = 1

    _last_backend_message_id = candidate
    return candidate

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
            "userId": normalize_user_id(user_id)
        }
        
        result = proxy_to_original_backend("session/add", method="POST", data=new_session)
        if result is None:
            logger.error(f"创建新会话 {session_id} 失败")
            return False
        logger.info(f"已创建新CircuitThink会话: {session_id}")
    return True

class MESSAGE_TYPE:
    USER = 'user'
    BOT = 'bot'

# =============== API相关函数 ===============
def generate_with_api_retry(payload, max_retries=3, stream=False):
    """带重试机制的API调用，支持流式响应"""
    for attempt in range(max_retries):
        try:
            response = external_api_session.post(API_URL, headers=api_headers, json=payload, stream=stream, timeout=120)
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

def get_latest_uploaded_image(user_id, conversation_id, include_url=False):
    """获取指定用户对话中最新上传的图像路径（可选返回URL）"""
    if user_id not in user_uploaded_files:
        return (None, None) if include_url else None
    
    if conversation_id not in user_uploaded_files[user_id] or not user_uploaded_files[user_id][conversation_id]:
        return (None, None) if include_url else None
    
    files = user_uploaded_files[user_id][conversation_id]
    image_files = []
    for saved_filename, file_info in files.items():
        if file_info.get('type', '').startswith('image/'):
            image_files.append((saved_filename, file_info))

    if not image_files:
        return (None, None) if include_url else None
    
    latest_filename, latest_file = max(image_files, key=lambda x: x[1].get('upload_time', 0))
    file_path = latest_file.get('saved_path')
    
    if not (file_path and os.path.exists(file_path)):
        return (None, None) if include_url else None

    if include_url:
        image_url = f"{BACKEND_URL}/files/{secure_filename(user_id)}/{secure_filename(conversation_id)}/{secure_filename(latest_filename)}"
        return file_path, image_url

    return file_path


def mark_pending_uploaded_image(user_id, conversation_id, file_path, image_url):
    pending_uploaded_images[(str(user_id), str(conversation_id))] = {
        "file_path": file_path,
        "image_url": image_url,
        "created_at": time.time(),
    }


def pop_pending_uploaded_image(user_id, conversation_id):
    return pending_uploaded_images.pop((str(user_id), str(conversation_id)), None)


def peek_pending_uploaded_image(user_id, conversation_id):
    return pending_uploaded_images.get((str(user_id), str(conversation_id)))


def get_cached_messages(user_id, conversation_id):
    return list(conversation_message_cache.get((str(user_id), str(conversation_id)), []))


def append_cached_exchange(user_id, conversation_id, user_message, assistant_message):
    key = (str(user_id), str(conversation_id))
    history = list(conversation_message_cache.get(key, []))
    if user_message:
        history.append({"role": "user", "content": user_message})
    if assistant_message:
        history.append({"role": "assistant", "content": assistant_message})
    if len(history) > 24:
        history = history[-24:]
    conversation_message_cache[key] = history


def persist_assistant_message(user_id, conversation_id, assistant_message):
    content = str(assistant_message or "").strip()
    if not content:
        return False

    payload = {
        "content": content,
        "messageId": normalize_backend_message_id(),
        "sessionId": conversation_id,
        "timestamp": datetime.now().isoformat(),
        "userId": normalize_user_id(user_id),
        "userType": MESSAGE_TYPE.BOT,
        "modelId": 5,
    }
    result = proxy_to_original_backend("message/add", method="POST", data=payload)
    return result is not None


def extract_reference_netlist(messages_history):
    """优先提取上一轮识别得到的网表正文。"""
    for msg in reversed(messages_history or []):
        if msg.get("role") != "assistant":
            continue
        content = str(msg.get("content", "") or "").strip()
        if not content:
            continue

        lower = content.lower()
        start = lower.find("<answer>")
        if start >= 0:
            end = lower.find("</answer>", start + len("<answer>"))
            if end > start:
                extracted = content[start + len("<answer>"):end].strip()
            else:
                extracted = content[start + len("<answer>"):].strip()
            if extracted:
                return extracted

        bracket_lines = [line.strip() for line in content.splitlines() if line.strip().startswith("[") and line.strip().endswith("]")]
        if bracket_lines:
            return "\n".join(bracket_lines)

    return ""


def normalize_history_text(content):
    text = str(content or "").strip()
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if "<img" in text.lower():
        text = "[用户上传了一张电路图片]"
    return text.strip()


def build_api_history_context(messages_history, max_messages=20, max_chars=12000):
    """把整段会话整理成稳定的文本上下文，避免直接堆太多 chat messages。"""
    if not messages_history:
        return ""

    trimmed_messages = messages_history[-max_messages:] if len(messages_history) > max_messages else list(messages_history)
    rendered = []
    total_chars = 0

    for msg in reversed(trimmed_messages):
        role = "用户" if msg.get("role") == "user" else "助手"
        content = normalize_history_text(msg.get("content", ""))
        if not content:
            continue

        block = f"{role}：\n{content}"
        projected = total_chars + len(block) + 2
        if rendered and projected > max_chars:
            break
        rendered.append(block)
        total_chars = projected

    rendered.reverse()
    return "\n\n".join(rendered)

# =============== 系统提示词函数 ===============
def get_system_prompt(has_image=False):
    """根据是否有图片返回对应的系统提示词"""
    if not has_image:
        # 正常对话的prompt
        return """你是电路大模型，基于Circuit-Think的电路设计智能助手，由浙江大学开发。你专门帮助用户解决电路设计、电路分析、电路图像识别和转换等相关问题。

你的专业领域包括：
1. 电路设计和分析
2. 电路图像识别和解读  
3. 电路原理图绘制建议
4. 电路仿真和测试建议
5. 电路故障诊断和维修

当用户询问电路设计问题时，请提供专业且易懂的解答和建议。
请用专业但易懂的语言回答用户的电路相关问题。"""
    
    # 有图片时的prompt
    base_prompt = """你是电路大模型，基于Circuit-Think的电路设计智能助手，由浙江大学开发。你专门帮助用户解决电路设计、电路分析、电路图像识别和转换等相关问题。

你的专业领域包括：
1. 电路设计和分析
2. 电路图像识别和解读  
3. 电路原理图绘制建议
4. 电路仿真和测试建议
5. 电路故障诊断和维修"""

    return base_prompt + """

**首先关注用户的具体问题，根据用户问题决定如何分析电路图像。**

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
        gpu_memory_util = 0.8
        max_model_len = 6144
        
        logger.info(f"使用单GPU vLLM模式")
        
        vllm_engine = LLM(
            model=model_path,
            tensor_parallel_size=tensor_parallel_size,
            gpu_memory_utilization=gpu_memory_util,
            dtype="bfloat16",
            trust_remote_code=True,
            max_model_len=max_model_len,
            enforce_eager=True,
            disable_log_stats=False,
        )
        
        vllm_sampling_params = SamplingParams(
            temperature=0.7,
            top_p=0.9,
            top_k=50,
            max_tokens=4096,
            stop_token_ids=[151643, 151645, 151646],
            repetition_penalty=1.05,
        )
        
        logger.info("vLLM VL模型加载成功!")
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
        ensure_qwen25_vl_generation_config_patch()
        quantization_config = BitsAndBytesConfig(
            load_in_8bit=True,
            llm_int8_threshold=6.0,
            llm_int8_has_fp16_weight=False
        )
        
        model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            model_path,
            quantization_config=quantization_config,
            device_map="cuda:0",
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
        ensure_qwen25_vl_generation_config_patch()
        model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            model_path,
            torch_dtype="auto",
            device_map="cuda:0",
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


def ensure_local_model_loaded():
    """按需加载本地模型，供图片请求使用。"""
    global model
    if model is not None:
        return True
    logger.info("触发图片请求，按需加载本地模型...")
    return load_model_by_mode(MODEL_PATH, DESIRED_MODE)

# =============== 统一的生成接口 ===============
def stream_generate_with_local_model(user_conversation_history, user_message, uploaded_image_path=None, max_tokens=None):
    """本地模型流式生成（用于图片处理）"""
    global CURRENT_MODE
    
    has_image = uploaded_image_path and os.path.exists(uploaded_image_path)
    if max_tokens is None:
        max_tokens = CIRCUIT_MAX_NEW_TOKENS
    system_prompt = get_system_prompt(has_image)
    prompt_text = CIRCUIT_IMAGE_FIXED_PROMPT if has_image else (user_message or "你好")

    messages = [{"role": "system", "content": system_prompt}]
    if (not has_image) and user_conversation_history:
        recent_history = user_conversation_history[-6:] if len(user_conversation_history) > 6 else user_conversation_history
        messages.extend(recent_history)

    if CURRENT_MODE == InferenceMode.VLLM:
        try:
            image_paths = []
            if has_image:
                image_paths.append(uploaded_image_path)
            messages.append({"role": "user", "content": prompt_text})
            result = generate_with_vllm(messages, image_paths)
            
            # 模拟流式输出
            chunk_size = 8
            for i in range(0, len(result), chunk_size):
                chunk = result[i:i + chunk_size]
                yield chunk
                time.sleep(0.02)
                
        except Exception as e:
            logger.error(f"vLLM生成出错: {str(e)}")
            yield f"[vLLM生成出错: {str(e)}]"
    
    else:
        try:
            image = None
            if has_image:
                image = Image.open(uploaded_image_path).convert('RGB')
                if image.size[0] > 1024 or image.size[1] > 1024:
                    image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)

            if has_image and image is not None:
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "image": image},
                            {"type": "text", "text": prompt_text},
                        ],
                    }
                )
            else:
                messages.append({"role": "user", "content": prompt_text})
            
            text = processor.apply_chat_template(
                messages, 
                tokenize=False, 
                add_generation_prompt=True
            )
            
            images = [image] if has_image and image is not None else None
            
            inputs = processor(
                text=[text],
                images=images,
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
                        "eos_token_id": processor.tokenizer.eos_token_id,
                        "do_sample": False,
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

def stream_generate_with_api(
    user_conversation_history,
    user_message,
    uploaded_image_path=None,
    uploaded_image_url=None,
    reference_netlist="",
):
    """使用API流式生成（支持图像识别）"""
    has_image = bool(uploaded_image_path and os.path.exists(uploaded_image_path))
    prompt_text = user_message or "请分析这张电路图"
    system_prompt = get_system_prompt(has_image=has_image)
    history_context = build_api_history_context(user_conversation_history)
    
    messages = [{"role": "system", "content": system_prompt}]
    
    def build_payload(image_url=None):
        req_messages = list(messages)
        text_blocks = []

        if history_context:
            text_blocks.append("本次会话的历史记录整理如下：\n" + history_context)

        if reference_netlist:
            text_blocks.append("解析参考的网表如下：\n" + reference_netlist)

        text_blocks.append("新的问题如下：\n" + prompt_text)

        if image_url:
            text_blocks.append("用户上传的图片如下，请结合图像内容进行分析。")
            user_content = [
                {
                    "type": "text",
                    "text": "\n\n".join(text_blocks)
                },
                {
                    "type": "image_url",
                    "image_url": {"url": image_url}
                }
            ]
            req_messages.append({"role": "user", "content": user_content})
        else:
            req_messages.append({"role": "user", "content": "\n\n".join(text_blocks)})
        return {
            "model": MODEL_NAME,
            "messages": req_messages,
            "temperature": 0.7,
            "top_p": 0.9,
            "max_tokens": CIRCUIT_MAX_NEW_TOKENS,
            "stream": True
        }

    def encode_image_to_data_url(image_path):
        ext = os.path.splitext(image_path)[1].lower()
        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
        }
        mime_type = mime_map.get(ext, "image/jpeg")
        with open(image_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        return f"data:{mime_type};base64,{encoded}"

    logger.info(f"使用API进行流式生成, has_image={has_image}, model={MODEL_NAME}")

    response = None
    if has_image:
        # 旧逻辑（先传图片URL，失败后再兜底data URL）已停用，保留如下便于回滚：
        # try:
        #     response = generate_with_api_retry(build_payload(uploaded_image_url), stream=True)
        # except Exception as url_error:
        #     logger.warning(f"图片URL方式请求失败，尝试data URL兜底: {str(url_error)}")
        #     image_data_url = encode_image_to_data_url(uploaded_image_path)
        #     response = generate_with_api_retry(build_payload(image_data_url), stream=True)
        try:
            image_data_url = encode_image_to_data_url(uploaded_image_path)
            response = generate_with_api_retry(build_payload(image_data_url), stream=True)
        except Exception as data_url_error:
            logger.error(f"图片请求失败(data URL): {str(data_url_error)}")
            yield "[图片请求失败，请检查图片格式或稍后重试]"
            return
    else:
        try:
            response = generate_with_api_retry(build_payload(), stream=True)
        except Exception as text_error:
            logger.error(f"文本请求失败: {str(text_error)}")
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
                        choices = chunk_data.get("choices") or []
                        if not choices:
                            continue

                        delta = (choices[0].get("delta") or {}).get("content", "")
                        if isinstance(delta, list):
                            text_parts = [
                                part.get("text", "")
                                for part in delta
                                if isinstance(part, dict) and part.get("type") == "text"
                            ]
                            delta = "".join(text_parts)

                        if delta:
                            yield delta
                    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
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
                
                circuit_msgs = [msg for msg in msgs if msg.get('modelId') == 5]
                
                for msg in circuit_msgs:
                    if msg.get('userType') == MESSAGE_TYPE.USER:
                        messages_history.append({"role": "user", "content": msg.get('content', '')})
                    elif msg.get('userType') == MESSAGE_TYPE.BOT:
                        messages_history.append({"role": "assistant", "content": msg.get('content', '')})
                
                logger.info(f"加载了 {len(messages_history)} 条历史消息")
            else:
                logger.warning(f"从原始后端获取会话 {conversation_id} 的消息失败或为空")
        except Exception as e:
            logger.error(f"获取历史消息时出错: {str(e)}")

        if not messages_history:
            cached_history = get_cached_messages(user_id, conversation_id)
            if cached_history:
                messages_history = cached_history
                logger.info(f"后端历史不可用，改用服务内缓存消息 {len(messages_history)} 条")

        # =============== 图片分析逻辑保持不变 ===============
        pending_image = peek_pending_uploaded_image(user_id, conversation_id)
        latest_uploaded_image_path, latest_uploaded_image_url = get_latest_uploaded_image(
            user_id, conversation_id, include_url=True
        )
        uploaded_image_path = None
        uploaded_image_url = None
        if pending_image:
            uploaded_image_path = pending_image.get("file_path")
            uploaded_image_url = pending_image.get("image_url")

        has_pending_image = bool(uploaded_image_path and os.path.exists(uploaded_image_path))
        is_image_analysis = has_pending_image
        reference_netlist = extract_reference_netlist(messages_history)
        
        def generate():
            request_id = f"{conversation_id}_{str(time.time())}"
            inference_type = f"local_{CURRENT_MODE.value}"
            logger.info(f"开始流式输出,请求ID: {request_id}, 推理类型: {inference_type}, 用户消息: {user_message[:20]}...")
            logger.info(f"传入生成函数的历史消息数量: {len(messages_history)}")
            
            aborted_streams = getattr(app, 'aborted_streams', set())
            if not hasattr(app, 'aborted_streams'):
                app.aborted_streams = set()
            
            start_chunk = {
                "chunk": "",
                "is_complete": False,
                "start_streaming": True,
                "request_id": request_id,
                "conversation_id": conversation_id,
                "inference_type": inference_type
            }
            
            yield f"data: {json.dumps(start_chunk, ensure_ascii=False)}\n\n"
            
            try:
                chunk_count = 0
                assistant_chunks = []
                
                use_local_for_this_turn = has_pending_image if HYBRID_IMAGE_LOCAL_TEXT_API else (CURRENT_PROVIDER == "local")

                if use_local_for_this_turn:
                    if not ensure_local_model_loaded():
                        yield "[本地模型加载失败，请稍后重试]"
                        return
                    pop_pending_uploaded_image(user_id, conversation_id)
                    if is_image_analysis:
                        logger.info(f"检测到图片分析请求，使用本地模型: {MODEL_PATH}")
                    else:
                        logger.info(f"检测到图片输入，使用本地模型: {MODEL_PATH}")
                    generation_stream = stream_generate_with_local_model(
                        messages_history,
                        user_message,
                        uploaded_image_path=uploaded_image_path if has_pending_image else None,
                    )
                else:
                    logger.info(f"本轮为纯文本输入，使用API推理: {MODEL_NAME}")
                    generation_stream = stream_generate_with_api(
                        messages_history,
                        user_message,
                        uploaded_image_path=latest_uploaded_image_path,
                        uploaded_image_url=latest_uploaded_image_url,
                        reference_netlist=reference_netlist,
                    )
                
                for chunk in generation_stream:
                    if request_id in app.aborted_streams:
                        logger.info(f"请求 {request_id} 已被中止")
                        app.aborted_streams.discard(request_id)
                        abort_complete = {
                            "chunk": "\n\n[回答已中止]",
                            "is_complete": True,
                            "aborted": True,
                            "conversation_id": conversation_id
                        }
                        yield f"data: {json.dumps(abort_complete)}\n\n"
                        return
                    
                    if chunk:
                        chunk_count += 1
                        assistant_chunks.append(chunk)
                        logger.debug(f"发送chunk {chunk_count}: {repr(chunk)}")
                        
                        data = json.dumps({
                            "chunk": chunk,
                            "is_complete": False,
                            "conversation_id": conversation_id
                        }, ensure_ascii=False)
                        yield f"data: {data}\n\n"
                
                yield f"data: {json.dumps({'is_complete': True, 'conversation_id': conversation_id})}\n\n"
                final_assistant_text = "".join(assistant_chunks)
                append_cached_exchange(user_id, conversation_id, user_message, final_assistant_text)
                if persist_assistant_message(user_id, conversation_id, final_assistant_text):
                    logger.info("助手消息已写回后端 message/add")
                else:
                    logger.warning("助手消息写回后端失败，当前仅保留服务内缓存")
                logger.info(f"响应生成完成，总共发送了 {chunk_count} 个chunks，推理类型: {inference_type}")
                
            except GeneratorExit:
                logger.info(f"客户端断开连接,请求ID: {request_id}")
                app.aborted_streams.discard(request_id)
                
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
        
        if file_type in ["glp", "png", "jpg", "jpeg", "gif", "bmp", "webp"]:
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
                
                is_image = file_type.lower() in ["png", "jpg", "jpeg", "gif", "bmp", "webp"]
                
                logger.info(f"生成的图片URL: {image_url}")
                logger.info(f"实际保存的文件名: {saved_filename}")
                if is_image:
                    mark_pending_uploaded_image(user_id, conversation_id, file_path, image_url)
                
                if is_image:
                    html_content = f'<img src="{image_url}" alt="{file.filename}" style="max-width: 400px; max-height: 400px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">'
                    return jsonify({"role": "assistant", "content": html_content}), 200
                else:
                    return jsonify({"role": "assistant", "content": f"文件 {file.filename} 上传成功！"}), 200
        
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
        
        app.aborted_streams.add(request_id)
        
        return jsonify({
            'message': f"已标记请求 {request_id} 为中止状态",
            'success': True
        }), 200
    except Exception as e:
        logger.error(f'中止流式输出时出错: {e}')
        return jsonify({'error': '中止失败', 'success': False}), 500

@app.route('/add_message', methods=['POST'])
def add_message():
    try:
        message_data = request.json
        
        required_fields = ['content', 'sessionId', 'userType', 'userId']
        missing_fields = [field for field in required_fields if field not in message_data]
        
        if missing_fields:
            return jsonify({'error': f"消息缺少必要字段: {missing_fields}"}), 400
        
        session_id = message_data.get('sessionId')
        message_data['userId'] = normalize_user_id(message_data.get('userId'))
        
        if not ensure_session_exists(session_id, message_data.get('userId', 'default')):
            return jsonify({"error": "无法创建会话"}), 500
        
        if 'modelId' not in message_data:
            message_data['modelId'] = 5

        message_data['messageId'] = normalize_backend_message_id(message_data.get('messageId'))
        
        if 'timestamp' not in message_data or not message_data['timestamp']:
            message_data['timestamp'] = datetime.now().isoformat()
        
        result = proxy_to_original_backend("message/add", method="POST", data=message_data)
        
        if result is not None:
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
        
        if result is not None:
            return jsonify({'success': True, 'session_id': session_id}), 200
        else:
            return jsonify({'error': '更新会话失败'}), 500
    
    except Exception as e:
        logger.error(f"更新会话时出错: {str(e)}")
        return jsonify({'error': str(e), 'errorType': type(e).__name__}), 500

@app.route('/switch_mode', methods=['POST'])
def switch_inference_mode():
    """切换推理模式的接口"""
    return jsonify({
        'success': True,
        'current_mode': CURRENT_MODE.value,
        'available_modes': [mode.value for mode in InferenceMode],
        'message': '当前服务使用本地模型推理，启动后不支持在线切换模式'
    }), 200

@app.route('/current_mode', methods=['GET'])
def get_current_mode():
    """获取当前推理模式"""
    return jsonify({
        'current_mode': CURRENT_MODE.value,
        'available_modes': [mode.value for mode in InferenceMode],
        'provider': CURRENT_PROVIDER,
        'hybrid_routing': HYBRID_IMAGE_LOCAL_TEXT_API,
        'model_path': MODEL_PATH if (CURRENT_PROVIDER == 'local' or HYBRID_IMAGE_LOCAL_TEXT_API) else None,
        'model_name': MODEL_NAME if (CURRENT_PROVIDER == 'api' or HYBRID_IMAGE_LOCAL_TEXT_API) else None,
    })

@app.route('/health', methods=['GET'])
def health_check():
    """健康检查接口"""
    return jsonify({
        'status': 'healthy',
        'service': 'CircuitThink',
        'current_mode': CURRENT_MODE.value,
        'provider': CURRENT_PROVIDER,
        'hybrid_routing': HYBRID_IMAGE_LOCAL_TEXT_API,
        'model_path': MODEL_PATH if (CURRENT_PROVIDER == 'local' or HYBRID_IMAGE_LOCAL_TEXT_API) else None,
        'model_name': MODEL_NAME if (CURRENT_PROVIDER == 'api' or HYBRID_IMAGE_LOCAL_TEXT_API) else None,
        'upload_base_dir': UPLOAD_BASE_DIR,
        'backend_url': BACKEND_URL,
        'timestamp': datetime.now().isoformat()
    }), 200

# =============== 标题生成接口 ===============
@app.route('/generate_session_title', methods=['POST'])
def generate_session_title():
    """生成会话标题的接口 - 使用简单截取方式"""
    try:
        data = request.json
        user_message = data.get('user_message', '')
        bot_response = data.get('bot_response', '')
        message_type = data.get('message_type', 'auto')
        
        logger.info(f"收到标题生成请求: user_message长度={len(user_message)}, bot_response长度={len(bot_response)}, type={message_type}")
        
        # 简单直接的标题生成：取前7个字符，图片上传特殊处理
        def generate_simple_title(user_msg):
            # 检查是否是图片上传
            if '<img' in user_msg or 'src=' in user_msg or '请分析这张电路图' in user_msg:
                return '电路图理解'
            
            # 清理用户输入，移除HTML标签
            clean_user = user_msg.replace('<img', '').replace('src=', '').replace('>', '').replace('<', '').strip()
            
            if clean_user:
                # 取前7个字符
                title = clean_user[:7]
                if len(title) < 2:  # 如果太短，使用默认标题
                    title = '新对话'
                return title
            else:
                return '新对话'
        
        title = generate_simple_title(user_message)
        logger.info(f"简单标题生成成功: {title}")
        
        return jsonify({
            "success": True,
            "title": title,
            "message": "标题生成成功"
        })
            
    except Exception as e:
        logger.error(f"标题生成出错: {str(e)}")
        return jsonify({
            "success": True,
            "title": "新对话",
            "message": "使用默认标题"
        })

# =============== 主函数 ===============
if __name__ == '__main__':
    if CURRENT_PROVIDER == "local" or HYBRID_IMAGE_LOCAL_TEXT_API:
        MODEL_PATH = resolve_local_model_path(MODEL_PATH)

    if (CURRENT_PROVIDER == "local" or HYBRID_IMAGE_LOCAL_TEXT_API) and DESIRED_MODE == InferenceMode.VLLM and not QWEN_VL_UTILS_AVAILABLE:
        logger.error("vLLM模式需要安装qwen-vl-utils")
        logger.error("请运行: pip install qwen-vl-utils[decord]")
        logger.error("然后重新启动服务")
        exit(1)

    setup_memory_optimization()
    if CURRENT_PROVIDER == "local" and not HYBRID_IMAGE_LOCAL_TEXT_API:
        success = load_model_by_mode(MODEL_PATH, DESIRED_MODE)
        if not success:
            logger.error("本地模型加载失败")
            exit(1)
    
    logger.info("=" * 60)
    logger.info("CircuitThink服务已启动")
    logger.info(f"推理提供方: {CURRENT_PROVIDER}")
    logger.info(f"混合路由(图片本地+文本API): {HYBRID_IMAGE_LOCAL_TEXT_API}")
    if HYBRID_IMAGE_LOCAL_TEXT_API:
        logger.info(f"本地模型目录(图片): {MODEL_PATH}")
        logger.info(f"API模型(文本): {MODEL_NAME}")
        logger.info("图片处理: 使用本地模型推理")
        logger.info("文本对话: 使用API流式输出")
    elif CURRENT_PROVIDER == "local":
        logger.info(f"本地模型目录: {MODEL_PATH}")
        logger.info(f"推理模式: {CURRENT_MODE.value}")
        logger.info("图片处理: 使用本地模型推理")
        logger.info("文本对话: 使用本地模型流式输出")
    else:
        logger.info(f"API模型: {MODEL_NAME}")
        logger.info("图片处理: 使用API推理")
        logger.info("文本对话: 使用API流式输出")
    logger.info(f"运行于 http://{SERVICE_HOST}:{CIRCUIT_PORT}")
    logger.info("=" * 60)
    
    app.run(debug=False, host=SERVICE_HOST, port=CIRCUIT_PORT, threaded=True)
