"""
Defect Detection Service - Datacenter Version
远程GPU服务器部署版本，支持多GPU并行处理
"""

import os

# ⚠️ 重要：必须在导入任何CUDA相关库之前设置CUDA_VISIBLE_DEVICES
GPU_ID = int(os.environ.get('GPU_ID', '0'))
SERVICE_PORT = int(os.environ.get('SERVICE_PORT', '5008'))
INSTANCE_ID = os.environ.get('INSTANCE_ID', f'defect-gpu{GPU_ID}')

# 设置CUDA可见设备（必须在torch导入前）
os.environ['CUDA_VISIBLE_DEVICES'] = str(GPU_ID)

# 现在可以安全地导入CUDA相关库
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from PIL import Image
import numpy as np
import io
import cv2
import requests
import shutil

from model.openllama import OpenLLAMAPEFTModel
import torch
from io import BytesIO
from PIL import Image as PILImage
import cv2
import numpy as np
from matplotlib import pyplot as plt
from torchvision import transforms
import json
import logging
import time

# 配置日志
logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Defect-Service")

app = Flask(__name__)
CORS(app)

logger.info(f"🚀 初始化Defect服务 - GPU {GPU_ID}, 端口 {SERVICE_PORT}, 实例ID: {INSTANCE_ID}")

# 验证CUDA设备设置
if torch.cuda.is_available():
    current_device = torch.cuda.current_device()
    device_count = torch.cuda.device_count()
    logger.info(f"📱 CUDA设备信息: 当前设备={current_device}, 可见设备数量={device_count}")
    logger.info(f"📱 CUDA_VISIBLE_DEVICES={os.environ.get('CUDA_VISIBLE_DEVICES')}")
else:
    logger.error("❌ CUDA不可用！")

# 现在设置torch设备（在CUDA_VISIBLE_DEVICES生效后，这里应该是0）
torch.cuda.set_device(0)

# 服务状态管理
service_stats = {
    'instance_id': INSTANCE_ID,
    'gpu_id': GPU_ID,
    'port': SERVICE_PORT,
    'start_time': time.time(),
    'total_requests': 0,
    'last_request_time': 0,
    'busy': False,
    'model_loaded': False,
    'errors': []
}

# 模型初始化
logger.info("📦 开始加载AI模型...")
try:
    # 模型参数配置
    args = {
        'model': 'openllama_peft',
        'imagebind_ckpt_path': '/data/yphu/Web-FabGPT/LLM/FabGPT/pretrained_ckpt/imagebind_ckpt/imagebind_huge.pth',
        'vicuna_ckpt_path': '/data/yphu/Web-FabGPT/LLM/FabGPT/pretrained_ckpt/vicuna_ckpt/7b_v0',
        'anomalygpt_ckpt_path': '/data/yphu/Web-FabGPT/LLM/FabGPT/code/ckpt/pytorch_model.pt',
        'delta_ckpt_path': '/data/yphu/Web-FabGPT/LLM/FabGPT/pretrained_ckpt/pandagpt_ckpt/7b/pytorch_model.pt',
        'stage': 2,
        'max_tgt_len': 128,
        'lora_r': 32,
        'lora_alpha': 32,
        'lora_dropout': 0.1
    }

    # 加载模型
    model = OpenLLAMAPEFTModel(**args)
    delta_ckpt = torch.load(args['delta_ckpt_path'], map_location=torch.device('cpu'))
    model.load_state_dict(delta_ckpt, strict=False)
    delta_ckpt = torch.load(args['anomalygpt_ckpt_path'], map_location=torch.device('cpu'))
    model.load_state_dict(delta_ckpt, strict=False)
    
    # 使用设备0而不是GPU_ID，因为CUDA_VISIBLE_DEVICES已经将指定GPU映射为设备0
    model = model.eval().half().cuda(0)
    
    service_stats['model_loaded'] = True
    logger.info("✅ 模型加载完成")
    logger.info(f"📱 模型已加载到设备: {next(model.parameters()).device}")
    
except Exception as e:
    logger.error(f"❌ 模型加载失败: {e}")
    service_stats['errors'].append(f"Model loading failed: {str(e)}")

