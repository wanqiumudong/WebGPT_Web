from swbpy2 import *
import os
import glob
import itertools
import subprocess
import shutil
from swbpy2.core.core import STATE_DONE, STATE_FAILED, STATE_PENDING, ConvertEnumToString

def clear_project_directory(path):
    """递归删除项目路径中的所有内容"""
    if os.path.exists(path):
        try:
            shutil.rmtree(path)
            print(f"已删除项目路径: {path}")
        except Exception as e:
            print(f"无法删除项目路径 {path}. 错误: {e}")

def add_ready_definition_to_sde_cmd(cmd_file):
    """在sde_cmd的第一行添加(define ready @ready@)，如果没有的话"""
    if os.path.isfile(cmd_file):
        with open(cmd_file, 'r') as file:
            lines = file.readlines()
        
        if lines and lines[0].strip() != "(define ready @ready@)":
            lines.insert(0, "(define ready @ready@)\n")
            
            with open(cmd_file, 'w') as file:
                file.writelines(lines)
            print(f"已修改sde_cmd文件 {cmd_file}，添加定义行")

def GetToolCommandFile(self, toolname):
    obj = self._entity.GetFlow().LookupToolStep(toolname)
    if obj is None:
        raise TreeException(f'Tool with name "{toolname}" not found') 
    cmdfile = f"{toolname}_{self.GetDBToolCtxItem(f'{obj.GetDBTool()},acronym')}.cmd"
    cmdfile = os.path.join(self.Project(), cmdfile)
    return cmdfile

Gtree.GetToolCommandFile = GetToolCommandFile

def add_cmd_file(tool_name, cmd_path, tree):
    """向指定工具添加 cmd 文件"""
    if os.path.isfile(cmd_path):
        try:
            cmdfile = tree.GetToolCommandFile(tool_name)
            with open(cmdfile, 'w') as f:
                with open(cmd_path, 'r') as cmd:
                    f.write(cmd.read())
            print(f"Added {cmd_path} to {tool_name}")
        except RuntimeError as e:
            print(f"fail to add {cmd_path} to {tool_name}: {e}")

def generate_experiments_path(parameters):
    value_lists = [param['value'] for param in parameters]
    product = itertools.product(*value_lists)
    experiments_path = [list(p) for p in product]
    return experiments_path

def find_sdevice_nodes(tree, scenario='default', desired_status=None):
    """查找所有与sdevice工具相关的节点，并获取其状态"""
    sdevice_steps = tree.ToolSteps('sdevice')
    sdevice_nodes = []
    
    for step in sdevice_steps:
        nodes_in_step = tree.AllStepNodes(scenario=scenario, step=step)
        for node in nodes_in_step:
            status = tree.NodeStatus(node)
            sdevice_nodes.append((node, status))
    
    print(f"总共有 {len(sdevice_nodes)} 个sdevice节点。 包括{sdevice_nodes}")
    
    if desired_status:
        filtered_nodes = [node for node in sdevice_nodes if node[1] == ConvertEnumToString(desired_status)]
        print(f"有 {len(filtered_nodes)} 个sdevice节点处于状态 '{ConvertEnumToString(desired_status)}'。")
        return filtered_nodes
    else:
        return sdevice_nodes

