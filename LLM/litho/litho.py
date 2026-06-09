# 统一的文件保存空间
import warnings
warnings.filterwarnings("ignore")
from flask import Flask, request, jsonify, send_file, send_from_directory, redirect, Response, stream_with_context
from flask_cors import CORS
from PIL import Image
import numpy as np
import io
import cv2
import os
import requests
import shutil

# from model.openllama import OpenLLAMAPEFTModel
import torch
from io import BytesIO
from PIL import Image as PILImage
import cv2
import numpy as np
from matplotlib import pyplot as plt
from torchvision import transforms
import json
import shlex
import time
import threading
from litho_code.parser.parser import parse_and_dispatch, documents
from litho_code.parser.llm_parser import analyze_user_intent, get_action_and_params
from litho_code.config.settings import get_upload_image_path, get_output_image_path, get_backend_url, get_backend_ip, get_backend_port
from litho_code.utils.utils import unzip_file, check_file_type_by_suffix

# 导入流式API所需函数
from litho_code.parser.stream_api import (
    analyze_user_intent_streaming,
    get_action_and_params_streaming,
    get_command_from_streaming_response
)

app = Flask(__name__)
CORS(app)  # 允许跨域请求

# output_folder = '/data/Web-FabGPT/LLM/litho/litho_code/output_image'  # 替换为你的输出文件夹路径
# output_folder = get_output_image_path()
app.config['OUTPUT_FOLDER'] = get_output_image_path()
app.config['UPLOAD_FOLDER'] = get_upload_image_path()

LITHO_PROVIDER = os.getenv("WEB_FABGPT_LITHO_PROVIDER", "siliconflow")
LITHO_MODEL = os.getenv("WEB_FABGPT_LITHO_LLM_MODEL", os.getenv("WEB_FABGPT_VL_MODEL", "Qwen/Qwen2.5-VL-72B-Instruct"))
LITHO_CPU_WORKERS = max(1, int(os.getenv("WEB_FABGPT_LITHO_CPU_WORKERS", "2")))
LITHO_GPU_WORKERS = max(1, int(os.getenv("WEB_FABGPT_LITHO_GPU_WORKERS", "1")))
_litho_cpu_gate = threading.BoundedSemaphore(LITHO_CPU_WORKERS)
_litho_gpu_gate = threading.BoundedSemaphore(LITHO_GPU_WORKERS)
_litho_scheduler_lock = threading.Lock()
_litho_running = {"cpu": 0, "gpu": 0}
_litho_waiting = {"cpu": 0, "gpu": 0}


class LithoBusyError(RuntimeError):
    pass


class LithoJobSlot:
    def __init__(self, pool: str, timeout_s: int = 7200) -> None:
        self.pool = pool
        self.timeout_s = timeout_s
        self.queue_position = 0
        self._gate = _litho_gpu_gate if pool == "gpu" else _litho_cpu_gate
        with _litho_scheduler_lock:
            pool_limit = LITHO_GPU_WORKERS if self.pool == "gpu" else LITHO_CPU_WORKERS
            if _litho_running[self.pool] >= pool_limit:
                self.queue_position = _litho_waiting[self.pool] + 1

    def __enter__(self):
        with _litho_scheduler_lock:
            if self.queue_position > 0:
                _litho_waiting[self.pool] += 1
        acquired = self._gate.acquire(timeout=self.timeout_s)
        if not acquired:
            with _litho_scheduler_lock:
                if self.queue_position > 0:
                    _litho_waiting[self.pool] = max(0, _litho_waiting[self.pool] - 1)
            raise LithoBusyError(f"litho {self.pool} worker pool is busy")
        with _litho_scheduler_lock:
            if self.queue_position > 0:
                _litho_waiting[self.pool] = max(0, _litho_waiting[self.pool] - 1)
            _litho_running[self.pool] += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        with _litho_scheduler_lock:
            _litho_running[self.pool] = max(0, _litho_running[self.pool] - 1)
        self._gate.release()
        return False


def _resolve_litho_pool(instructions):
    normalized = [str(item).strip().lower() for item in instructions]
    if normalized and normalized[0] == "optimize" and "neural_ilt" in normalized:
        return "gpu"
    return "cpu"