# 用户会话管理 - 按用户+会话隔离
user_histories = {}  # {user_session_key: history}
user_modality_caches = {}  # {user_session_key: modality_cache}

def load_conversation_history_from_db(username, conversation_id):
    """从数据库加载对话历史"""
    try:
        import requests
        
        # 调用后端API获取会话消息
        api_url = f"http://10.98.64.22:8080/message/list-by-session?sessionId={conversation_id}"
        response = requests.get(api_url, timeout=10)
        
        if response.status_code == 200:
            msgs = response.json()
            if msgs:
                logger.info(f"从数据库获取到 {len(msgs)} 条消息")
                
                # 调试：显示消息的modelId分布
                model_ids = [msg.get('modelId') for msg in msgs]
                logger.info(f"消息的modelId分布: {model_ids}")
                
                # 改进的过滤逻辑：FabGPT页面的消息通常modelId为None，但内容特征明确
                defect_msgs = []
                for msg in msgs:
                    model_id = msg.get('modelId')
                    content = msg.get('content', '')
                    
                    if model_id in [1, '1']:
                        # 明确标记为Defect模型的消息（理想情况）
                        defect_msgs.append(msg)
                        logger.info(f"✅ 发现明确Defect消息: modelId={model_id}")
                    elif model_id is None and content:
                        # FabGPT页面的消息通常没有正确的modelId，通过内容智能判断
                        if ('缺陷' in content or 'SEM' in content or 'defect' in content.lower() or 
                            '<img' in content.lower() or '分析' in content or '检测' in content or
                            '扫描电子显微镜' in content or '半导体' in content or 'fabrication' in content.lower()):
                            defect_msgs.append(msg)
                            logger.info(f"🔍 智能判断为Defect消息: content='{content[:30]}...'")
                
                logger.info(f"过滤后得到 {len(defect_msgs)} 条Defect相关消息")
                logger.info(f"🔍 消息类型分布: 明确Defect={sum(1 for msg in msgs if msg.get('modelId') in [1, '1'])}, "
                           f"None值={sum(1 for msg in msgs if msg.get('modelId') is None)}, "
                           f"智能匹配={len(defect_msgs)}")
                
                # 如果还是没有消息，尝试获取所有消息
                if len(defect_msgs) == 0:
                    logger.warning("没有找到modelId=1的消息，尝试加载所有消息")
                    defect_msgs = msgs
                
                # 转换为模型需要的格式: (question, answer) 元组
                history = []
                current_user_msg = None
                
                for msg in defect_msgs:
                    user_type = msg.get('userType')
                    content = msg.get('content', '')
                    
                    logger.info(f"处理消息: userType={user_type} (类型: {type(user_type)}), content长度={len(content)}")
                    
                    if user_type in [1, '1', 'user']:
                        # 用户消息
                        if '<img' in content.lower() or 'http://' in content:
                            current_user_msg = "请分析这张图片"
                        else:
                            # 改进的HTML清理
                            import re
                            # 移除所有HTML标签
                            clean_content = re.sub(r'<[^>]+>', '', content)
                            # 移除多余空白
                            clean_content = re.sub(r'\s+', ' ', clean_content).strip()
                            current_user_msg = clean_content if clean_content else "请分析这张图片"
                            
                    elif user_type in [0, '0', 'bot', 'assistant'] and current_user_msg:
                        # 机器人消息 - 与前面的用户消息配对
                        
                        if '<img' in content.lower():
                            # 提取图像标签外的实际文本内容
                            import re
                            # 移除img标签但保留其他文本
                            text_only = re.sub(r'<img[^>]*/?>', '', content)
                            # 清理其他HTML标签
                            text_only = re.sub(r'<[^>]+>', '', text_only)
                            # 清理多余空白
                            text_only = re.sub(r'\s+', ' ', text_only).strip()
                            
                            if text_only:
                                clean_response = text_only
                                logger.info(f"🔍 从图像消息中提取文本: '{clean_response[:50]}...'")
                            else:
                                clean_response = "图像已成功处理，可以开始缺陷检测对话"
                                logger.info(f"🔍 图像消息无文本内容，使用默认消息")
                        else:
                            # 纯文本消息，清理HTML标签
                            clean_response = content.replace('<br>', '\n').replace('<p>', '').replace('</p>', '')
                            clean_response = clean_response.replace('<div>', '').replace('</div>', '').strip()
                        
                        
                        if current_user_msg and clean_response:
                            # 添加为 (用户输入, 模型输出) 元组
                            history.append((current_user_msg, clean_response))
                            logger.info(f"添加对话对: 用户='{current_user_msg[:30]}...', 助手='{clean_response[:30]}...'")
                        
                        current_user_msg = None  # 重置
                
                # 保持最近8轮对话
                if len(history) > 8:
                    history = history[-8:]
                
                logger.info(f"成功加载对话历史: {len(history)} 轮")
                return history
            else:
                logger.info("数据库中没有找到消息")
                return []
        else:
            logger.warning(f"获取消息失败: HTTP {response.status_code}")
            return []
            
    except Exception as e:
        logger.error(f"从数据库加载对话历史失败: {e}")
        return []