def generate_tcl_script(node, project_path, tcl_dir, png_output_dir, simulation_type, x_data, y_data, index, filename_suffix=""):
    """为指定的节点生成TCL脚本"""
    plt_filename = f"{simulation_type}_n{node}_des.plt"
    plt_filepath = os.path.join(project_path, "results", "nodes", f"{node}", plt_filename)
    
    if not os.path.isfile(plt_filepath):
        print(f"警告: .plt文件不存在: {plt_filepath}")
        return None
    
    x_region = x_data[0]
    y_region = y_data[0]
    x_type = x_data[1]
    y_type = y_data[1]
    dataset_name = f"{simulation_type}_n{node}_des"
    x_title = x_data[-1]
    y_title = y_data[-1]
    
    output_image_path = f"{png_output_dir}/outputs_node3{filename_suffix}.png"
    output_result_path = f"{png_output_dir}/outputs_result3{filename_suffix}.txt"
    
    if simulation_type == "IdVds":
        tcl_content = f"""
load_file {plt_filepath} -name {{{dataset_name}}}
set output_file {output_result_path}
set file_id [open $output_file "w"]
set Id 1e-7
set SIGN 1
create_plot -1d
select_plots {{Plot_{index}}}
create_curve -axisX {{{x_region} {x_type}}} -axisY {{{y_region} {y_type}}} -dataset {{{dataset_name}}} -plot Plot_{index}
set_axis_prop -plot Plot_{index} -axis x -title {x_title}
set_axis_prop -plot Plot_{index} -axis y -title {y_title}
export_view {output_image_path} -plots {{Plot_{index}}} -format png
set Vds [get_variable_data "drain InnerVoltage" -dataset {{{dataset_name}}}]
set Ids [get_variable_data "drain TotalCurrent" -dataset {{{dataset_name}}}]
ext::ExtractBVv -out BVdsov -name "BV" -v $Vds -i $Ids -sign $SIGN -f "%.3f"
ext::ExtractVdlin -out Vdlin -name "out" -v $Vds -i $Ids -io $Id -f "%.4f"
puts  $file_id "Extracted Breakdown Voltage: [format %.3f $BVdsov] V"
puts  $file_id "Linear region voltage corresponding to current $Id A: [format %.4f $Vdlin] V"
puts "脚本执行完成。"
close $file_id
    """.strip()
    elif simulation_type == "IdVgs":
        tcl_content = f"""
load_file {plt_filepath} -name {{{dataset_name}}}
set SIGN 1.0
create_plot -1d
select_plots {{Plot_{index}}}
create_curve -axisX {{{x_region} {x_type}}} -axisY {{{y_region} {y_type}}} -dataset {{{dataset_name}}} -plot Plot_{index}
set_axis_prop -plot Plot_{index} -axis x -title {x_title}
set_axis_prop -plot Plot_{index} -axis y -title {y_title}
export_view {output_image_path} -plots {{Plot_{index}}} -format png
set Vgs [get_variable_data "gate OuterVoltage" -dataset {{{dataset_name}}}]
set Ids [get_variable_data "drain TotalCurrent" -dataset {{{dataset_name}}}]
ext::AbsList -out absIds -x $Ids
ext::ExtractVtgm -out Vtgm -name Vtgm -v $Vgs -i $absIds
ext::ExtractGm -out gm -name gmLin -v $Vgs -i $absIds
ext::ExtractSS -out SS -name SSlin -v $Vgs -i $absIds -vo [expr $SIGN * 1e-2]
ext::ExtractExtremum -out Idmax -name IdLin  -x $Vgs -y $absIds -type max
set output_file {output_result_path}
set file_id [open $output_file "w"]
puts  $file_id "Extracted Vt (Max gm method): [format %.3f $Vtgm] V"
puts  $file_id "Extracted SSlin: [format %.3f $SS] mV/dec"
puts  $file_id "Extracted Max gm: [format %.3f $gm] S"
puts  $file_id "Max IdLin is: [format %.3f $Idmax] A"
puts "脚本执行完成。"
close $file_id
    """.strip()
    else:
        tcl_content = f"""
load_file {plt_filepath} -name {{{dataset_name}}}
create_plot -1d
select_plots {{Plot_{index}}}
create_curve -axisX {{{x_region} {x_type}}} -axisY {{{y_region} {y_type}}} -dataset {{{dataset_name}}} -plot Plot_{index}
set_axis_prop -plot Plot_{index} -axis x -title {x_title}
set_axis_prop -plot Plot_{index} -axis y -title {y_title}
export_view {output_image_path} -plots {{Plot_{index}}} -format png
set output_file {output_result_path}
set file_id [open $output_file "w"]
puts $file_id "Simulation Type: {simulation_type}"
puts $file_id "Node: {node}"
puts $file_id "X-axis: {x_title}"
puts $file_id "Y-axis: {y_title}"
close $file_id
puts "脚本执行完成。"
        """.strip()
    
    os.makedirs(png_output_dir, exist_ok=True)
    os.makedirs(tcl_dir, exist_ok=True)
    
    tcl_filename = f"generate_png_node{node}.tcl"
    tcl_filepath = os.path.join(tcl_dir, tcl_filename)

    with open(tcl_filepath, 'w') as tcl_file:
        tcl_file.write(tcl_content)

    print(f"生成TCL脚本: {tcl_filepath}")
    print(f"PNG将输出到: {output_image_path}")
    return tcl_filepath

