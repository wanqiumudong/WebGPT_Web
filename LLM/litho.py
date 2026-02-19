# 统一的文件保存空间
import warnings
warnings.filterwarnings("ignore")
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
import os
import json
import shlex
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
CORS(app)

app.config['OUTPUT_FOLDER'] = get_output_image_path()
app.config['UPLOAD_FOLDER'] = get_upload_image_path()
@app.route('/uploadFile', methods=['POST'])
def upload_image():
    global history, modality_cache
    upload_image_save_path = get_upload_image_path()
    print("enter backend")
    try:
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

@app.route('/generate', methods=['POST'])
def upload_message():
    global history, modality_cache
    print("enter backend message")
    try:
        data = json.loads(request.data)
        message = data.get('message', '')
        username = data.get('user_id', 'default')

        print(username)
        print(message)
        analysis = analyze_user_intent(message)
        print(f"Analysis: {analysis}")
        llm_output_instructions = get_action_and_params(message, analysis)
        print(f"llm_output_instructions(need to be string)  {llm_output_instructions} ")
        if llm_output_instructions != "":
            instructions = shlex.split(llm_output_instructions)

            print(instructions)
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

        output = f"""
## 分析结果

{analysis}

{combined_instruction_litho_output}
"""

        return jsonify({"role": "assistant", "content": output})
    except SystemExit as e:
        print('Error:', e)
        response = documents()
        return jsonify({"role": "assistant", "content": response})
    except Exception as e:
        print('Error:', e)
        response = "Error: " + str(e)
        return jsonify({"role": "assistant", "content": response})