def get_user_session_key(username, conversation_id):
    """生成用户会话唯一键"""
    return f"{username}_{conversation_id}"

def parse_text(text):
    """文本解析函数"""
    lines = text.split("\n")
    lines = [line for line in lines if line != ""]
    count = 0
    for i, line in enumerate(lines):
        if "```" in line:
            count += 1
            items = line.split('`')
            if count % 2 == 1:
                lines[i] = f'<pre><code class="language-{items[-1]}">'
            else:
                lines[i] = f'<br></code></pre>'
        else:
            if i > 0:
                if count % 2 == 1:
                    line = line.replace("`", "\`")
                    line = line.replace("<", "&lt;")
                    line = line.replace(">", "&gt;")
                    line = line.replace(" ", "&nbsp;")
                    line = line.replace("*", "&ast;")
                    line = line.replace("_", "&lowbar;")
                    line = line.replace("-", "&#45;")
                    line = line.replace(".", "&#46;")
                    line = line.replace("!", "&#33;")
                    line = line.replace("(", "&#40;")
                    line = line.replace(")", "&#41;")
                    line = line.replace("$", "&#36;")
                lines[i] = "<br>" + line
    text = "".join(lines)
    return text

def predict(input_text, image_path, normal_img_path, max_length, top_p, temperature, history, modality_cache):
    """缺陷检测预测函数"""
    if image_path is None and normal_img_path is None:
        return "There is no input data provided! Please upload your data and start the conversation."

    # 系统提示词
    system_prompt = """你是FabGPT，基础模型为FabGPT-VL，由浙江大学开发的智能助手。能够精准进行缺陷检测和根因分析"""
    
    # 构建完整提示
    prompt_text = f'System: {system_prompt}\n### '
    
    for idx, (q, a) in enumerate(history):
        if idx == 0:
            prompt_text += f'Human: {q}\n### Assistant: {a}\n###'
        else:
            prompt_text += f' Human: {q}\n### Assistant: {a}\n###'
    
    if len(history) == 0:
        prompt_text += f'Human: {input_text}'
    else:
        prompt_text += f' Human: {input_text}'

    # 模型推理
    response, pixel_output = model.generate({
        'prompt': prompt_text,
        'image_paths': [image_path] if image_path else [],
        'normal_img_paths': [normal_img_path] if normal_img_path else [],
        'audio_paths': [],
        'video_paths': [],
        'thermal_paths': [],
        'top_p': top_p,
        'temperature': temperature,
        'max_tgt_len': max_length,
        'modality_embeds': modality_cache
    }, web_demo=True)
    
    history.append((input_text, response))
    return response