def execute_svisual(tcl_dir):
    """执行svisual批处理TCL脚本"""
    os.makedirs(tcl_dir, exist_ok=True)
    
    tcl_files = [f for f in os.listdir(tcl_dir) if f.endswith('.tcl')]
    if not tcl_files:
        print("没有找到TCL脚本文件，跳过执行svisual。")
        return
    
    command = ["svisual", "-batchx"] + tcl_files
    
    try:
        result = subprocess.run(command, cwd=tcl_dir, capture_output=True, text=True, check=True)
        print("svisual执行输出:")
        print(result.stdout)
        if result.stderr:
            print("svisual执行错误:")
            print(result.stderr)
    except subprocess.CalledProcessError as e:
        print(f"svisual执行失败: {e}")
        print("标准输出:", e.stdout)
        print("标准错误:", e.stderr)

def move_out_files(node, project_path, png_output_dir, filename_suffix=""):
    """将对应节点的.out文件移动到png_output_dir目录中"""
    os.makedirs(png_output_dir, exist_ok=True)
    
    out_filename = f"n{node}_des.out"
    out_filepath = os.path.join(project_path, "results", "nodes", f"{node}", out_filename)
    destination_filename = f"n{node}_des{filename_suffix}.out"
    destination_path = os.path.join(png_output_dir, destination_filename)
    
    if os.path.isfile(out_filepath):
        try:
            shutil.copy(out_filepath, destination_path)
            print(f"已复制 {out_filepath} 到 {destination_path}")
        except Exception as e:
            print(f"复制 {out_filepath} 到 {destination_path} 失败: {e}")
    else:
        print(f"警告: .out文件不存在: {out_filepath}")
        try:
            with open(destination_path, 'w') as f:
                f.write(f"No output log available for node {node}\n")
            print(f"已创建占位符文件: {destination_path}")
        except Exception as e:
            print(f"创建占位符文件失败: {e}")

def determine_simulation_type(project_path):
    """自动检测 simulation_type 通过扫描 .plt 文件名"""
    plt_pattern = os.path.join(project_path, "results", "nodes", "*", "*.plt")
    plt_files = glob.glob(plt_pattern)
    
    if plt_files:
        plt_filename = os.path.basename(plt_files[0])
        try:
            simulation_type = plt_filename.split("_n")[0]
            print(f"检测到的 simulation_type: {simulation_type}")
            return simulation_type
        except Exception as e:
            print(f"从文件名 {plt_filename} 提取simulation_type失败: {e}")
    
    cmd_pattern = os.path.join(project_path, "*.cmd")
    cmd_files = glob.glob(cmd_pattern)
    
    for cmd_file in cmd_files:
        try:
            with open(cmd_file, 'r') as f:
                content = f.read().lower()
                if "idvg" in content or "gate" in content and "sweep" in content:
                    print("基于命令文件内容判断为 IdVgs 仿真")
                    return "IdVgs"
                elif "idvd" in content or "drain" in content and "sweep" in content:
                    print("基于命令文件内容判断为 IdVds 仿真")
                    return "IdVds"
        except Exception:
            pass
    
    print("无法确定仿真类型，默认使用 IdVgs")
    return "IdVgs"

simulation_config = {
    "IdVds": {
        "x_data": ["drain", "TotalCurrent", "Id"],
        "y_data": ["drain", "OuterVoltage", "Vd"]
    },
    "IdVgs":{
        "x_data": ["drain", "TotalCurrent", "Id"],
        "y_data": ["gate", "OuterVoltage", "Vg"]
    }
}