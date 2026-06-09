from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import requests
from datetime import datetime
import json
import random
import time
import logging
import os
import re
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 确保本地服务调用不经过系统代理（避免127.0.0.1:11888等代理干扰）
DEFAULT_SERVICE_HOST = os.environ.get("WEB_FABGPT_HOST", "10.98.193.46")
_local_no_proxy = f"127.0.0.1,localhost,{DEFAULT_SERVICE_HOST}"
os.environ["NO_PROXY"] = ",".join(
    filter(None, [os.environ.get("NO_PROXY", ""), _local_no_proxy])
)
os.environ["no_proxy"] = os.environ["NO_PROXY"]

SERVICE_HOST = DEFAULT_SERVICE_HOST
BACKEND_PORT = int(os.environ.get("WEB_FABGPT_BACKEND_PORT", "5107"))
CHATBOT_PORT = int(os.environ.get("WEB_FABGPT_CHATBOT_PORT", "5101"))
RAG_MANAGER_PORT = int(os.environ.get("WEB_FABGPT_RAG_PORT", "5106"))
ORIGINAL_API_URL = f"http://{SERVICE_HOST}:{BACKEND_PORT}"

api_session = requests.Session()
api_session.trust_env = False
api_adapter = HTTPAdapter(
    pool_connections=20,
    pool_maxsize=40,
    max_retries=Retry(total=2, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504]),
)
api_session.mount("https://", api_adapter)
api_session.mount("http://", api_adapter)

# 添加代理请求函数
def proxy_to_original_backend(original_endpoint, method="GET", data=None, params=None, headers=None):
    """代理请求到原始8080后端"""
    try:
        url = f"{ORIGINAL_API_URL}/{original_endpoint}"
        default_headers = {"Content-Type": "application/json"}
        if headers:
            default_headers.update(headers)
            
        logger.info(f"代理请求到原始后端: URL={url}, 方法={method}")
        
        if method.upper() == "GET":
            response = api_session.get(url, params=params, headers=default_headers, timeout=10)
        elif method.upper() == "POST":
            response = api_session.post(url, json=data, headers=default_headers, timeout=10)
        else:
            logger.error(f"不支持的HTTP方法: {method}")
            return None
            
        if response.status_code >= 200 and response.status_code < 300:
            try:
                return response.json()
            except json.JSONDecodeError:
                # 如果不是JSON格式，返回文本内容
                return response.text
        # else:
        #     logger.error(f"原始后端返回错误: {response.status_code}, URL: {url}")
        #     return None
        else:
            error_text = ""
            try:
                error_text = response.text
            except:
                error_text = "无法读取错误内容"
            logger.error(f"原始后端返回错误: {response.status_code}, URL: {url}, 错误详情: {error_text}")
            return None
    except Exception as e:
        logger.error(f"代理到原始后端时出错: {str(e)}, URL: {original_endpoint}")
        return None

class MESSAGE_TYPE:
    USER = 'user'
    BOT = 'bot'

def convert_user_id_to_int(user_id_value):
    """将任意user_id稳定转换为后端可接受的整数类型。"""
    if isinstance(user_id_value, int):
        return user_id_value

    user_id_str = str(user_id_value or "").strip()
    if user_id_str.isdigit():
        return int(user_id_str)

    import hashlib
    digest = hashlib.md5(user_id_str.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 2147483647

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("chatbot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("Chatbot-Service")
# 提高werkzeug的日志级别，只显示警告和错误
logging.getLogger("werkzeug").setLevel(logging.WARNING)
# 提高应用日志级别，减少一般信息日志
logging.getLogger("Chatbot-Service").setLevel(logging.INFO)

app = Flask(__name__)
CORS(app)

# 添加 RAG Manager 服务相关配置
SOCKET_TIMEOUT = 10  # Socket连接超时时间（秒）
is_rag_manager_available = False  # 是否可以连接到RAG Manager服务

CODE_REQUEST_PATTERN = re.compile(
    r"(代码|脚本|scheme|sde|sentaurus|tcad|python|program|function|生成.*代码|可执行)",
    re.IGNORECASE,
)
WHITESPACE_PATTERN = re.compile(r"\s+")
NUMERIC_PATTERN = re.compile(r"\b\d+(?:\.\d+)?(?:e[+-]?\d+)?\b", re.IGNORECASE)

# LLM API配置
API_URL = os.environ.get(
    "WEB_FABGPT_CHATBOT_API_URL",
    os.environ.get(
        "WEB_FABGPT_LLM_API_URL",
        os.environ.get("WEB_FABGPT_TEXT_API_BASE_URL", "https://api.siliconflow.cn/v1/chat/completions"),
    ),
)
API_KEY = os.environ.get(
    "WEB_FABGPT_CHATBOT_API_KEY",
    os.environ.get(
        "WEB_FABGPT_LLM_API_KEY",
        os.environ.get("WEB_FABGPT_SILICONFLOW_API_KEY", ""),
    ),
)
MODEL_NAME = os.environ.get(
    "WEB_FABGPT_CHATBOT_MODEL",
    os.environ.get("WEB_FABGPT_TEXT_MODEL", os.environ.get("WEB_FABGPT_LLM_MODEL", "Qwen/Qwen2.5-72B-Instruct")),
)
TITLE_MODEL_NAME = os.environ.get("WEB_FABGPT_TITLE_MODEL", MODEL_NAME)

# 请求头配置
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}"
}