@app.route('/health', methods=['GET'])
def health_check():
    """健康检查端点"""
    current_time = time.time()
    uptime = current_time - service_stats['start_time']
    
    return jsonify({
        'status': 'healthy' if service_stats['model_loaded'] else 'unhealthy',
        'instance_id': service_stats['instance_id'],
        'gpu_id': service_stats['gpu_id'],
        'port': service_stats['port'],
        'busy': service_stats['busy'],
        'model_loaded': service_stats['model_loaded'],
        'total_requests': service_stats['total_requests'],
        'last_request_time': service_stats['last_request_time'],
        'uptime': uptime,
        'timestamp': current_time,
        'errors': service_stats['errors'][-5:] if service_stats['errors'] else []  # 最近5个错误
    })

@app.route('/status', methods=['GET'])
def get_status():
    """状态检查端点（与health相同，但用于负载均衡器）"""
    return health_check()

@app.route('/', methods=['GET'])
def root():
    """根端点 - 服务信息"""
    return jsonify({
        'service': 'FabGPT Defect Detection',
        'version': '2.0',
        'instance_id': service_stats['instance_id'],
        'gpu_id': service_stats['gpu_id'],
        'endpoints': ['/predict', '/uploadImage', '/uploadMessage', '/health', '/status'],
        'status': 'running'
    })

@app.route('/predict', methods=['POST'])
def predict_endpoint():
    """统一预测端点 - 支持图像和文本输入"""
    service_stats['busy'] = True
    service_stats['total_requests'] += 1
    service_stats['last_request_time'] = time.time()
    
    try:
        # 获取用户和会话信息
        username = request.headers.get('X-Username', 'anonymous')
        conversation_id = request.headers.get('X-Conversation-Id', 'default')
        session_key = get_user_session_key(username, conversation_id)
        
        # 初始化会话数据
        if session_key not in user_histories:
            user_histories[session_key] = []
        if session_key not in user_modality_caches:
            user_modality_caches[session_key] = []
        
        logger.info(f"📨 处理请求: 用户={username}, 会话={conversation_id}, GPU={GPU_ID}")
        
        # 处理不同类型的请求
        if request.content_type and 'multipart/form-data' in request.content_type:
            # 图像上传请求
            return handle_image_upload(username, conversation_id, session_key)
        else:
            # 文本消息请求
            return handle_text_message(username, conversation_id, session_key)
            
    except Exception as e:
        logger.error(f"❌ 请求处理失败: {e}")
        service_stats['errors'].append(f"Request failed: {str(e)}")
        return jsonify({'error': 'Internal Server Error', 'message': str(e)}), 500
    finally:
        service_stats['busy'] = False

