from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from PIL import Image
import numpy as np
import io
import cv2
import os
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

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Defect-Service")

app = Flask(__name__)
CORS(app)  # 允许跨域请求

# 多GPU配置
GPU_ID = int(os.environ.get('GPU_ID', '2'))  # 默认使用GPU 2
SERVICE_PORT = int(os.environ.get('SERVICE_PORT', '5008'))  # 默认端口5008

logger.info(f"初始化Defect服务 - GPU {GPU_ID}, 端口 {SERVICE_PORT}")

# 设置CUDA设备
torch.cuda.set_device(GPU_ID)
os.environ['CUDA_VISIBLE_DEVICES'] = str(GPU_ID)

# init the model
args = {
    'model': 'openllama_peft',
    'imagebind_ckpt_path': '/data/yphu/Web-FabGPT/LLM/FabGPT/pretrained_ckpt/imagebind_ckpt/imagebind_huge.pth',
    'vicuna_ckpt_path': '/data/yphu/Web-FabGPT/LLM/FabGPT/pretrained_ckpt/vicuna_ckpt/7b_v0',
    'anomalygpt_ckpt_path': '/data/yphu/Web-FabGPT/LLM/FabGPT/code/ckpt/pytorch_model.pt',
    # 'anomalygpt_ckpt_path': '/data/yqjiang/project/FabGPT/code/ckpt/train_supervised/pytorch_model.pt',
    'delta_ckpt_path': '/data/yphu/Web-FabGPT/LLM/FabGPT/pretrained_ckpt/pandagpt_ckpt/7b/pytorch_model.pt',
    'stage': 2,
    'max_tgt_len': 128,
    'lora_r': 32,
    'lora_alpha': 32,
    'lora_dropout': 0.1
}

logger.info("加载模型到GPU...")
model = OpenLLAMAPEFTModel(**args)
delta_ckpt = torch.load(args['delta_ckpt_path'], map_location=torch.device('cpu'))
model.load_state_dict(delta_ckpt, strict=False)
delta_ckpt = torch.load(args['anomalygpt_ckpt_path'], map_location=torch.device('cpu'))
model.load_state_dict(delta_ckpt, strict=False)
model = model.eval().half().cuda(GPU_ID)
logger.info("模型加载完成")


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
        pass

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
        
        # 获取上传的文件
        file = request.files['file']

        # 为每个用户和会话创建独立的上传目录
        user_upload_dir = f'/data/Web-FabGPT/LLM/FabGPT/code/uploads/{username}/{conversation_id}'
        if not os.path.exists(user_upload_dir):
            os.makedirs(user_upload_dir)
        
        # 将上传的文件保存到用户会话专属目录
        file_path = os.path.join(user_upload_dir, file.filename)
        file.save(file_path)

        new_file_path = os.path.join(user_upload_dir, 'upload.png')

        # 使用shutil.copyfile复制并重命名图片文件
        shutil.copyfile(file_path, new_file_path)
        
        file_path = [file_path]
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
        backend_url = "http://10.98.64.22:2226"
        image_url = f"{backend_url}/static/upload/{username}/{conversation_id}/processed_output.png"
        original_image_url = f"{backend_url}/static/upload/{username}/{conversation_id}/upload.png"
        
        return jsonify({
            'status': 'success',
            'content': f'<img src="{image_url}" alt="Processed Image" style="max-width: 400px; max-height: 400px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);" />',
            'image_url': image_url,
            'original_image_url': original_image_url
        })

    except Exception as e:
        return jsonify({'error': 'Internal Server Error'}), 500


@app.route('/static/upload/<user_id>/<conversation_id>/<filename>')
def serve_user_upload(user_id, conversation_id, filename):
    """为用户特定会话的上传文件提供静态文件服务"""
    try:
        user_upload_dir = f'/data/Web-FabGPT/LLM/FabGPT/code/uploads/{user_id}/{conversation_id}'
        file_path = os.path.join(user_upload_dir, filename)
        return send_file(file_path)
    except Exception as e:
        return jsonify({'error': 'File not found'}), 404

@app.route('/uploadMessage', methods=['POST'])
def upload_message():
    global user_histories, user_modality_caches
    try:
        # 从请求头中获取用户名和会话ID
        username = request.headers.get('X-Username', 'anonymous')
        conversation_id = request.headers.get('X-Conversation-Id', 'default')
        
        
        # 从请求体中获取消息
        message = request.data.decode('utf-8')

        # 使用用户会话专属的图片路径
        user_upload_dir = f'/data/Web-FabGPT/LLM/FabGPT/code/uploads/{username}/{conversation_id}'
        image_path = os.path.join(user_upload_dir, 'upload.png') if os.path.exists(os.path.join(user_upload_dir, 'upload.png')) else None
        

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
    except Exception as e:
        return jsonify({'error': 'Internal Server Error'}), 500


if __name__ == '__main__':
    # 创建用户上传目录
    upload_dir = f'uploads_gpu{GPU_ID}'
    if not os.path.exists(upload_dir):
        os.makedirs(upload_dir)
    
    logger.info(f"✅ Defect 服务启动完成 - GPU {GPU_ID}")
    logger.info(f"📍 服务地址: http://10.98.193.46:{SERVICE_PORT}")
    logger.info(f"📋 主要端点: /uploadImage, /uploadMessage")
    logger.info(f"🔗 状态检查: http://10.98.193.46:{SERVICE_PORT}/")
    logger.info(f"📁 上传目录: {upload_dir}")
    
    print(f"✅ Defect 服务启动完成 - GPU {GPU_ID}")
    print(f"📍 服务地址: http://10.98.193.46:{SERVICE_PORT}")
    print(f"📋 主要端点: /uploadImage, /uploadMessage")
    print(f"🔗 状态检查: http://10.98.193.46:{SERVICE_PORT}/")
    
    app.run(host='0.0.0.0', port=SERVICE_PORT, threaded=True)
