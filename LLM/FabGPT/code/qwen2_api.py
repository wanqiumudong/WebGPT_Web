from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import requests
from datetime import datetime
import json
import random
import time
import logging
import os

# 多实例配置
INSTANCE_ID = int(os.environ.get('INSTANCE_ID', '1'))
SERVICE_PORT = int(os.environ.get('SERVICE_PORT', '5002'))
ORIGINAL_API_URL = "http://10.98.64.22:8080"

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f"chatbot_instance{INSTANCE_ID}.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(f"Chatbot-Service-Instance{INSTANCE_ID}")
logging.getLogger("werkzeug").setLevel(logging.WARNING)

logger.info(f"初始化Chatbot实例{INSTANCE_ID} - 端口 {SERVICE_PORT}")

def proxy_to_original_backend(original_endpoint, method="GET", data=None, params=None, headers=None):
    """代理请求到原始8080后端"""
    try:
        url = f"{ORIGINAL_API_URL}/{original_endpoint}"
        default_headers = {"Content-Type": "application/json"}
        if headers:
            default_headers.update(headers)
            
        if method.upper() == "GET":
            response = requests.get(url, params=params, headers=default_headers, timeout=10)
        elif method.upper() == "POST":
            response = requests.post(url, json=data, headers=default_headers, timeout=10)
        else:
            return None
            
        if response.status_code >= 200 and response.status_code < 300:
            try:
                json_result = response.json()
                return json_result
            except json.JSONDecodeError:
                # 有些成功的响应可能是空的或非JSON格式
                response_text = response.text
                if not response_text.strip():
                    return {"success": True}  # 空响应视为成功
                return response_text
        else:
            logger.error(f"代理请求失败 - URL: {url}, 状态码: {response.status_code}, 响应: {response.text}")
            return None
    except Exception as e:
        logger.error(f"代理到原始后端时出错: {str(e)}")
        return None

def convert_user_id_to_int(user_id_str):
    """将字符串用户ID转换为整数ID"""
    if isinstance(user_id_str, int):
        return user_id_str
    
    # 如果是纯数字字符串，直接转换
    if user_id_str.isdigit():
        return int(user_id_str)
    
    # 否则使用哈希函数生成一个稳定的整数ID
    import hashlib
    hash_obj = hashlib.md5(user_id_str.encode())
    return int(hash_obj.hexdigest()[:8], 16) % 2147483647  # 限制在32位整数范围内

class MESSAGE_TYPE:
    USER = 'user'
    BOT = 'bot'

app = Flask(__name__)
CORS(app)

# RAG Manager本地服务配置 - 使用负载均衡器
RAG_MANAGER_HOST = "10.98.64.22"
RAG_MANAGER_PORT = 5100  # RAG负载均衡器端口
SOCKET_TIMEOUT = 10
is_rag_manager_available = True  # 默认启用RAG功能，动态检查可用性

# SiliconFlow API配置
API_URL = "https://api.siliconflow.cn/v1/chat/completions"
API_KEY = "sk-irsugzjxawzpmljctfsqjfcwziklolujvvrfznyojlzymksg"
MODEL_NAME = "Qwen/Qwen2.5-72B-Instruct"
TITLE_MODEL_NAME = "Qwen/Qwen2.5-7B-Instruct"

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}"
}

conversation_history = {}
uploaded_files = {}

def get_available_rag_manager_port():
    """返回RAG负载均衡器端口"""
    return RAG_MANAGER_PORT

def check_rag_manager_availability():
    """检查RAG Manager负载均衡器服务是否可用"""
    try:
        response = requests.get(f"http://{RAG_MANAGER_HOST}:{RAG_MANAGER_PORT}/health", timeout=10)
        if response.status_code == 200:
            # 减少日志输出频率，避免大量RAG状态日志
            return True
        else:
            logger.warning(f"RAG Manager负载均衡器响应异常: {response.status_code}")
            return False
    except Exception as e:
        # 减少日志输出频率，只在调试模式下输出
        return False