@app.route('/stream_generate', methods=['POST'])
def stream_message():
    """流式生成接口。"""
    print("enter streaming backend message")
    
    def generate():
        try:
            data = json.loads(request.data)
            message = data.get('message', '')
            username = data.get('user_id', 'default')
            
            print(f"用户: {username}")
            print(f"流式消息: {message}")
            print("分析中...")

            analysis_chunks = []
            for chunk in analyze_user_intent_streaming(message, streaming=True):
                analysis_chunks.append(chunk)
                chunk_data = {
                    "chunk": chunk,
                    "is_complete": False
                }
                yield f"data: {json.dumps(chunk_data)}\n\n"

            analysis = "".join(analysis_chunks)
            print(f"完整分析: {analysis}")
            print("确定操作...")

            instruction_chunks = []
            for chunk in get_action_and_params_streaming(message, analysis=analysis, streaming=True):
                instruction_chunks.append(chunk)

            instruction_text = "".join(instruction_chunks)
            llm_output_instructions = get_command_from_streaming_response(instruction_text)
            print(f"提取指令: {llm_output_instructions}")

            if llm_output_instructions != "":
                instruction_chunk = {
                    "chunk": f"\n\n## 使用如下指令\n\n{llm_output_instructions}\n\n",
                    "is_complete": False
                }
                yield f"data: {json.dumps(instruction_chunk)}\n\n"

                instructions = shlex.split(llm_output_instructions)
                print(f"解析指令: {instructions}")

                print("执行中...")
                litho_output = parse_and_dispatch(instructions, test=False)
                print(f"光刻输出: {litho_output}")

                result_chunk = {
                    "chunk": f"## 光刻输出结果\n\n{litho_output}",
                    "is_complete": True
                }
                yield f"data: {json.dumps(result_chunk)}\n\n"
            else:
                end_chunk = {
                    "chunk": "",
                    "is_complete": True
                }
                yield f"data: {json.dumps(end_chunk)}\n\n"
        
        except Exception as e:
            error_message = f"处理消息时出错: {str(e)}"
            print(error_message)
            error_chunk = {
                "chunk": error_message,
                "is_complete": True
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"

    return Response(stream_with_context(generate()), content_type='text/event-stream')

@app.route('/static/output/<filename>')
def output_file(filename):
    file_path = os.path.join(app.config['OUTPUT_FOLDER'], filename)
    print("Serving file from:", file_path)
    response = send_from_directory(app.config['OUTPUT_FOLDER'], filename)
    last_modified_time = os.path.getmtime(file_path)

    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    response.headers['Last-Modified'] = last_modified_time
    return response

@app.route('/static/upload/<filename>')
def uploaded_file(filename):
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    print("Serving file from:", file_path)
    response = send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    last_modified_time = os.path.getmtime(file_path)

    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    response.headers['Last-Modified'] = last_modified_time
    return response


def test_online():
    test_args = ["optimize", "--target", "M1_test1.glp", "--model", "simpleilt", "--tile_sizeX", "2048", "--tile_sizeY", "2048", "--output_format", "printedNom"]
    output = parse_and_dispatch(test_args, test=False)
    print(output)
    print("simpleilt_optimize_online     PASS")


    ###得保证那个文件夹下只有一个文件
    test_args = ["optimize", "--model", "simpleilt", "--tile_sizeX", "2048", "--tile_sizeY", "2048", "--output_format", "printedNom"]
    output = parse_and_dispatch(test_args, test=False)
    print(output)
    print("simpleilt_optimize_online_no_target     PASS")

def test():
    #### simpleilt
    test_args = ["optimize", "--target", "/data/Web-FabGPT/LLM/litho_code/thirdparty/OpenILT/benchmark/ICCAD2013/M1_test1.glp", "--model", "simpleilt", "--tile_sizeX", "2048", "--tile_sizeY", "2048", "--output_format", "printedNom"]
    output = parse_and_dispatch(test_args)
    print("simpleilt_optimize     PASS")
    
    test_args = ["simulate", "--mask", "/data/Web-FabGPT/LLM/litho_code/output_image/M1_test1_mask.png", "--tile_sizeX", "2048", "--tile_sizeY", "2048", "--output_format", "printedNom"]
    output = parse_and_dispatch(test_args)
    print("simpleilt_simulate     PASS")
    
    # evaluate --mask "M1_test1_mask.png"  --target  "M1_test1.glp"
    test_args = ['evaluate', '--mask', "/data/Web-FabGPT/LLM/litho_code/output_image/M1_test1_mask.png", '--target', '/data/Web-FabGPT/LLM/litho_code/thirdparty/OpenILT/benchmark/ICCAD2013/M1_test1.glp']
    output = parse_and_dispatch(test_args)
    print("simpleilt_evaluate     PASS")
    
    #### neural_ilt
    
    test_args = ["optimize", "--target", "/data/Web-FabGPT/LLM/litho_code/thirdparty/neural_ilt/dataset/ibm_opc_test/t1_0_mask.png", "--model", "neural_ilt", "--output_result", "--output_metrics", "--tile_sizeX", "2048", "--tile_sizeY", "2048", "--output_format", "printedNom"]
    output = parse_and_dispatch(test_args)
    print("neural-ilt_optimize     PASS")
    
    ##### levelset
    test_args = ["optimize", "--target", "/data/Web-FabGPT/LLM/litho_code/thirdparty/OpenILT/benchmark/ICCAD2013/M1_test2.glp", "--model", "levelset", "--tile_sizeX", "2048", "--tile_sizeY", "2048", "--output_format", "printedNom"]
    output = parse_and_dispatch(test_args)
    print("levelset_optimize     PASS")
    
    ##### multilevel
    test_args = ["optimize", "--target", "/data/Web-FabGPT/LLM/litho_code/thirdparty/OpenILT/benchmark/ICCAD2013/M1_test3.glp", "--model", "multilevel", "--tile_sizeX", "2048", "--tile_sizeY", "2048", "--output_format", "printedNom"]
    output = parse_and_dispatch(test_args)
    print("multilevel_optimize     PASS")
    
    ##### curvmulti
    test_args = ["optimize", "--target", "/data/Web-FabGPT/LLM/litho_code/thirdparty/OpenILT/benchmark/ICCAD2013/M1_test4.glp", "--model", "curvmulti", "--tile_sizeX", "2048", "--tile_sizeY", "2048", "--output_format", "printedNom"]
    output = parse_and_dispatch(test_args)
    print("curvmulti_optimize     PASS")
    
def custom():
    test_args = ["simulate", "--mask", "/data/Web-FabGPT/LLM/litho_code/thirdparty/OpenILT/benchmark/ICCAD2013/M1_test1.glp", "--tile_sizeX", "2048", "--tile_sizeY", "2048", "--output_format", "printedNom"]
    output = parse_and_dispatch(test_args)   

if __name__ == '__main__':
    app.run(debug=True, host=get_backend_ip(), port=get_backend_port())