# 用于存储每个用户的对话历史和上传文件
conversation_history = {}
uploaded_files = {}  # 按对话ID存储

################ RagManager

# 检查RagManager服务是否可用
def check_rag_manager_availability():
    """检查RagManager服务是否可用"""
    try:
        # 最大重试次数
        max_retries = 3
        retry_interval = 2  # 秒
        
        logger.info(f"检查RagManager服务是否可用 (端口: {RAG_MANAGER_PORT})...")
        
        for i in range(max_retries):
            try:
                response = api_session.get(f"http://{SERVICE_HOST}:{RAG_MANAGER_PORT}/health", timeout=10)
                
                if response.status_code == 200:
                    logger.info("成功连接到RagManager服务")
                    # 增加更详细的日志输出
                    try:
                        healthData = response.json()
                        logger.info(f"RagManager健康状态: {healthData}")
                    except:
                        logger.info("RagManager返回了有效响应但不是JSON格式")
                    return True
                else:
                    logger.warning(f"RagManager服务响应异常: {response.status_code}, 响应内容: {response.text[:100]}")
            except requests.exceptions.ConnectionError:
                logger.warning(f"无法连接到RagManager服务，重试 {i+1}/{max_retries}")
            except Exception as e:
                logger.error(f"检查RagManager服务时出错: {str(e)}")
            
            # 等待一段时间后重试
            time.sleep(retry_interval)
        
        logger.error(f"无法连接到RagManager服务，请确保RagManager已启动并监听端口 {RAG_MANAGER_PORT}")
        return False
    except Exception as e:
        logger.error(f"检查RagManager可用性时出错: {str(e)}")
        return False

# 从RagManager获取相关上下文
def get_relevant_context(query, user_id='default', config_id='default'):
    try:
        logger.info(f"RAG查询: {query}, 用户ID: {user_id}, 使用知识库ID: {config_id}")
        
        # 构建对RagManager的请求
        import requests
        
        # 构建请求URL - 使用RAG查询标准端点而不是Chatbot专用端点
        url = f"http://{SERVICE_HOST}:{RAG_MANAGER_PORT}/get_relevant_context"
        
        # 优化payload结构，参考TCAD的实现
        payload = {
            "query": query,
            "max_tokens": 4000,
            "config_id": config_id,
            "service": "chatbot",  # 标识请求来源
            "user_id": user_id,
            "query_timestamp": int(time.time())  # 添加时间戳防止缓存
        }
        
        # 发送前记录完整请求内容
        logger.info(f"向RAG Manager发送请求: URL={url}, Payload={payload}")
        
        # 发送请求，增加超时时间
        response = api_session.post(url, json=payload, timeout=120)  # 增加超时时间为120秒
        
        if response.status_code == 200:
            context_data = response.json()
            raw_context = context_data.get("context", "")
            knowledge_base_name = context_data.get("knowledge_base_name", "")
            search_id = context_data.get("search_id", "")
            
            # 添加查询结果日志
            if raw_context and "未找到" not in raw_context and "出错" not in raw_context:
                logger.info(f"成功获取RAG上下文，长度: {len(raw_context)} 字符，使用知识库: {knowledge_base_name}, 搜索ID: {search_id}")
                return {
                    "context": raw_context,
                    "knowledge_base_name": knowledge_base_name,
                    "search_id": search_id,
                    "results": context_data.get("results", []),
                }
            else:
                logger.warning(f"RAG查询未返回有效内容: {raw_context[:100]}...")
                return {
                    "context": "知识库中未找到相关内容。",
                    "knowledge_base_name": knowledge_base_name,
                    "search_id": search_id,
                    "results": [],
                }
        else:
            error_msg = f"RAG查询失败: HTTP {response.status_code}"
            logger.error(error_msg)
            return {
                "context": "从知识库检索上下文时出错。",
                "knowledge_base_name": "",
                "search_id": "",
                "results": [],
            }
            
    except Exception as e:
        logger.error(f"[RAG ERROR] get_relevant_context函数出错: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            "context": "访问知识库时出错。",
            "knowledge_base_name": "",
            "search_id": "",
            "results": [],
        }