def get_relevant_context(query, user_id='default', config_id='default'):
    try:
        port = get_available_rag_manager_port()
        url = f"http://{RAG_MANAGER_HOST}:{port}/get_relevant_context"
        
        payload = {
            "query": query,
            "max_tokens": 4000,
            "config_id": config_id,
            "service": "chatbot",
            "user_id": user_id,
            "query_timestamp": int(time.time())
        }
        
        response = requests.post(url, json=payload, timeout=60)
        
        if response.status_code == 200:
            context_data = response.json()
            raw_context = context_data.get("context", "")
            
            if raw_context and "未找到" not in raw_context and "出错" not in raw_context:
                return raw_context
            else:
                return "知识库中未找到相关内容。"
        else:
            return "从知识库检索上下文时出错。"
            
    except Exception as e:
        logger.error(f"get_relevant_context函数出错: {str(e)}")
        return f"访问知识库时出错。"

def generate_with_retry(payload, max_retries=3, stream=False):
    """带重试机制的API调用，支持流式响应"""
    for attempt in range(max_retries):
        try:
            response = requests.post(API_URL, headers=headers, json=payload, stream=stream)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                time.sleep(wait_time)
            else:
                raise
    return None

@app.route('/stream_generate', methods=['POST'])
def stream_generate_response():
    global is_rag_manager_available  # 声明全局变量
    try:
        data = json.loads(request.data)
        user_message = data.get('message', '')
        user_id = data.get('user_id', 'default')
        conversation_id = data.get('conversation_id', user_id)
        config_id = data.get('config_id', 'default')
        
        if not conversation_id or conversation_id.strip() == '':
            return jsonify({"error": "无效的请求标识符"}), 400
        
        # 检查会话是否存在 - 特别处理500状态码（表示不存在）
        session_check = None
        try:
            url = f"{ORIGINAL_API_URL}/session/get?sessionId={conversation_id}"
            response = requests.get(url, headers={"Content-Type": "application/json"}, timeout=10)
            
            if response.status_code == 200:
                session_check = response.json()
            elif response.status_code == 500:
                # Java后端对不存在的会话返回500，这是正常情况
                response_data = response.json()
                if "Session not exists" in response_data.get("message", ""):
                    session_check = None  # 会话不存在，需要创建
                else:
                    logger.error(f"会话检查异常: {response_data}")
                    return jsonify({"error": "会话检查失败"}), 500
            else:
                logger.error(f"会话检查失败 - 状态码: {response.status_code}, 响应: {response.text}")
                return jsonify({"error": "会话检查失败"}), 500
        except Exception as e:
            logger.error(f"会话检查异常: {str(e)}")
            return jsonify({"error": "会话检查失败"}), 500
            
        if not session_check:
            # 转换用户ID为整数类型（Java后端要求）
            user_id_int = convert_user_id_to_int(user_id)
            
            new_session = {
                "sessionId": conversation_id,
                "createTime": datetime.now().isoformat(),
                "header": user_message[:8] if user_message else "新会话",
                "lastActive": datetime.now().isoformat(),
                "modelId": 0,
                "status": 1,
                "userId": user_id_int  # 使用整数ID
            }
            
            logger.info(f"准备创建会话: {new_session}")
            result = proxy_to_original_backend("session/add", method="POST", data=new_session)
            logger.info(f"创建会话结果: {result}")
            if not result:
                logger.error(f"创建会话失败 - 会话ID: {conversation_id}, 用户ID: {user_id} -> {user_id_int}")
                return jsonify({"error": "创建会话失败"}), 500
        
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
        except Exception as e:
            logger.error(f"获取历史消息时出错: {str(e)}")
        
        # RAG功能 - 获取用户活跃配置
        rag_context = ""
        actual_config_id = config_id
        
        # 如果用户没有指定配置或指定为默认，则查询用户的活跃配置
        if config_id == 'default' or not config_id:
            if is_rag_manager_available:
                try:
                    # 向RAG Manager查询用户的活跃配置
                    port = get_available_rag_manager_port()
                    response = requests.get(
                        f"http://{RAG_MANAGER_HOST}:{port}/get_user_active_config?user_id={user_id}",
                        timeout=5
                    )
                    if response.status_code == 200:
                        active_data = response.json()
                        if active_data.get('success') and active_data.get('active_config'):
                            actual_config_id = active_data['active_config']
                            logger.info(f"用户 {user_id} 的活跃配置: {actual_config_id}")
                except Exception as e:
                    logger.warning(f"获取用户活跃配置失败: {str(e)}，使用传入的配置ID")
        
        if actual_config_id != 'none':
            # 只在RAG Manager确实可用时才进行检索，避免频繁的可用性检查
            if is_rag_manager_available:
                try:
                    rag_context = get_relevant_context(
                        query=user_message,
                        user_id=user_id,
                        config_id=actual_config_id
                    )
                    
                    if rag_context and "未找到" not in rag_context and "出错" not in rag_context:
                        logger.info(f"RAG检索成功，获取到上下文长度: {len(rag_context)}")
                    else:
                        rag_context = ""
                except Exception as e:
                    logger.error(f"RAG检索失败: {str(e)}")
                    # 检索失败时暂时禁用RAG，避免后续重复失败
                    is_rag_manager_available = False
                    rag_context = ""

        # 构造消息
        system_prompt = """你是FabGPT，基础模型为General-FabGPT，由浙江大学开发的智能助手。"""

        if rag_context:
            system_prompt += f"\n\n参考以下知识库信息回答用户问题：\n{rag_context}"

        messages = [{"role": "system", "content": system_prompt}]
        
        if messages_history:
            messages.extend(messages_history)
        
        messages.append({"role": "user", "content": user_message})
        
        # API请求
        payload = {
            "model": MODEL_NAME,
            "messages": messages,
            "temperature": 0.7,
            "top_p": 0.5,
            "stream": True
        }
        
        response = generate_with_retry(payload, stream=True)
                
        if not response or response.status_code != 200:
            return jsonify({"error": "API请求失败"}), 500
                    
        def generate():
            request_id = f"{conversation_id}_{str(time.time())}"
            
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
                yield f"data: {json.dumps(start_chunk)}\n\n"
            
            try:
                for line in response.iter_lines():
                    if request_id in app.aborted_streams:
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
                                    except Exception:
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

                
            except GeneratorExit:
                app.aborted_streams.discard(request_id)
                
            except Exception as e:
                error_data = json.dumps({
                    "error": str(e),
                    "is_complete": True,
                    "conversation_id": conversation_id
                })
                yield f"data: {error_data}\n\n"
        
        return Response(generate(), mimetype='text/event-stream')
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/set_active_configuration', methods=['POST'])
def set_active_configuration():
    """设置活跃的知识库配置"""
    try:
        data = request.json
        config_id = data.get('config_id')
        is_sync_request = data.get('is_sync_request', False)
        
        if not config_id:
            return jsonify({'error': '未提供配置ID'}), 400
        
        # 如果是同步请求，只更新本地配置，不再转发到RagManager
        if is_sync_request:
            return jsonify({
                'message': f"已通过同步请求设置知识库配置: {config_id}",
                'success': True,
                'config_id': config_id,
                'is_none_config': config_id == 'none'
            }), 200
            
        # 非同步请求，转发到RagManager
        if is_rag_manager_available:
            response = requests.post(
                f"http://{RAG_MANAGER_HOST}:{RAG_MANAGER_PORT}/set_active_configuration",
                json=data,
                timeout=SOCKET_TIMEOUT
            )
            
            if response.status_code == 200:
                try:
                    resp_data = response.json()
                    global current_rag_config_id
                    current_rag_config_id = config_id
                    return jsonify(resp_data), 200
                except Exception:
                    pass
                    
            return Response(
                response.content,
                status=response.status_code,
                content_type=response.headers.get('content-type', 'application/json')
            )
        else:
            return jsonify({
                'error': 'RagManager服务不可用，无法设置全局知识库配置',
                'local_update': True,
                'config_id': config_id
            }), 503
            
    except Exception as e:
        logger.error(f'设置知识库配置错误: {str(e)}')
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/uploadFile', methods=['POST'])
def upload_file():
    """文件上传接口"""
    try:
        file = request.files['file']
        
        # 检查文件大小
        file.seek(0, 2)
        file_size = file.tell()
        file.seek(0)
        
        if file_size > 15 * 1024 * 1024:
            return jsonify({'error': '文件过大，请上传小于15MB的文件'}), 413
        
        conversation_id = request.form.get('conversation_id', 'default')
        
        if conversation_id not in uploaded_files:
            uploaded_files[conversation_id] = {}
        
        # 读取文件内容
        content = ""
        try:
            content = file.read().decode('utf-8')
        except UnicodeDecodeError:
            file.seek(0)
            content = file.read().decode('latin-1')
        finally:
            file.seek(0)
        
        # 保存文件信息
        uploaded_files[conversation_id][file.filename] = {
            'name': file.filename,
            'content': content,
            'size': file_size,
            'type': file.content_type,
            'upload_time': time.time()
        }
        
        return jsonify({'message': f"文件 '{file.filename}' 上传成功"}), 200
    except Exception as e:
        logger.error(f'文件上传错误: {str(e)}')
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/deleteFile', methods=['POST'])
def delete_file():
    """删除上传的文件"""
    try:
        data = request.json
        conversation_id = data.get('conversation_id', 'default')
        file_name = data.get('file_name')
        
        if not file_name:
            return jsonify({'error': '未提供文件名'}), 400
            
        if conversation_id not in uploaded_files or file_name not in uploaded_files[conversation_id]:
            return jsonify({
                'message': f"文件 '{file_name}' 已成功删除",
                'isDeleted': True
            }), 200
        
        del uploaded_files[conversation_id][file_name]
        
        return jsonify({
            'message': f"文件 '{file_name}' 已成功删除",
            'isDeleted': True
        }), 200
    except Exception as e:
        logger.error(f'删除文件错误: {e}')
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
        
        if not hasattr(app, 'aborted_streams'):
            app.aborted_streams = set()
        
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

