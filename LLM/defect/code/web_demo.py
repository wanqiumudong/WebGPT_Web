from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from PIL import Image
import numpy as np
import io
import cv2
import os
import requests
import shutil
from pathlib import Path
import threading

from model.openllama import OpenLLAMAPEFTModel
import torch
from io import BytesIO
from PIL import Image as PILImage
import cv2
import numpy as np
from matplotlib import pyplot as plt
from torchvision import transforms
import json

app = Flask(__name__)
CORS(app)  # 允许跨域请求

# 端口与对外地址配置（默认与前端 /predict 调用保持一致）
SERVICE_HOST = os.getenv('FABGPT_HOST', '0.0.0.0')
SERVICE_PORT = int(os.getenv('FABGPT_PORT', '5002'))
PUBLIC_BASE_URL = os.getenv('FABGPT_PUBLIC_BASE_URL', '').rstrip('/')
MODEL_PARALLEL = os.getenv('FABGPT_MODEL_PARALLEL', '1').lower() in ['1', 'true', 'yes', 'on']
MAIN_DEVICE = os.getenv('FABGPT_MAIN_DEVICE', 'cuda:0')
CODE_ROOT = Path(__file__).resolve().parent
DEFECT_ROOT = CODE_ROOT.parent
UPLOADS_ROOT = CODE_ROOT / 'uploads'
DEFECT_MAX_CONCURRENT = max(1, int(os.getenv('WEB_FABGPT_DEFECT_MAX_CONCURRENT', '1')))
_defect_gate = threading.BoundedSemaphore(DEFECT_MAX_CONCURRENT)
_defect_state_lock = threading.Lock()
_defect_active_jobs = 0


class DefectBusyError(RuntimeError):
    pass


class DefectJobSlot:
    def __init__(self, timeout_s: int = 900) -> None:
        self.timeout_s = timeout_s

    def __enter__(self):
        global _defect_active_jobs
        acquired = _defect_gate.acquire(timeout=self.timeout_s)
        if not acquired:
            raise DefectBusyError('defect service is busy, please retry later')
        with _defect_state_lock:
            _defect_active_jobs += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        global _defect_active_jobs
        with _defect_state_lock:
            _defect_active_jobs = max(0, _defect_active_jobs - 1)
        _defect_gate.release()
        return False

# init the model
args = {
    'model': 'openllama_peft',
    'imagebind_ckpt_path': str(DEFECT_ROOT / 'pretrained_ckpt' / 'imagebind_ckpt' / 'imagebind_huge.pth'),
    'vicuna_ckpt_path': str(DEFECT_ROOT / 'pretrained_ckpt' / 'vicuna_ckpt' / '7b_v0'),
    'anomalygpt_ckpt_path': str(CODE_ROOT / 'ckpt' / 'pytorch_model.pt'),
    # 'anomalygpt_ckpt_path': '/data/yqjiang/project/FabGPT/code/ckpt/train_supervised/pytorch_model.pt',
    'delta_ckpt_path': str(DEFECT_ROOT / 'pretrained_ckpt' / 'pandagpt_ckpt' / '7b' / 'pytorch_model.pt'),
    'stage': 2,
    'max_tgt_len': 128,
    'lora_r': 32,
    'lora_alpha': 32,
    'lora_dropout': 0.1,
    'model_parallel': MODEL_PARALLEL,
    'llm_device_map': os.getenv('FABGPT_LLM_DEVICE_MAP', 'auto'),
    'main_device': MAIN_DEVICE
}

model = OpenLLAMAPEFTModel(**args)
delta_ckpt = torch.load(args['delta_ckpt_path'], map_location=torch.device('cpu'))
model.load_state_dict(delta_ckpt, strict=False)
delta_ckpt = torch.load(args['anomalygpt_ckpt_path'], map_location=torch.device('cpu'))
model.load_state_dict(delta_ckpt, strict=False)
if MODEL_PARALLEL:
    print('[Startup] FABGPT_MODEL_PARALLEL=1, use multi-GPU sharding when available.')
    model = model.eval().half()
else:
    print(f'[Startup] FABGPT_MODEL_PARALLEL=0, use single device: {MAIN_DEVICE}')
    model = model.eval().half().to(MAIN_DEVICE)


# def add_gaussian_noise(image):
#     # 转换为 numpy 数组
#     img_array = np.array(image)
#
#     # 添加高斯噪声
#     noise = np.random.normal(0, 1, img_array.shape)
#     noisy_image = img_array + noise * 50  # 根据需要调整噪声强度
#
#     # 将值限制在 0 到 255 之间
#     noisy_image = np.clip(noisy_image, 0, 255)
#
#     # 转换回 PIL.Image 对象
#     noisy_image = Image.fromarray(np.uint8(noisy_image))
#
#     return noisy_image

user_histories = {}  # {username: history}
user_modality_caches = {}  # {username: modality_cache}

def parse_text(text):
    """copy from https://github.com/GaiZhenbiao/ChuanhuChatGPT/"""
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