def _is_code_generation_request(user_message: str) -> bool:
    return bool(CODE_REQUEST_PATTERN.search(user_message or ""))


def _normalize_text_block(text: str) -> str:
    return WHITESPACE_PATTERN.sub(" ", (text or "")).strip()


def _truncate_text(text: str, limit: int = 520) -> str:
    normalized = _normalize_text_block(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _analyze_codegen_spec_coverage(user_message: str) -> dict:
    message = user_message or ""
    lowered = message.lower()
    coverage = {
        "geometry": bool(re.search(r"(长度|宽度|厚度|半径|尺寸|geometry|gate length|width|height|thickness|nm|um|μm)", lowered)),
        "material": bool(re.search(r"(材料|material|silicon|oxide|nitride|polysilicon|gaas|gan|algan|siO2|hfo2)", lowered, re.IGNORECASE)),
        "contact": bool(re.search(r"(接触|电极|contact|source|drain|gate|anode|cathode|substrate)", lowered)),
        "doping": bool(re.search(r"(掺杂|doping|gaussian|constant|profile|浓度|结深|halo|extension)", lowered)),
        "mesh": bool(re.search(r"(网格|mesh|refinement|细化|window|窗口)", lowered)),
        "output": bool(re.search(r"(输出|保存|build-mesh|文件名|output|save)", lowered)),
    }
    numeric_present = bool(NUMERIC_PATTERN.search(message))
    covered_categories = [name for name, present in coverage.items() if present]
    missing_categories = [name for name, present in coverage.items() if not present]
    sufficiently_specified = numeric_present and len(covered_categories) >= 3
    return {
        "numeric_present": numeric_present,
        "covered_categories": covered_categories,
        "missing_categories": missing_categories,
        "sufficiently_specified": sufficiently_specified,
    }


def _format_rag_evidence(rag_payload: dict) -> str:
    results = rag_payload.get("results") or []
    if not results:
        raw_context = rag_payload.get("context", "")
        if raw_context and "未找到" not in raw_context and "出错" not in raw_context:
            return _truncate_text(raw_context, limit=1200)
        return ""

    evidence_blocks = []
    for index, item in enumerate(results[:4], start=1):
        section_title = str(item.get("section_title") or "").strip()
        page_start = item.get("page_num_start") or item.get("page_num")
        page_end = item.get("page_num_end") or page_start
        location_bits = []
        if section_title:
            location_bits.append(f"章节: {section_title}")
        if page_start:
            if page_end and page_end != page_start:
                location_bits.append(f"页码: {page_start}-{page_end}")
            else:
                location_bits.append(f"页码: {page_start}")
        header = f"[证据{index}]"
        if location_bits:
            header += " " + " | ".join(location_bits)
        body = _truncate_text(str(item.get("text_content") or ""))
        if body:
            evidence_blocks.append(f"{header}\n{body}")
    return "\n\n".join(evidence_blocks)


def _build_system_prompt(user_message: str, rag_payload: dict | None) -> str:
    prompt = "你是FabGPT，基础模型为General-FabGPT，由浙江大学开发的智能助手。"
    if not rag_payload:
        return prompt

    rag_evidence = _format_rag_evidence(rag_payload)
    if not rag_evidence:
        return prompt

    prompt += (
        "\n\n你将收到一组来自知识库的检索证据。请严格按以下规则使用它们："
        "\n1. 检索证据只是参考证据，不是可直接照抄的答案模板。"
        "\n2. 优先采信连贯的解释性正文，弱化目录、索引、命令速查表、函数列表、参数清单。"
        "\n3. 不要根据零散命令片段、目录项或 API 名称拼凑伪代码。"
        "\n4. 如果证据不足以支撑确定性结论，要明确说明不确定，并向用户索要缺失信息。"
        "\n5. 若证据与常识冲突，以更稳妥、更保守的工程判断为准。"
    )

    if _is_code_generation_request(user_message):
        spec_coverage = _analyze_codegen_spec_coverage(user_message)
        prompt += (
            "\n\n对于代码/脚本生成类请求："
            "\n- 只有在规格充分时才输出可执行代码。"
            "\n- 如果文档只给了局部命令说明，没有给完整器件规格或边界条件，不要硬写完整代码。"
            "\n- 优先确认关键规格是否齐全，例如 geometry、material、contact、doping、mesh、output。"
            "\n- 若信息不足，先用最短的话指出缺失项，再继续生成。"
        )
        if not spec_coverage["sufficiently_specified"]:
            missing_items = ", ".join(spec_coverage["missing_categories"])
            prompt += (
                "\n\n当前请求分析：用户尚未提供足以生成可执行工程代码的完整规格。"
                "\n你必须先指出信息不足，并只追问缺失的关键项；不要输出示例代码、伪代码、占位代码或猜测性参数。"
                f"\n当前缺失的关键类别：{missing_items or '未识别到足够的工程约束'}。"
            )

    prompt += f"\n\n知识库检索证据如下：\n{rag_evidence}"
    return prompt


def _build_generation_config(user_message: str, rag_payload: dict | None) -> dict:
    if rag_payload and _format_rag_evidence(rag_payload) and _is_code_generation_request(user_message):
        return {"temperature": 0.2, "top_p": 0.3}
    return {"temperature": 0.7, "top_p": 0.5}

# 以上为RAG相关功能嵌入
def generate_with_retry(payload, max_retries=3, stream=False):
    """带重试机制的API调用，支持流式响应"""
    for attempt in range(max_retries):
        try:
            response = api_session.post(API_URL, headers=headers, json=payload, stream=stream, timeout=120)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                print(f"请求失败，{wait_time}秒后重试... 错误：{str(e)}")
                time.sleep(wait_time)
            else:
                raise
    return None

#支持中止和暂停的流式输出
@app.route('/stream_generate', methods=['POST'])
def stream_generate_response():
    try:
        data = json.loads(request.data)
        user_message = data.get('message', '')
        user_id = data.get('user_id', 'default')
        conversation_id = data.get('conversation_id', user_id)
        config_id = data.get('config_id', 'default')
        
        if not conversation_id or conversation_id.strip() == '':
            logger.error("无效的请求标识符")
            return jsonify({"error": "无效的请求标识符"}), 400
        
        # 检查会话是否存在
        session_check = proxy_to_original_backend(f"session/get?sessionId={conversation_id}")
        if not session_check:
            logger.warning(f"会话 {conversation_id} 不存在,尝试创建")
            user_id_int = convert_user_id_to_int(user_id)
            new_session = {
                "sessionId": conversation_id,
                "createTime": datetime.now().isoformat(),
                "header": user_message[:8] if user_message else "新会话",
                "lastActive": datetime.now().isoformat(),
                "modelId": 0,
                "status": 1,
                "userId": user_id_int
            }
            
            result = proxy_to_original_backend("session/add", method="POST", data=new_session)
            if result is None:
                logger.error(f"创建新会话 {conversation_id} 失败")
                return jsonify({"error": "创建会话失败"}), 500
                
            logger.info(f"已创建新会话: {conversation_id}")
        
        # 获取会话历史消息
        messages_history = []
        try:
            msgs = proxy_to_original_backend(f"message/list-by-session?sessionId={conversation_id}")
            
            if msgs:
                for msg in msgs:
                    if msg.get('userType') == MESSAGE_TYPE.USER:
                        messages_history.append({"role": "user", "content": msg.get('content', '')})
                    elif msg.get('userType') == MESSAGE_TYPE.BOT:
                        messages_history.append({"role": "assistant", "content": msg.get('content', '')})
                
                if len(messages_history) > 8:
                    messages_history = messages_history[-8:]
                    
                logger.info(f"从原始后端加载了 {len(messages_history)} 条会话 {conversation_id} 的历史消息")
            else:
                logger.warning(f"从原始后端获取会话 {conversation_id} 的消息失败")
        except Exception as e:
            logger.error(f"获取历史消息时出错: {str(e)}")
        
        # RAG功能
        rag_context = ""
        rag_payload = None
        if config_id != 'none' and is_rag_manager_available:
            try:
                logger.info(f"开始获取RAG上下文，知识库配置: {config_id}")
                rag_payload = get_relevant_context(
                    query=user_message,
                    user_id=user_id,
                    config_id=config_id
                )
                
                rag_context = (rag_payload or {}).get("context", "")
                if rag_context and "未找到" not in rag_context and "出错" not in rag_context:
                    logger.info(f"成功获取RAG上下文，长度: {len(rag_context)}")
                else:
                    logger.warning(f"RAG未返回有效内容: {rag_context}")
                    rag_payload = None
            except Exception as e:
                logger.error(f"获取RAG上下文时出错: {str(e)}")
                rag_payload = None
        else:
            if config_id == 'none':
                logger.info("知识库配置为'none'，跳过RAG检索")
            else:
                logger.warning("RAG Manager不可用，跳过RAG检索")

        # 构造消息
        system_prompt = _build_system_prompt(user_message, rag_payload)

        messages = [{"role": "system", "content": system_prompt}]
        
        if messages_history:
            messages.extend(messages_history)
        
        messages.append({"role": "user", "content": user_message})
        
        logger.info(f"准备发送给LLM的消息数量: {len(messages)}, 是否包含RAG上下文: {bool(rag_context)}")
        
        # API请求
        generation_config = _build_generation_config(user_message, rag_payload)

        payload = {
            "model": MODEL_NAME,
            "messages": messages,
            "temperature": generation_config["temperature"],
            "top_p": generation_config["top_p"],
            "stream": True
        }
        
        print("发送流式API请求...")
        response = generate_with_retry(payload, stream=True)
                
        if not response or response.status_code != 200:
            return jsonify({"error": "API请求失败"}), 500
                    
        def generate():
            request_id = f"{conversation_id}_{str(time.time())}"
            print(f"开始流式输出,请求ID: {request_id}")
            
            full_response = ""
            aborted_streams = getattr(app, 'aborted_streams', set())
            if not hasattr(app, 'aborted_streams'):
                app.aborted_streams = set()
            
            # 发送开始信号
            start_chunk = {
                "chunk": "",
                "is_complete": False,
                "start_streaming": True,
                "request_id": request_id,
                "conversation_id": conversation_id
            }
            
            try:
                start_data = json.dumps(start_chunk, ensure_ascii=False)
                yield f"data: {start_data}\n\n"
            except Exception as e:
                print(f"开始信号JSON序列化错误: {e}")
                yield f"data: {json.dumps(start_chunk)}\n\n"
            
            try:
                for line in response.iter_lines():
                    if request_id in app.aborted_streams:
                        print(f"检测到请求 {request_id} 已被中止,停止流式输出")
                        app.aborted_streams.discard(request_id)
                        abort_complete = {
                            "chunk": "\n\n[回答已中止]",
                            "is_complete": True,
                            "aborted": True,
                            "conversation_id": conversation_id
                        }
                        yield f"data: {json.dumps(abort_complete)}\n\n"
                        return
                    
                    if line:
                        line = line.decode('utf-8')
                        
                        if line.startswith("data: "):
                            line = line[6:]
                            
                            if line == "[DONE]":
                                data = json.dumps({
                                    "is_complete": True,
                                    "conversation_id": conversation_id
                                })
                                yield f"data: {data}\n\n"
                                break
                            
                            try:
                                chunk_data = json.loads(line)
                                delta = chunk_data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                                
                                if delta:
                                    full_response += delta
                                    try:
                                        data = json.dumps({
                                            "chunk": delta,
                                            "is_complete": False,
                                            "conversation_id": conversation_id
                                        }, ensure_ascii=False)
                                        yield f"data: {data}\n\n"
                                    except Exception as json_err:
                                        logger.error(f"JSON序列化错误: {json_err}")
                                        safe_delta = delta.encode('utf-8', errors='replace').decode('utf-8')
                                        data = json.dumps({
                                            "chunk": safe_delta,
                                            "is_complete": False,
                                            "conversation_id": conversation_id
                                        })
                                        yield f"data: {data}\n\n"
                            except json.JSONDecodeError:
                                continue
                
                # 清理会话历史
                if conversation_id in conversation_history:
                    conversation_history[conversation_id] = []

                logger.info(f"生成的响应长度: {len(full_response)}")
                
            except GeneratorExit:
                print(f"客户端断开连接,请求ID: {request_id}")
                app.aborted_streams.discard(request_id)
                
            except Exception as e:
                print(f"流式响应处理异常:{str(e)}")
                error_data = json.dumps({
                    "error": str(e),
                    "is_complete": True,
                    "conversation_id": conversation_id
                })
                yield f"data: {error_data}\n\n"
        
        return Response(generate(), mimetype='text/event-stream')
    except Exception as e:
        print(f"API调用异常:{str(e)}")
        return jsonify({"error": str(e)}), 500

########## 路由部分

@app.route('/set_active_configuration', methods=['POST'])
def set_active_configuration():
    """设置活跃的知识库配置 - 处理来自RagManager的同步请求"""
    try:
        data = request.json
        config_id = data.get('config_id')
        is_sync_request = data.get('is_sync_request', False)
        
        if not config_id:
            return jsonify({'error': '未提供配置ID'}), 400
            
        logger.info(f"收到设置知识库配置请求: {config_id}, 是否同步请求: {is_sync_request}")
        
        # 如果是同步请求，只更新本地配置，不再转发到RagManager
        if is_sync_request:
            # 特殊处理'none'配置
            if config_id == 'none':
                logger.info(f"已通过同步请求设置知识库为'无': {config_id}")
            else:
                logger.info(f"已通过同步请求更新知识库配置: {config_id}")
                
            return jsonify({
                'message': f"已通过同步请求设置知识库配置: {config_id}",
                'success': True,
                'config_id': config_id,
                'is_none_config': config_id == 'none'
            }), 200
            
        # 非同步请求，转发到RagManager
        if is_rag_manager_available:
            response = api_session.post(
                f"http://{SERVICE_HOST}:{RAG_MANAGER_PORT}/set_active_configuration",
                json=data,
                timeout=SOCKET_TIMEOUT
            )
            
            # 解析响应
            if response.status_code == 200:
                try:
                    resp_data = response.json()
                    # 更新本地知识库配置状态
                    global current_rag_config_id
                    current_rag_config_id = config_id
                    logger.info(f"已从RagManager成功更新知识库配置: {config_id}")
                    
                    return jsonify(resp_data), 200
                except Exception as parse_err:
                    logger.error(f"解析RagManager响应时出错: {str(parse_err)}")
                    
            # 如果请求失败，返回错误
            return Response(
                response.content,
                status=response.status_code,
                content_type=response.headers.get('content-type', 'application/json')
            )
        else:
            # 如果RagManager不可用
            return jsonify({
                'error': 'RagManager服务不可用，无法设置全局知识库配置',
                'local_update': True,
                'config_id': config_id
            }), 503
            
    except Exception as e:
        logger.error(f'设置知识库配置错误: {str(e)}')
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': '服务器内部错误'}), 500

# 文件上传处理路由
@app.route('/uploadFile', methods=['POST'])
def upload_file():
    """文件上传接口"""
    try:
        file = request.files['file']
        
        # 检查文件大小
        file.seek(0, 2)  # 移动到文件末尾
        file_size = file.tell()  # 获取文件大小
        file.seek(0)  # 重置文件指针到开始
        
        if file_size > 15 * 1024 * 1024:  # 15MB
            return jsonify({'error': '文件过大，请上传小于15MB的文件'}), 413
        
        conversation_id = request.form.get('conversation_id', 'default')
        
        # 初始化对话的文件记录
        if conversation_id not in uploaded_files:
            uploaded_files[conversation_id] = {}
        
        # 读取文件内容
        import io
        content = ""
        try:
            content = file.read().decode('utf-8')
        except UnicodeDecodeError:
            file.seek(0)
            content = file.read().decode('latin-1')
        finally:
            file.seek(0)  # 重置文件指针
        
        # 保存文件信息
        uploaded_files[conversation_id][file.filename] = {
            'name': file.filename,
            'content': content,
            'size': file_size,
            'type': file.content_type,
            'upload_time': time.time()
        }
        
        logger.info(f"文件 '{file.filename}' 已上传, 对话ID: {conversation_id}")
        return jsonify({'message': f"文件 '{file.filename}' 上传成功"}), 200
    except Exception as e:
        logger.error(f'文件上传错误: {str(e)}')
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'error': '服务器内部错误'}), 500