# @app.route('/uploadImage', methods=['POST'])
@app.route('/uploadFile', methods=['POST'])
def upload_image():
    global history, modality_cache
    # upload_image_save_path = '/data/Web-FabGPT/LLM/litho/litho_code/upload_image'
    upload_image_save_path = get_upload_image_path()
    print("enter backend")
    try:
        # 获取上传的文件
        file = request.files['file']

        file_type = check_file_type_by_suffix(file.filename)
        print(file_type)
        if file_type in ["glp", "png"]:
            file_path = os.path.join(upload_image_save_path, file.filename)
            file.save(file_path)
            if file_type == "png":
                backend_url = get_backend_url()
                image_url = f"{backend_url}/static/upload/" + os.path.basename(file_path)
                output = f'<img src="{image_url}" alt="image" width="300" height="300"> \n'
                print(output)
                return jsonify({"role": "assistant", "content": output}), 200
            elif file_type == "glp":
                return jsonify({'message': 'Upload Success'}), 200
        
        elif file_type == "zip":
            zip_file_path = os.path.join(upload_image_save_path, file.filename)
            file.save(zip_file_path)
            extract_path = os.path.join(upload_image_save_path)
            unzip_file(zip_file_path, extract_path)
            os.remove(zip_file_path)
        else:
            return jsonify({'error': 'File Type Error'}), 500
        
        return jsonify({'message': 'Upload Success'}), 200

    except Exception as e:
        print('Error:', e)
        return jsonify({'error': 'Internal Server Error'}), 500


# @app.route('/uploadMessage', methods=['POST'])
@app.route('/generate', methods=['POST'])
def upload_message():
    global history, modality_cache
    print("enter backend message")
    # return jsonify({"role": "assistant", "content": "response"})
    try:
        # 从请求头中获取用户名
        data = json.loads(request.data)
        message = data.get('message', '')
        username = data.get('user_id', 'default')
        # username = request.headers.get('X-Username', '')
        
        # # 从请求体中获取JSON数据
        # data = json.loads(request.data.decode('utf-8'))
        # message = data.get('message', '')

        # image_content = download_image(imageUrl)

        # 从请求体中获取消息
        # message = request.data.decode('utf-8')
        
        
        
        print(username)
        print(message)
        analysis = analyze_user_intent(message)
        print(f"Analysis: {analysis}")
        llm_output_instructions = get_action_and_params(message, analysis)
        print(f"llm_output_instructions(need to be string)  {llm_output_instructions} ")
        # instructions=shlex.split(message)
        if llm_output_instructions != "":
            
            instructions = shlex.split(llm_output_instructions)

            print(instructions)
            pool = _resolve_litho_pool(instructions)
            with LithoJobSlot(pool):
                litho_output = parse_and_dispatch(instructions, test=False)

            print(litho_output)
            
            combined_instruction_litho_output = f"""
## 使用如下指令

{llm_output_instructions}

## 光刻输出结果

{litho_output}
"""
        else:
            combined_instruction_litho_output = ""
        
        
        
        # output=f"{analysis}\n{llm_output_instructions}\n{litho_output}"
        output = f"""
## 分析结果

{analysis}

{combined_instruction_litho_output}
"""

        return jsonify({"role": "assistant", "content": output})
        


        # # 将消息翻转
        # reversed_message = message[::-1]

        # 返回响应给前端
        # return jsonify({'message': message, 'username': username})
    except SystemExit as e:
        
        print('Error:', e)  # 添加调试语句，打印错误信息
        # response="Error: "+str("argument command: invalid choice:(choose from 'simulate', 'optimize', 'evaluate')")
        response = documents()
        # return jsonify({'error': 'Internal Server Error'}), 500
        return jsonify({"role": "assistant", "content": response})
    except LithoBusyError as e:
        return jsonify({"role": "assistant", "content": f"Error: {str(e)}"})
    except Exception as e:
        # import traceback
        # traceback.print_exc()
        # # 或者将异常信息保存到字符串中
        # error_message = traceback.format_exc()
        # print(error_message)
        print('Error:', e)  # 添加调试语句，打印错误信息
        response = "Error: " + str(e)
        # return jsonify({'error': 'Internal Server Error'}), 500
        return jsonify({"role": "assistant", "content": response})