def handle_image_upload(username, conversation_id, session_key):
    """处理图像上传"""
    try:
        logger.info(f"🔍 文件字段检查: {list(request.files.keys())}")
        logger.info(f"🔍 表单字段检查: {list(request.form.keys())}")
        
        # 获取上传文件
        if 'image' in request.files:
            file = request.files['image']
            logger.info(f"✅ 找到image字段: {file.filename}, 大小: {file.content_length}")
        elif 'file' in request.files:
            file = request.files['file']
            logger.info(f"✅ 找到file字段: {file.filename}, 大小: {file.content_length}")
        else:
            logger.warning(f"❌ 未找到图像文件字段，可用字段: {list(request.files.keys())}")
            return jsonify({'error': 'No image file provided'}), 400
        
        user_input = request.form.get('user_input', '')
        
        # 创建用户专属目录
        user_upload_dir = f'/data/yphu/Web-FabGPT/LLM/FabGPT/code/uploads/{username}/{conversation_id}'
        if not os.path.exists(user_upload_dir):
            os.makedirs(user_upload_dir)
        
        # 保存上传文件
        file_path = os.path.join(user_upload_dir, file.filename)
        file.save(file_path)
        
        # 标准化文件名
        new_file_path = os.path.join(user_upload_dir, 'upload.png')
        shutil.copyfile(file_path, new_file_path)
        
        # 提取多模态特征
        file_paths = [file_path]
        mask = model.extract_multimodal_feature_new(file_paths)
        
        # 生成缺陷可视化
        import matplotlib
        matplotlib.use('Agg')
        
        plt.figure()
        plt.imshow(mask.to(torch.float16).reshape(224, 224).detach().cpu(), cmap='binary_r')
        plt.axis('off')
        temp_output_path = os.path.join(user_upload_dir, 'temp_output.png')
        plt.savefig(temp_output_path, bbox_inches='tight', pad_inches=0)
        plt.close()
        
        # 图像后处理
        target_size = 224
        original_width, original_height = PILImage.open(file_path).size
        if original_width > original_height:
            new_width = target_size
            new_height = int(target_size * (original_height / original_width))
        else:
            new_height = target_size
            new_width = int(target_size * (original_width / original_height))
        
        new_image = PILImage.new('L', (target_size, target_size), 255)
        paste_x = (target_size - new_width) // 2
        paste_y = (target_size - new_height) // 2
        
        pixel_output = PILImage.open(temp_output_path).resize((new_width, new_height), PILImage.LANCZOS)
        new_image.paste(pixel_output, (paste_x, paste_y))
        
        temp_resized_path = os.path.join(user_upload_dir, 'temp_resized.png')
        new_image.save(temp_resized_path)
        
        # 形态学处理
        image = cv2.imread(temp_resized_path, cv2.IMREAD_GRAYSCALE)
        kernel = np.ones((3, 3), np.uint8)
        eroded_image = cv2.erode(image, kernel, iterations=1)
        
        output_image_path = os.path.join(user_upload_dir, 'processed_output.png')
        cv2.imwrite(output_image_path, eroded_image)
        
        # 上传新图片时清空历史记录，恢复备份版本的逻辑
        user_histories[session_key] = []  
        user_modality_caches[session_key] = []  
        
        logger.info(f"🗑️ 上传新图片，已清空用户 {username} 会话 {conversation_id} 的历史记录")
        
        # 返回处理结果
        backend_url = f"http://10.98.193.46:{SERVICE_PORT}"
        image_url = f"{backend_url}/static/upload/{username}/{conversation_id}/processed_output.png"
        original_image_url = f"{backend_url}/static/upload/{username}/{conversation_id}/upload.png"
        
        logger.info(f"✅ 图像处理完成: {username}/{conversation_id}")
        
        return jsonify({
            'status': 'success',
            'content': f'<img src="{image_url}" alt="Processed Image" style="max-width: 400px; max-height: 400px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);" />',
            'image_url': image_url,
            'original_image_url': original_image_url,
            'message': '图像已成功处理，可以开始缺陷检测对话'
        })
        
    except Exception as e:
        logger.error(f"❌ 图像处理失败: {e}")
        raise

def handle_text_message(username, conversation_id, session_key):
    """处理文本消息"""
    try:
        # 简化消息提取，使用备份版本的直接方法
        message = request.data.decode('utf-8')
        logger.info(f"收到消息: '{message[:30]}...' 用户: {username}")
        
        # 获取用户图像路径
        user_upload_dir = f'/data/yphu/Web-FabGPT/LLM/FabGPT/code/uploads/{username}/{conversation_id}'
        image_path = os.path.join(user_upload_dir, 'upload.png') if os.path.exists(
            os.path.join(user_upload_dir, 'upload.png')) else None
        
        # 使用简单的内存历史管理，就像备份版本
        if session_key not in user_histories:
            user_histories[session_key] = []
        if session_key not in user_modality_caches:
            user_modality_caches[session_key] = []
        
        logger.info(f"内存历史: {len(user_histories[session_key])} 轮对话")
        
        # 推理参数
        normal_img_path = None
        max_length = 512
        top_p = 0.01
        temperature = 1.0
        
        # 执行预测 - 使用内存中的历史记录，就像备份版本
        response = predict(
            message,
            image_path,
            normal_img_path,
            max_length,
            top_p,
            temperature,
            user_histories[session_key],  # 使用内存历史
            user_modality_caches[session_key],  # 使用内存模态缓存
        )
        
        logger.info(f"✅ 文本推理完成: {username}/{conversation_id}")
        
        return jsonify({
            'message': response,
            'response': response,  # 兼容性字段
            'username': username,
            'status': 'success'
        })
        
    except Exception as e:
        logger.error(f"❌ 文本处理失败: {e}")
        raise

@app.route('/uploadImage', methods=['POST'])
def upload_image():
    """图像上传端点（向后兼容）"""
    return predict_endpoint()