@app.route('/get_rag_configurations', methods=['GET'])
def get_rag_configurations():
    """获取RAG配置列表"""
    try:
        if not is_rag_manager_available:
            return jsonify({
                'success': False,
                'error': 'RAG Manager服务不可用',
                'configurations': []
            }), 503
        
        # 使用负载均衡选择RAG Manager实例
        port = get_available_rag_manager_port()
        response = requests.get(
            f"http://{RAG_MANAGER_HOST}:{port}/get_rag_configurations",
            timeout=SOCKET_TIMEOUT
        )
        
        if response.status_code == 200:
            data = response.json()
            return jsonify(data), 200
        else:
            logger.warning(f"RAG Manager(端口{port})返回错误: {response.status_code}")
            return jsonify({
                'success': False,
                'error': f'RAG Manager返回错误: {response.status_code}',
                'configurations': []
            }), response.status_code
            
    except Exception as e:
        logger.error(f'获取RAG配置时出错: {str(e)}')
        return jsonify({
            'success': False,
            'error': str(e),
            'configurations': []
        }), 500

@app.route('/add_message', methods=['POST'])
def add_message():
    try:
        message_data = request.json
        
        required_fields = ['content', 'sessionId', 'userType', 'userId']
        missing_fields = [field for field in required_fields if field not in message_data]
        
        if missing_fields:
            return jsonify({'error': f"消息缺少必要字段: {missing_fields}"}), 400
        
        session_id = message_data.get('sessionId')
        
        # 先验证会话是否存在
        session_check = proxy_to_original_backend(f"session/get?sessionId={session_id}")
        if not session_check:
            new_session = {
                "sessionId": session_id,
                "createTime": datetime.now().isoformat(),
                "header": message_data.get('content', '')[:8] if message_data.get('content') else "新会话",
                "lastActive": datetime.now().isoformat(),
                "modelId": 0,
                "status": 1,
                "userId": message_data.get('userId', 'default')
            }
            
            create_result = proxy_to_original_backend("session/add", method="POST", data=new_session)
            if not create_result:
                return jsonify({"error": "无法创建会话"}), 500
        
        # 确保messageId是整数
        if 'messageId' not in message_data or not isinstance(message_data.get('messageId'), int):
            try:
                if 'messageId' in message_data:
                    message_data['messageId'] = int(message_data['messageId'])
                else:
                    message_data['messageId'] = int(time.time() * 1000)
            except (ValueError, TypeError):
                message_data['messageId'] = int(time.time() * 1000)
        
        if 'timestamp' not in message_data or not message_data['timestamp']:
            message_data['timestamp'] = datetime.now().isoformat()
        
        result = proxy_to_original_backend("message/add", method="POST", data=message_data)
        
        if result:
            return jsonify({'success': True, 'message_id': message_data['messageId']}), 201
        else:
            return jsonify({'error': "插入消息失败"}), 500
        
    except Exception as e:
        logger.error(f"添加消息时出错: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """健康检查接口"""
    try:
        # 检查基础服务状态
        health_status = {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "instance_id": INSTANCE_ID,
            "service_port": SERVICE_PORT,
            "checks": {
                "api_ready": True,
                "backend_connection": False,
                "rag_manager_connection": False
            }
        }
        
        # 检查原始后端连接
        try:
            response = requests.get(f"{ORIGINAL_API_URL}/session/get?sessionId=health_check_test", timeout=5)
            # 只要能收到HTTP响应，说明后端服务正常
            health_status["checks"]["backend_connection"] = response.status_code in [200, 404, 500]
        except Exception:
            health_status["checks"]["backend_connection"] = False
        
        # 检查RAG Manager连接
        try:
            rag_check = check_rag_manager_availability()
            health_status["checks"]["rag_manager_connection"] = rag_check
        except Exception:
            health_status["checks"]["rag_manager_connection"] = False
        
        # 确定整体健康状态
        if not health_status["checks"]["backend_connection"]:
            health_status["status"] = "unhealthy"
            return jsonify(health_status), 503
        
        return jsonify(health_status), 200
        
    except Exception as e:
        logger.error(f"健康检查时出错: {str(e)}")
        return jsonify({
            "status": "unhealthy",
            "timestamp": datetime.now().isoformat(),
            "instance_id": INSTANCE_ID,
            "service_port": SERVICE_PORT,
            "error": str(e)
        }), 503

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

if __name__ == '__main__':
    is_rag_manager_available = check_rag_manager_availability()
    if not is_rag_manager_available:
        logger.warning("RagManager服务不可用，Chatbot将运行在有限功能模式")
    else:
        logger.info("RagManager服务可用，Chatbot将使用完整功能")
    
    if is_rag_manager_available:
        try:
            response = requests.get(f"http://{RAG_MANAGER_HOST}:{RAG_MANAGER_PORT}/get_rag_configurations")
            if response.status_code == 200:
                data = response.json()
                rag_configurations = {config["id"]: config for config in data.get("configurations", [])}
        except Exception as e:
            logger.error(f"从RagManager同步配置时出错: {str(e)}")
    
    print("✅ Chatbot 服务启动完成")
    print(f"📍 服务地址: http://10.98.64.22:{SERVICE_PORT}")
    print("📋 主要端点: /stream_generate")
    print(f"🔗 状态检查: http://10.98.64.22:{SERVICE_PORT}/health")
    
    app.run(debug=False, host='0.0.0.0', port=SERVICE_PORT, threaded=True)