# 新增流式输出接口
@app.route('/stream_generate', methods=['POST'])
def stream_message():
    """流式生成接口，处理流式输出请求"""
    print("enter streaming backend message")
    
    def generate():
        try:
            # 获取请求数据
            data = json.loads(request.data)
            message = data.get('message', '')
            username = data.get('user_id', 'default')
            
            print(f"用户: {username}")
            print(f"流式消息: {message}")
            
            # 不向前端发送"分析中"的消息，只在后端打印日志
            print("分析中...")
            
            # 流式分析用户意图
            analysis_chunks = []
            for chunk in analyze_user_intent_streaming(message, streaming=True):
                analysis_chunks.append(chunk)
                # 直接将内容发送给用户
                chunk_data = {
                    "chunk": chunk,
                    "is_complete": False
                }
                yield f"data: {json.dumps(chunk_data)}\n\n"
            
            # 组合完整的分析结果
            analysis = "".join(analysis_chunks)
            print(f"完整分析: {analysis}")
            
            # 不向前端发送"确定操作"的消息，只在后端打印日志
            print("确定操作...")
            
            # 流式获取操作和参数，但不发送这部分内容
            instruction_chunks = []
            for chunk in get_action_and_params_streaming(message, analysis=analysis, streaming=True):
                instruction_chunks.append(chunk)
            
            # 组合完整的指令
            instruction_text = "".join(instruction_chunks)
            llm_output_instructions = get_command_from_streaming_response(instruction_text)
            print(f"提取指令: {llm_output_instructions}")
            
            # 如果有指令，则执行它
            if llm_output_instructions != "":
                # 发送指令信息，但不包含多余的提示文字
                instruction_chunk = {
                    "chunk": f"\n\n## 使用如下指令\n\n{llm_output_instructions}\n\n",
                    "is_complete": False
                }
                yield f"data: {json.dumps(instruction_chunk)}\n\n"
                
                # 解析指令
                instructions = shlex.split(llm_output_instructions)
                print(f"解析指令: {instructions}")
                
                # 执行指令但不发送"执行中"提示
                print("执行中...")
                pool = _resolve_litho_pool(instructions)
                slot = LithoJobSlot(pool)
                if slot.queue_position > 0:
                    queue_chunk = {
                        "chunk": "",
                        "is_complete": False,
                        "status": "queued",
                        "job_type": pool,
                        "queue_position": slot.queue_position,
                    }
                    yield f"data: {json.dumps(queue_chunk)}\n\n"
                with slot:
                    running_chunk = {
                        "chunk": "",
                        "is_complete": False,
                        "status": "running",
                        "job_type": pool,
                        "queue_position": 0,
                    }
                    yield f"data: {json.dumps(running_chunk)}\n\n"
                    litho_output = parse_and_dispatch(instructions, test=False)
                print(f"光刻输出: {litho_output}")
                
                # 输出结果
                result_chunk = {
                    "chunk": f"## 光刻输出结果\n\n{litho_output}",
                    "is_complete": True
                }
                yield f"data: {json.dumps(result_chunk)}\n\n"
            else:
                # 如果没有指令，直接标记为完成
                end_chunk = {
                    "chunk": "",
                    "is_complete": True
                }
                yield f"data: {json.dumps(end_chunk)}\n\n"
        
        except LithoBusyError as e:
            error_chunk = {
                "chunk": str(e),
                "is_complete": True,
                "status": "busy",
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
        except Exception as e:
            error_message = f"处理消息时出错: {str(e)}"
            print(error_message)
            error_chunk = {
                "chunk": error_message,
                "is_complete": True
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
    
    # 返回Flask流式响应
    return Response(stream_with_context(generate()), content_type='text/event-stream')


@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "service": "litho",
        "status": "ok",
        "port": get_backend_port(),
        "provider": LITHO_PROVIDER,
        "model": LITHO_MODEL,
        "scheduler": {
            "cpu_workers": LITHO_CPU_WORKERS,
            "gpu_workers": LITHO_GPU_WORKERS,
            "running": dict(_litho_running),
            "waiting": dict(_litho_waiting),
        },
    }), 200

# @app.route('/uploadMessage', methods=['POST'])
# def upload_message():
#     # 根据用户传入指令处理
#     pass
#     # 返回结果，可能包含若干张图和文字，需要前端按顺序输出
    
#     return jsonify(data)

@app.route('/static/output/<filename>')
def output_file(filename):
    file_path = os.path.join(app.config['OUTPUT_FOLDER'], filename)
    print("Serving file from:", file_path)
    # timestamp = int(time.time())
    # return send_from_directory(app.config['OUTPUT_FOLDER'], filename)
    response = send_from_directory(app.config['OUTPUT_FOLDER'], filename)
    # 获取文件的最后修改时间
    last_modified_time = os.path.getmtime(file_path)

    # 添加 Cache-Control 和 Last-Modified 头部
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    response.headers['Last-Modified'] = last_modified_time
    return response

@app.route('/static/upload/<filename>')
def uploaded_file(filename):
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    print("Serving file from:", file_path)
    # timestamp = int(time.time())
    # return send_from_directory(app.config['OUTPUT_FOLDER'], filename)
    response = send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    # 获取文件的最后修改时间
    last_modified_time = os.path.getmtime(file_path)

    # 添加 Cache-Control 和 Last-Modified 头部
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    response.headers['Last-Modified'] = last_modified_time
    return response


