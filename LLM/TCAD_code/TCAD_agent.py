from agent_utils import *
import os
import argparse
Gtree.GetToolCommandFile = GetToolCommandFile

def modify_sdevice_cmd(sdevice_cmd_path, user_requests=None):
    """修改仿真命令文件，添加用户指定的物理模型参数"""
    if not user_requests:
        user_requests = {
            "Tmodel": "Thermodynamic",
            "DF": "GradQuasiFermi",
            "QC": "eQuantumPotential", 
            "EQUATIONSET": "Poisson Electron Hole Temperature"
        }
    
    try:
        with open(sdevice_cmd_path, 'r') as f:
            content = f.read()
        
        model_definitions = []
        for key, value in user_requests.items():
            model_definitions.append(f"#define _{key}_ {value}")
        
        model_definition_text = "\n".join(model_definitions)
        modified_content = model_definition_text + "\n\n" + content
        
        with open(sdevice_cmd_path, 'w') as f:
            f.write(modified_content)
        
        print(f"已添加物理模型参数到 {sdevice_cmd_path}:")
        print(model_definition_text)
        
        return True
    except Exception as e:
        print(f"修改仿真文件时出错: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description='TCAD EDA Agent')
    parser.add_argument('project_path', help='项目路径')
    parser.add_argument('sde_cmd', help='SDE命令文件')
    parser.add_argument('sdevice_cmd', help='SDEVICE命令文件')
    parser.add_argument('--output_dir', help='结果输出目录', default=None)
    
    args = parser.parse_args()
    
    project_path = args.project_path
    sde_cmd = args.sde_cmd
    sdevice_cmd = args.sdevice_cmd
    output_dir = args.output_dir
    
    if output_dir is None:
        output_dir = os.path.abspath(os.path.join(os.getcwd(), "png_outputs"))
    
    os.makedirs(output_dir, exist_ok=True)
    
    conversation_id = None
    if 'upload_files' in output_dir:
        parts = output_dir.split(os.sep)
        for i, part in enumerate(parts):
            if part == 'upload_files' and i+1 < len(parts):
                conversation_id = parts[i+1]
                break
    
    print(f"当前会话ID: {conversation_id}")
    print(f"输出目录: {output_dir}")
    
    user_model_params = {
        "Tmodel": "Thermodynamic",
        "DF": "GradQuasiFermi",
        "QC": "eQuantumPotential", 
        "EQUATIONSET": "\"Poisson Electron Hole Temperature\""
    }
    modify_sdevice_cmd(sdevice_cmd, user_model_params)
    
    tools = [
        {"tool": "sde", "dbtool": "sde"},
        {"tool": "sdevice", "dbtool": "sdevice"},
    ]

    clear_project_directory(project_path)
    add_ready_definition_to_sde_cmd(sde_cmd)

    parameters = [
        {"name": "ready", "value": ['1']}
    ]

    proj = Deck(project_path)
    tree1 = proj.getGtree()

    AllTools = tree1.AllTools()

    for i, tool in enumerate(tools):
        try:
            tree1.AddTool(tool=tool["tool"], dbtool=tool["dbtool"], step=i, toSave=True)
            print(f"{tool['tool']} Added")
        except RuntimeError as e:
            print(f"fail to add tool {tool['tool']}: {e}")

    add_cmd_file('sde', sde_cmd, tree1)
    add_cmd_file('sdevice', sdevice_cmd, tree1)

    for param in reversed(parameters):
        try:
            default_value = param['value'][0]
            tree1.AddParam(param['name'], default_value, step=1)
            print(f"{param['name']} Added with default value: {default_value}")
        except RuntimeError as e:
            print(f"fail to add param {param['name']} to sdevice: {e}")

    experiments_path = generate_experiments_path(parameters)
    for path in experiments_path:
        try:
            tree1.AddPath(pvalues=path)
            print(f"Added experiment path: {path}")
        except RuntimeError as e:
            print(f"fail to add experiment path {path}: {e}")

    proj.save()
    proj.reload()

    proj.preprocess()
    proj.run()

    sdevice_nodes = find_sdevice_nodes(tree1, scenario='default', desired_status=STATE_DONE)

    print(f"有 {len(sdevice_nodes)} 个sdevice节点。")
    print("与sdevice工具相关的节点及其状态列表:")
    for node, status in sdevice_nodes:
        print(f"节点编号: {node}，状态: {status}")

    simulation_type = determine_simulation_type(project_path)

    if simulation_type in simulation_config:
        x_data = simulation_config[simulation_type]["x_data"]
        y_data = simulation_config[simulation_type]["y_data"]
    else:
        print(f"不支持的仿真类型: {simulation_type}，将使用默认配置")
        simulation_type = "IdVgs"
        x_data = simulation_config["IdVgs"]["x_data"]
        y_data = simulation_config["IdVgs"]["y_data"]

    print("下面开始输出结果到指定文件夹...")
    print(f"输出目录: {output_dir}")

    filename_suffix = ""
    if conversation_id:
        filename_suffix = f"_{conversation_id}"
    
    tcl_scripts_dir = os.path.join(output_dir, "tcl_scripts")
    os.makedirs(tcl_scripts_dir, exist_ok=True)
    
    index = 1
    tcl_files_created = []
    
    try:
        for file_name in os.listdir(tcl_scripts_dir):
            file_path = os.path.join(tcl_scripts_dir, file_name)
            if os.path.isfile(file_path):
                os.unlink(file_path)
        print(f"已清理旧的TCL脚本文件")
    except Exception as e:
        print(f"清理TCL脚本时出错: {e}")

    expected_files = [
        f"outputs_node3{filename_suffix}.png",
        f"outputs_result3{filename_suffix}.txt",
        f"n3_des{filename_suffix}.out"
    ]
    
    for filename in expected_files:
        file_path = os.path.join(output_dir, filename)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                print(f"已删除旧文件: {file_path}")
            except Exception as e:
                print(f"删除文件 {file_path} 时出错: {e}")

    for node, status in sdevice_nodes:
        if status != ConvertEnumToString(STATE_DONE):
            print(f"跳过节点 {node}，状态为 {status}")
            continue
        
        tcl_filepath = generate_tcl_script(
            node, 
            project_path, 
            tcl_scripts_dir,
            output_dir,
            simulation_type, 
            x_data, 
            y_data,
            index,
            filename_suffix=filename_suffix
        )
        
        if tcl_filepath:
            tcl_files_created.append(tcl_filepath)
        index += 1

    if tcl_files_created:
        print(f"开始执行svisual分析，处理 {len(tcl_files_created)} 个脚本...")
        execute_svisual(tcl_scripts_dir)
        
        for filename in expected_files:
            file_path = os.path.join(output_dir, filename)
            if os.path.exists(file_path):
                print(f"已生成文件: {file_path}")
            else:
                print(f"警告: 预期的输出文件不存在: {file_path}")
    else:
        print("未创建任何TCL脚本，跳过svisual执行")
        output_result_path = os.path.join(output_dir, f"outputs_result3{filename_suffix}.txt")
        with open(output_result_path, 'w') as f:
            f.write(f"TCAD Simulation Results Summary\n")
            f.write("=" * 40 + "\n\n")
            f.write(f"Simulation Type: {simulation_type}\n")
            f.write(f"Warning: No valid TCL scripts were created. No results available.\n")

    print("开始生成对应的out/log文件...")
    out_files_copied = False
    
    for node, status in sdevice_nodes:
        if status != ConvertEnumToString(STATE_DONE):
            continue
        move_out_files(node, project_path, output_dir, filename_suffix)
        out_files_copied = True
    
    if not out_files_copied:
        print(f"没有复制任何.out文件，创建默认文件")
        output_log_path = os.path.join(output_dir, f"n3_des{filename_suffix}.out")
        with open(output_log_path, 'w') as f:
            f.write("No simulation log files were found for any completed nodes.\n")
    
    output_result_path = os.path.join(output_dir, f"outputs_result3{filename_suffix}.txt")
    if not os.path.exists(output_result_path):
        print(f"结果文件不存在，创建默认文件: {output_result_path}")
        with open(output_result_path, 'w') as f:
            f.write(f"TCAD Simulation Results Summary\n")
            f.write("=" * 40 + "\n\n")
            f.write(f"Simulation Type: {simulation_type}\n")
            f.write(f"Completed Nodes: {len(sdevice_nodes)}\n\n")
            
            f.write("Simulation Parameters:\n")
            for param in parameters:
                f.write(f"- {param['name']}: {param['value'][0]}\n")
            
            f.write("\nPhysical Models:\n")
            for key, value in user_model_params.items():
                f.write(f"- {key}: {value}\n")
            
            f.write("\nKey Metrics (extracted from simulation):\n")
            f.write("- Default result file created as no extractions were available\n")

    print(f"结果摘要已保存到: {output_result_path}")
    print("PNG结果和.out文件已生成。")
    print("脚本执行完成。")

if __name__ == "__main__":
    main()