# 文件删除路由
@app.route('/deleteFile', methods=['POST'])
def delete_file():
    """删除上传的文件"""
    try:
        data = request.json
        conversation_id = data.get('conversation_id', 'default')
        file_name = data.get('file_name')
        
        if not file_name:
            return jsonify({'error': '未提供文件名'}), 400
            
        # 检查该对话的文件记录
        if conversation_id not in uploaded_files or file_name not in uploaded_files[conversation_id]:
            # 即使找不到文件也返回成功，因为前端会处理更新
            logger.warning(f"文件不存在，但仍返回成功状态: {file_name}")
            return jsonify({
                'message': f"文件 '{file_name}' 已成功删除",
                'isDeleted': True
            }), 200
        
        # 从上传文件记录中删除文件信息
        del uploaded_files[conversation_id][file_name]
        
        logger.info(f"文件 '{file_name}' 已从对话 {conversation_id} 中删除")
        return jsonify({
            'message': f"文件 '{file_name}' 已成功删除",
            'isDeleted': True
        }), 200
    except Exception as e:
        logger.error(f'删除文件错误: {e}')
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({
            'message': f"文件已标记为删除",
            'isDeleted': True
            }), 200

@app.route('/abort_stream', methods=['POST'])
def abort_stream():
    """处理中止流式输出的请求"""
    try:
        data = request.json
        request_id = data.get('request_id')
        
        if not request_id:
            return jsonify({'error': '缺少请求ID'}), 400
        
        # 初始化中止集合
        if not hasattr(app, 'aborted_streams'):
            app.aborted_streams = set()
        
        # 标记该流式请求为已中止
        app.aborted_streams.add(request_id)
        
        return jsonify({
            'message': f"已标记请求 {request_id} 为中止状态",
            'success': True
        }), 200
    except Exception as e:
        logger.error(f'中止流式输出时出错: {e}')
        return jsonify({
            'error': '中止失败',
            'success': False
        }), 500