def predict(
        input,
        image_path,
        normal_img_path,
        max_length,
        top_p,
        temperature,
        history,
        modality_cache,
):
    if image_path is None and normal_img_path is None:
        # return [(input, "There is no input data provided! Please upload your data and start the conversation.")]
        return [("There is no input data provided! Please upload your data and start the conversation.")]
    else:
        print(f'[!] image path: {image_path}\n[!] normal image path: {normal_img_path}\n')

    # 定义system prompt
    system_prompt = """你是FabGPT，基础模型为FabGPT-VL，由浙江大学开发的智能助手。能够精准进行缺陷检测和根因分析"""
    
    # prepare the prompt with system prompt
    prompt_text = f'System: {system_prompt}\n### '
    
    for idx, (q, a) in enumerate(history):
        if idx == 0:
            prompt_text += f'Human: {q}\n### Assistant: {a}\n###'
        else:
            prompt_text += f' Human: {q}\n### Assistant: {a}\n###'
    
    if len(history) == 0:
        prompt_text += f'Human: {input}'
    else:
        prompt_text += f' Human: {input}'

    print(f'[!] Final prompt: {prompt_text}')  # 调试用，可以看到最终的prompt结构

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
    
    history.append((input, response))
    return response
# def predict(
#         input,
#         image_path,
#         normal_img_path,
#         max_length,
#         top_p,
#         temperature,
#         history,
#         modality_cache,
# ):
#     if image_path is None and normal_img_path is None:
#         return [(input, "There is no input data provided! Please upload your data and start the conversation.")]
#     else:
#         print(f'[!] image path: {image_path}\n[!] normal image path: {normal_img_path}\n')

#     # prepare the prompt
#     prompt_text = ''
#     for idx, (q, a) in enumerate(history):
#         if idx == 0:
#             prompt_text += f'{q}\n### Assistant: {a}\n###'
#         else:
#             prompt_text += f' Human: {q}\n### Assistant: {a}\n###'
#     if len(history) == 0:
#         prompt_text += f'{input}'
#     else:
#         prompt_text += f' Human: {input}'

#     response, pixel_output = model.generate({
#         'prompt': prompt_text,
#         'image_paths': [image_path] if image_path else [],
#         'normal_img_paths': [normal_img_path] if normal_img_path else [],
#         'audio_paths': [],
#         'video_paths': [],
#         'thermal_paths': [],
#         'top_p': top_p,
#         'temperature': temperature,
#         'max_tgt_len': max_length,
#         'modality_embeds': modality_cache
#     }, web_demo=True)
#     history.append((input, response))

#     return response


@app.route('/uploadImage', methods=['POST'])
def upload_image():
    global user_histories, user_modality_caches
    try:
        # 获取用户名和会话ID
        username = request.headers.get('X-Username', 'anonymous')
        conversation_id = request.headers.get('X-Conversation-Id', 'default')
        
        print(f"[!] FabGPT接收到文件上传请求 - 用户: {username}, 会话ID: {conversation_id}")
        
        # 兼容两种字段名：旧版使用 file，新版前端使用 image
        file = request.files.get('file') or request.files.get('image')
        if file is None:
            return jsonify({'error': 'No file uploaded'}), 400

        # 为每个用户和会话创建独立的上传目录
        user_upload_dir = os.path.join(str(UPLOADS_ROOT), username, conversation_id)
        if not os.path.exists(user_upload_dir):
            os.makedirs(user_upload_dir)
        
        # 将上传的文件保存到用户会话专属目录
        file_path = os.path.join(user_upload_dir, file.filename)
        file.save(file_path)

        new_file_path = os.path.join(user_upload_dir, 'upload.png')

        # 使用shutil.copyfile复制并重命名图片文件
        shutil.copyfile(file_path, new_file_path)
        
        print(f"[!] 文件保存路径: {file_path}")
        print(f"[!] 处理文件路径: {new_file_path}")

        file_path = [file_path]
        with DefectJobSlot():
            mask = model.extract_multimodal_feature_new(file_path)

        # 修复matplotlib线程问题
        import matplotlib
        matplotlib.use('Agg')  # 使用非交互式后端
        
        plt.figure()  # 创建新图形
        plt.imshow(mask.to(torch.float16).reshape(224, 224).detach().cpu(), cmap='binary_r')
        plt.axis('off')
        temp_output_path = os.path.join(user_upload_dir, 'temp_output.png')
        plt.savefig(temp_output_path, bbox_inches='tight', pad_inches=0)
        plt.close()  # 关闭图形，释放内存

        target_size = 224
        original_width, original_height = PILImage.open(file_path[0]).size
        if original_width > original_height:
            new_width = target_size
            new_height = int(target_size * (original_height / original_width))
        else:
            new_height = target_size
            new_width = int(target_size * (original_width / original_height))

        new_image = PILImage.new('L', (target_size, target_size), 255)  # 'L' mode for grayscale

        paste_x = (target_size - new_width) // 2
        paste_y = (target_size - new_height) // 2

        pixel_output = PILImage.open(temp_output_path).resize((new_width, new_height), PILImage.LANCZOS)

        new_image.paste(pixel_output, (paste_x, paste_y))

        temp_resized_path = os.path.join(user_upload_dir, 'temp_resized.png')
        new_image.save(temp_resized_path)

        image = cv2.imread(temp_resized_path, cv2.IMREAD_GRAYSCALE)
        kernel = np.ones((3, 3), np.uint8)
        eroded_image = cv2.erode(image, kernel, iterations=1)
        
        # 将处理后的图片保存到用户特定目录
        output_image_path = os.path.join(user_upload_dir, 'processed_output.png')
        cv2.imwrite(output_image_path, eroded_image)

        mask = PILImage.open(output_image_path).convert('L')

        # 清空该用户的历史记录
        if username in user_histories:
            user_histories[username] = []
        if username in user_modality_caches:
            user_modality_caches[username] = []

        # 返回HTML格式的图片数据，包含持久化的URL路径
        # 优先使用显式配置，其次使用当前请求Host自动推断
        backend_url = PUBLIC_BASE_URL or request.host_url.rstrip('/')
        image_url = f"{backend_url}/static/upload/{username}/{conversation_id}/processed_output.png"
        original_image_url = f"{backend_url}/static/upload/{username}/{conversation_id}/upload.png"
        
        return jsonify({
            'status': 'success',
            'content': f'<img src="{image_url}" alt="Processed Image" style="max-width: 400px; max-height: 400px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);" />',
            'image_url': image_url,
            'original_image_url': original_image_url
        })

    except DefectBusyError as e:
        return jsonify({'error': str(e), 'status': 'busy'}), 429
    except Exception as e:
        print('Error:', e)
        return jsonify({'error': 'Internal Server Error'}), 500