@app.route('/uploadMessage', methods=['POST'])
def upload_message():
    """消息上传端点（向后兼容）"""
    return predict_endpoint()

@app.route('/static/upload/<user_id>/<conversation_id>/<filename>')
def serve_user_upload(user_id, conversation_id, filename):
    """静态文件服务"""
    try:
        user_upload_dir = f'/data/yphu/Web-FabGPT/LLM/FabGPT/code/uploads/{user_id}/{conversation_id}'
        file_path = os.path.join(user_upload_dir, filename)
        return send_file(file_path)
    except Exception as e:
        logger.error(f"❌ 文件服务失败: {e}")
        return jsonify({'error': 'File not found'}), 404

@app.route('/clear_history', methods=['POST'])
def clear_conversation_history():
    """手动清空对话历史记录"""
    try:
        # 获取用户和会话信息
        username = request.headers.get('X-Username', 'anonymous')
        conversation_id = request.headers.get('X-Conversation-Id', 'default')
        session_key = get_user_session_key(username, conversation_id)
        
        # 清空内存中的历史记录
        if session_key in user_histories:
            old_count = len(user_histories[session_key])
            user_histories[session_key] = []
            logger.info(f"🗑️ 已清空用户 {username} 会话 {conversation_id} 的历史记录: {old_count} 轮")
        
        # 可选：也清空模态缓存
        clear_modality = request.json.get('clear_modality_cache', False) if request.json else False
        if clear_modality and session_key in user_modality_caches:
            user_modality_caches[session_key] = []
            logger.info(f"🧠 已清空用户 {username} 的模态缓存")
        
        return jsonify({
            'status': 'success',
            'message': f'已清空会话历史记录',
            'username': username,
            'conversation_id': conversation_id,
            'cleared_modality_cache': clear_modality
        })
        
    except Exception as e:
        logger.error(f"清空历史记录失败: {e}")
        return jsonify({
            'status': 'error',
            'message': f'清空历史记录失败: {str(e)}'
        }), 500

@app.route('/get_history_info', methods=['GET'])
def get_history_info():
    """获取当前会话的历史记录信息"""
    try:
        username = request.headers.get('X-Username', 'anonymous')
        conversation_id = request.headers.get('X-Conversation-Id', 'default')
        session_key = get_user_session_key(username, conversation_id)
        
        memory_history_count = len(user_histories.get(session_key, []))
        has_modality_cache = len(user_modality_caches.get(session_key, []))  > 0
        
        # 尝试从数据库获取历史记录数量
        try:
            db_history = load_conversation_history_from_db(username, conversation_id)
            db_history_count = len(db_history) if db_history else 0
        except:
            db_history_count = 0
        
        return jsonify({
            'status': 'success',
            'username': username,
            'conversation_id': conversation_id,
            'memory_history_count': memory_history_count,
            'db_history_count': db_history_count,
            'has_modality_cache': has_modality_cache,
            'gpu_id': GPU_ID,
            'instance_id': INSTANCE_ID
        })
        
    except Exception as e:
        logger.error(f"获取历史信息失败: {e}")
        return jsonify({
            'status': 'error',
            'message': f'获取历史信息失败: {str(e)}'
        }), 500

if __name__ == '__main__':
    
    logger.info(f"✅ Defect 服务启动完成 - GPU {GPU_ID}")
    logger.info(f"📍 服务地址: http://10.98.193.46:{SERVICE_PORT}")
    logger.info(f"📋 主要端点: /predict, /uploadImage, /uploadMessage")
    logger.info(f"🔗 状态检查: http://10.98.193.46:{SERVICE_PORT}/health")
    
    print(f"✅ Defect 服务启动完成 - GPU {GPU_ID}")
    print(f"📍 服务地址: http://10.98.193.46:{SERVICE_PORT}")
    print(f"📋 主要端点: /predict, /uploadImage, /uploadMessage")
    print(f"🔗 状态检查: http://10.98.193.46:{SERVICE_PORT}/health")
    
    app.run(host='0.0.0.0', port=SERVICE_PORT, threaded=True)