@app.route('/add_message', methods=['POST'])
def add_message():
    try:
        # 只读取一次请求数据
        message_data = request.json
        
        # 验证必要字段
        required_fields = ['content', 'sessionId', 'userType', 'userId']
        missing_fields = [field for field in required_fields if field not in message_data]
        
        if missing_fields:
            logger.error(f"消息缺少必要字段: {missing_fields}")
            return jsonify({'error': f"消息缺少必要字段: {missing_fields}"}), 400
        
        session_id = message_data.get('sessionId')
        user_id_int = convert_user_id_to_int(message_data.get('userId', 'default'))
        
        # 先验证会话是否存在
        session_check = proxy_to_original_backend(f"session/get?sessionId={session_id}")
        if not session_check:
            logger.warning(f"尝试向不存在的会话 {session_id} 添加消息，将创建新会话")
            # 创建新会话的逻辑
            new_session = {
                "sessionId": session_id,
                "createTime": datetime.now().isoformat(),
                "header": message_data.get('content', '')[:8] if message_data.get('content') else "新会话",
                "lastActive": datetime.now().isoformat(),
                "modelId": 0,
                "status": 1,
                "userId": user_id_int
            }
            
            create_result = proxy_to_original_backend("session/add", method="POST", data=new_session)
            if create_result is None:
                logger.error(f"无法创建会话 {session_id}")
                return jsonify({"error": "无法创建会话"}), 500
            
            logger.info(f"已创建新会话: {session_id}")
        
        # 确保messageId是整数
        if 'messageId' not in message_data or not isinstance(message_data.get('messageId'), int):
            try:
                if 'messageId' in message_data:
                    message_data['messageId'] = int(message_data['messageId'])
                else:
                    message_data['messageId'] = int(time.time() * 1000)
            except (ValueError, TypeError):
                message_data['messageId'] = int(time.time() * 1000)

        # Java后端字段类型要求：userId必须为Integer
        message_data['userId'] = user_id_int
        
        # 添加时间戳(如果没有)
        if 'timestamp' not in message_data or not message_data['timestamp']:
            message_data['timestamp'] = datetime.now().isoformat()
        
        # 转发到原始后端
        result = proxy_to_original_backend("message/add", method="POST", data=message_data)
        
        if result is not None:
            logger.info(f"成功添加消息 {message_data['messageId']} 到会话 {message_data['sessionId']}")
            return jsonify({'success': True, 'message_id': message_data['messageId']}), 201
        else:
            logger.error("插入消息失败")
            return jsonify({'error': "插入消息失败"}), 500
        
    except Exception as e:
        logger.error(f"添加消息时出错: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/update_session', methods=['POST'])
def update_session():
    """更新会话信息"""
    try:
        session_data = request.json
        
        # 验证必要字段
        if 'sessionId' not in session_data:
            return jsonify({'error': "缺少sessionId字段"}), 400
        
        session_id = session_data['sessionId']
        if 'userId' in session_data:
            session_data['userId'] = convert_user_id_to_int(session_data.get('userId'))
        
        # 添加:检查会话ID格式是否有效
        if not isinstance(session_id, str) or len(session_id) < 5:
            return jsonify({'error': "无效的sessionId格式"}), 400
        
        # 检查会话是否存在
        existing_session = proxy_to_original_backend(f"session/get?sessionId={session_id}")
        
        if not existing_session:
            # 创建新会话
            session_data['createTime'] = session_data.get('createTime', datetime.now().isoformat())
            result = proxy_to_original_backend("session/add", method="POST", data=session_data)
            logger.info(f"创建新会话: {session_id}")
        else:
            # 更新现有会话
            session_data['lastActive'] = datetime.now().isoformat()
            result = proxy_to_original_backend("session/update", method="POST", data=session_data)
            logger.info(f"更新会话: {session_id}")
        
        if result is not None:
            return jsonify({'success': True, 'session_id': session_id}), 200
        else:
            return jsonify({'error': '更新会话失败'}), 500
    
    except Exception as e:
        logger.error(f"更新会话时出错: {str(e)}")
        # 添加更详细的错误信息
        return jsonify({
            'error': str(e), 
            'errorType': type(e).__name__
        }), 500

def analyze_message_type(content):
    """分析消息类型"""
    if '<img' in content or 'src=' in content:
        return 'image'
    if any(keyword in content.lower() for keyword in ['电路', 'circuit', 'spice', '网表']):
        return 'circuit'
    if any(keyword in content.lower() for keyword in ['光刻', 'lithography', '掩模', 'mask']):
        return 'lithography'
    if any(keyword in content.lower() for keyword in ['tcad', '仿真', 'simulation']):
        return 'tcad'
    if any(keyword in content.lower() for keyword in ['文件', 'pdf', '上传', '文档']):
        return 'document'
    return 'text'

def call_title_generation_api(user_message, bot_response, message_type):
    """调用SiliconFlow API生成会话标题"""
    try:
        # 构建提示词
        prompt = f"""请根据以下对话内容生成一个简洁准确的会话标题（8-15个汉字）。

消息类型：{message_type}
用户输入：{user_message[:200]}  # 截取前200个字符
AI回复：{bot_response[:300]}  # 截取前300个字符

要求：
1. 标题要简洁明了，8-15个汉字
2. 准确概括对话主题和内容
3. 如果涉及专业术语，请使用专业术语
4. 如果是图片分析，请体现具体分析类型
5. 只输出标题，不要其他内容

标题："""

        # 准备请求数据
        data = {
            "model": TITLE_MODEL_NAME,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.3,
            "max_tokens": 50,
            "top_p": 0.8
        }
        
        # 发送请求
        import requests
        response = api_session.post(API_URL, json=data, headers=headers, timeout=15)
        
        if response.status_code == 200:
            result = response.json()
            title = result["choices"][0]["message"]["content"].strip()
            
            # 清理标题，只保留核心内容
            title = title.replace('标题：', '').replace('Title:', '').strip()
            
            # 确保标题长度合适
            if len(title) > 15:
                title = title[:15]
            elif len(title) < 3:
                title = generate_fallback_title(user_message, message_type)
                
            logger.info(f"AI生成标题: {title}")
            return title
        else:
            logger.error(f"标题生成API调用失败: {response.status_code}")
            return generate_fallback_title(user_message, message_type)
            
    except Exception as e:
        logger.error(f"生成标题时出错: {str(e)}")
        return generate_fallback_title(user_message, message_type)

def generate_fallback_title(user_message, message_type):
    """降级策略：基于规则生成标题"""
    try:
        # 移除HTML标签
        import re
        clean_content = re.sub(r'<[^>]*>', '', user_message)
        
        # 根据消息类型生成标题
        if message_type == 'image':
            return '图像分析'
        elif message_type == 'circuit':
            return '电路分析'
        elif message_type == 'lithography':
            return '光刻工艺'
        elif message_type == 'tcad':
            return 'TCAD仿真'
        elif message_type == 'document':
            return '文档问答'
        else:
            # 提取关键信息
            if clean_content:
                # 提取第一个完整句子或短语
                sentence = clean_content.split('。')[0].split('？')[0].split('！')[0]
                if len(sentence) <= 12:
                    return sentence
                else:
                    return sentence[:10] + '...'
            else:
                return '技术咨询'
    except:
        return '新对话'

@app.route('/generate_session_title', methods=['POST'])
def generate_session_title():
    """生成会话标题的API端点"""
    try:
        data = request.json
        user_message = data.get('user_message', '')
        bot_response = data.get('bot_response', '')
        message_type = data.get('message_type', 'text')
        
        if not user_message:
            return jsonify({'error': '用户消息不能为空'}), 400
        
        # 分析消息类型
        if not message_type or message_type == 'text':
            message_type = analyze_message_type(user_message)
        
        # 生成标题
        title = call_title_generation_api(user_message, bot_response, message_type)
        
        return jsonify({
            'title': title,
            'message_type': message_type,
            'success': True
        }), 200
        
    except Exception as e:
        logger.error(f"生成会话标题时出错: {str(e)}")
        return jsonify({
            'error': '标题生成失败',
            'title': '新对话',  # 提供默认标题
            'success': False
        }), 500

if __name__ == '__main__':
    # 检查RagManager服务
    is_rag_manager_available = check_rag_manager_availability()
    if not is_rag_manager_available:
        logger.warning("RagManager服务不可用，Chatbot将运行在有限功能模式")
    else:
        logger.info("RagManager服务可用，Chatbot将使用完整功能")
    
    # 同步配置从RagManager - 增加详细日志
    if is_rag_manager_available:
        try:
            import requests
            
            logger.info("开始从RagManager同步知识库配置...")
            response = api_session.get(f"http://{SERVICE_HOST}:{RAG_MANAGER_PORT}/get_rag_configurations", timeout=15)
            if response.status_code == 200:
                data = response.json()
                rag_configurations = {config["id"]: config for config in data.get("configurations", [])}
                logger.info(f"已从RagManager同步 {len(rag_configurations)} 个知识库配置: {[config.get('name') for config in data.get('configurations', [])]}")
            else:
                logger.error(f"从RagManager获取配置失败，状态码: {response.status_code}")
        except Exception as e:
            logger.error(f"从RagManager同步配置时出错: {str(e)}")
    
    logger.info("=" * 50)
    logger.info("Chatbot服务已启动")
    logger.info(f"运行于 http://{SERVICE_HOST}:{CHATBOT_PORT}")
    logger.info("=" * 50)
    
    app.run(debug=False, host=SERVICE_HOST, port=CHATBOT_PORT, threaded=True)