def test_online():
    test_args = ["optimize", "--target", "M1_test1.glp", "--model", "simpleilt", "--tile_sizeX", "2048", "--tile_sizeY", "2048", "--output_format", "printedNom"]  #,"--output_result", "False", "--output_metrics","True"
    output = parse_and_dispatch(test_args, test=False)
    print(output)
    print("simpleilt_optimize_online     PASS")


    ###得保证那个文件夹下只有一个文件
    test_args = ["optimize", "--model", "simpleilt", "--tile_sizeX", "2048", "--tile_sizeY", "2048", "--output_format", "printedNom"]  #,"--output_result", "False", "--output_metrics","True"
    output = parse_and_dispatch(test_args, test=False)
    print(output)
    print("simpleilt_optimize_online_no_target     PASS")

def test():
    # import pdb;pdb.set_trace()
    
    #### simpleilt
    test_args = ["optimize", "--target", "/data/Web-FabGPT/LLM/litho/litho_code/thirdparty/OpenILT/benchmark/ICCAD2013/M1_test1.glp", "--model", "simpleilt", "--tile_sizeX", "2048", "--tile_sizeY", "2048", "--output_format", "printedNom"]  #,"--output_result", "False", "--output_metrics","True"
    output = parse_and_dispatch(test_args)
    print("simpleilt_optimize     PASS")
    
    test_args = ["simulate", "--mask", "/data/Web-FabGPT/LLM/litho/litho_code/output_image/M1_test1_mask.png", "--tile_sizeX", "2048", "--tile_sizeY", "2048", "--output_format", "printedNom"]
    output = parse_and_dispatch(test_args)
    print("simpleilt_simulate     PASS")
    
    # evaluate --mask "M1_test1_mask.png"  --target  "M1_test1.glp"
    test_args = ['evaluate', '--mask', "/data/Web-FabGPT/LLM/litho/litho_code/output_image/M1_test1_mask.png", '--target', '/data/Web-FabGPT/LLM/litho/litho_code/thirdparty/OpenILT/benchmark/ICCAD2013/M1_test1.glp']
    output = parse_and_dispatch(test_args)
    print("simpleilt_evaluate     PASS")
    
    #### neural_ilt
    
    test_args = ["optimize", "--target", "/data/Web-FabGPT/LLM/litho/litho_code/thirdparty/neural_ilt/dataset/ibm_opc_test/t1_0_mask.png", "--model", "neural_ilt", "--output_result", "--output_metrics", "--tile_sizeX", "2048", "--tile_sizeY", "2048", "--output_format", "printedNom"]  #,"--output_result", "False", "--output_metrics","True"
    output = parse_and_dispatch(test_args)
    print("neural-ilt_optimize     PASS")
    
    ##### levelset
    test_args = ["optimize", "--target", "/data/Web-FabGPT/LLM/litho/litho_code/thirdparty/OpenILT/benchmark/ICCAD2013/M1_test2.glp", "--model", "levelset", "--tile_sizeX", "2048", "--tile_sizeY", "2048", "--output_format", "printedNom"]  #,"--output_result", "False", "--output_metrics","True"
    output = parse_and_dispatch(test_args)
    print("levelset_optimize     PASS")
    
    ##### multilevel
    test_args = ["optimize", "--target", "/data/Web-FabGPT/LLM/litho/litho_code/thirdparty/OpenILT/benchmark/ICCAD2013/M1_test3.glp", "--model", "multilevel", "--tile_sizeX", "2048", "--tile_sizeY", "2048", "--output_format", "printedNom"]  #,"--output_result", "False", "--output_metrics","True"
    output = parse_and_dispatch(test_args)
    print("multilevel_optimize     PASS")
    
    ##### curvmulti
    test_args = ["optimize", "--target", "/data/Web-FabGPT/LLM/litho/litho_code/thirdparty/OpenILT/benchmark/ICCAD2013/M1_test4.glp", "--model", "curvmulti", "--tile_sizeX", "2048", "--tile_sizeY", "2048", "--output_format", "printedNom"]  #,"--output_result", "False", "--output_metrics","True"
    output = parse_and_dispatch(test_args)
    print("curvmulti_optimize     PASS")
    
def custom():
    test_args = ["simulate", "--mask", "/data/Web-FabGPT/LLM/litho/litho_code/thirdparty/OpenILT/benchmark/ICCAD2013/M1_test1.glp", "--tile_sizeX", "2048", "--tile_sizeY", "2048", "--output_format", "printedNom"]
    output = parse_and_dispatch(test_args)   
    
if __name__ == '__main__':
    # test()
    # test_online()
    
    # custom()
    
    app.run(
        debug=False,
        use_reloader=False,
        host=get_backend_ip(),
        port=get_backend_port(),
    )