@app.route('/static/upload/<user_id>/<conversation_id>/<filename>')
def serve_user_upload(user_id, conversation_id, filename):
    """为用户特定会话的上传文件提供静态文件服务"""
    try:
        user_upload_dir = os.path.join(str(UPLOADS_ROOT), user_id, conversation_id)
        file_path = os.path.join(user_upload_dir, filename)
        print(f"[!] 静态文件服务请求: {file_path}")
        return send_file(file_path)
    except Exception as e:
        print(f'Error serving file: {e}')
        return jsonify({'error': 'File not found'}), 404

@app.route('/uploadMessage', methods=['POST'])
def upload_message():
    global user_histories, user_modality_caches
    try:
        # 从请求头中获取用户名和会话ID
        username = request.headers.get('X-Username', 'anonymous')
        conversation_id = request.headers.get('X-Conversation-Id', 'default')
        
        print(f"[!] FabGPT接收到消息请求 - 用户: {username}, 会话ID: {conversation_id}")
        
        # 从请求体中获取消息（兼容纯文本与JSON）
        if request.is_json:
            payload = request.get_json(silent=True) or {}
            message = payload.get('message') or payload.get('user_input') or ''
        else:
            message = request.data.decode('utf-8')

        # 使用用户会话专属的图片路径
        user_upload_dir = os.path.join(str(UPLOADS_ROOT), username, conversation_id)
        image_path = os.path.join(user_upload_dir, 'upload.png') if os.path.exists(os.path.join(user_upload_dir, 'upload.png')) else None
        
        print(f"[!] 查找图片路径: {image_path}")
        print(f"[!] 图片是否存在: {os.path.exists(image_path) if image_path else False}")

        normal_img_path = None
        max_length = 512
        top_p = 0.01
        temperature = 1.0
        
        # 使用用户+会话的组合键来管理历史记录
        history_key = f"{username}_{conversation_id}"
        if history_key not in user_histories:
            user_histories[history_key] = []
        if history_key not in user_modality_caches:
            user_modality_caches[history_key] = []

        with DefectJobSlot():
            message = predict(
                message,
                image_path,
                normal_img_path,
                max_length,
                top_p,
                temperature,
                user_histories[history_key],
                user_modality_caches[history_key],
            )

        # 返回响应给前端
        return jsonify({'message': message, 'username': username})
    except DefectBusyError as e:
        return jsonify({'error': str(e), 'status': 'busy'}), 429
    except Exception as e:
        print('Error:', e)  # 添加调试语句，打印错误信息
        return jsonify({'error': 'Internal Server Error'}), 500


@app.route('/predict', methods=['POST'])
def predict_compat():
    """
    兼容前端统一入口：
    - 有文件时走图片上传流程
    - 无文件时走文本对话流程
    """
    if request.files.get('file') or request.files.get('image'):
        return upload_image()
    return upload_message()


@app.route('/health', methods=['GET'])
def health_check():
    with _defect_state_lock:
        active_jobs = _defect_active_jobs
    return jsonify({
        'status': 'ok',
        'service': 'web_demo',
        'max_concurrent': DEFECT_MAX_CONCURRENT,
        'active_jobs': active_jobs,
        'main_device': MAIN_DEVICE,
    })


if __name__ == '__main__':
    UPLOADS_ROOT.mkdir(parents=True, exist_ok=True)
    app.run(host=SERVICE_HOST, port=SERVICE_PORT)
