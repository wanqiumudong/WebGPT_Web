import argparse
from ..config.settings import get_upload_image_path

################################################
## 命令行参数解析器，定义了三个主要子命令(simulate, optimize, evaluate)
################################################
def create_parser():

    # 创建顶层参数解析器对象，设置描述
    # 创建子解析器容器，用于定义不同的子命令
    parser = argparse.ArgumentParser(description='光刻模拟与优化工具')
    subparsers = parser.add_subparsers(dest='command', help='可用命令')

    # 创建共同参数，定义多个子命令共用的参数配置
    # 每个参数包含选项（如可选值、默认值）和帮助文本
    common_args = {
        # '--model': {
        #     'choices': ['simpleilt', 'levelset', 'multilevel', 'curvmulti', 'neural_ilt'],
        #     'default': 'simpleilt',
        #     'help': '模型选择'
        # },
        '--output_format': {
            'choices': ['printedNom', 'printedMax', 'printedMin'],
            'default': 'printedNom',
            'help': '输出格式'
        },
        # '--output_metrics': {
        #     'type': bool,
        #     'default': False,
        #     'help': '是否输出L2，pvb等指标'
        # },
        '--tile_sizeX': {
            'type': int,
            'default': 2048,
            'help': 'X方向tile大小'
        },
        '--tile_sizeY': {
            'type': int,
            'default': 2048,
            'help': 'Y方向tile大小'
        }
    }

    # 创建simulate子命令的解析器
    # 添加特定于该命令的参数（--mask）
    simulate_parser = subparsers.add_parser('simulate', help='执行模拟过程')
    simulate_parser.add_argument('--mask', help='输入mask文件(支持glp和png格式)')
    
    # 添加共同参数到simulate子命令解析器中
    for arg, options in common_args.items():
        simulate_parser.add_argument(arg, **options)

    # 创建optimize子命令
    # 添加特定于该命令的参数（--model, --target等）
    # 布尔参数使用action="store_true"来表示存在即为True --> 用户只需要添加参数名而不需要显式地指定值，参数的存在本身就代表"启用"这个选项
    optimize_parser = subparsers.add_parser('optimize', help='执行优化过程')
    optimize_parser.add_argument('--model', choices=['simpleilt', 'neural_ilt','levelset', 'multilevel', 'curvmulti'], default='simpleilt', help='模型选择')
    optimize_parser.add_argument('--target', help='目标图像文件(支持glp和png格式)')
    optimize_parser.add_argument('--output_result', action="store_true", help='是否输出最终的printed image')
    optimize_parser.add_argument('--output_metrics', action="store_true", help='是否输出L2，pvb等指标')

    # 添加共同参数到optimize
    for arg, options in common_args.items():
        optimize_parser.add_argument(arg, **options)
    
    # 创建evaluate子命令
    evaluate_parser = subparsers.add_parser('evaluate', help='评估mask')
    evaluate_parser.add_argument('--mask', required=True, help='输入mask文件(支持glp和png格式)')
    evaluate_parser.add_argument('--target',required=True, help='目标图像文件(支持glp和png格式)')
    evaluate_parser.add_argument('--tile_sizeX', type=int, default=2048, help='X方向tile大小')
    evaluate_parser.add_argument('--tile_sizeY', type=int, default=2048, help='Y方向tile大小')
    return parser


from litho_code.handler.optimize_handler import optimize_handler
from litho_code.handler.simulate_handler import simulate_handler
from litho_code.handler.evaluate_handler import evaluate_handler

import argparse
import glob
import os

# 使用glob模块查找指定目录中的所有文件，排除目录，只保留文件
# 根据找到的文件返回不同的结果
def check_single_file_glob(file_path):
    """使用 glob 检查路径下是否只有一个文件。"""
    files = glob.glob(os.path.join(file_path, "*")) # 匹配路径下所有文件
    files = [f for f in files if os.path.isfile(f)] # 再次过滤，确保是文件
    if len(files) == 1:
        return os.path.basename(files[0]) 
    elif len(files) == 0:
        print(f"路径 {file_path} 下没有文件。")
        return None
    else:
        print(f"路径 {file_path} 下有多个文件：{files}。")
        return None


def documents():
    html_str='''
## 命令行工具使用提示

本工具提供 模拟 (simulation), 优化 (optimization), 和 评估 (evaluation) 三大功能，通过命令行指令进行操作。

以下分别介绍每个功能的命令和选项，请根据您的需求选择合适的命令和选项。

---

### `simulate` 命令：执行仿真模拟

**功能描述：**  该命令用于执行前向仿真过程，根据您提供的掩模 (mask) 文件，模拟打印过程并生成打印图像。

#### 使用方法：

`simulate --mask [掩模文件路径] [可选参数]`

#### 必需选项：

* `--mask [image_file]`：  **必须指定** 掩模文件路径。
    * 支持的文件格式： **glp** 和 **png** 格式。
    * 请确保提供的文件路径正确，且文件格式符合要求。

#### 可选选项：

* `--output_format [格式]`:  指定输出打印图像的格式。
    * 可选项： `printedNom`, `printedMax`, `printedMin`。
    * 默认值： `printedNom`。
    * 如果您需要特定类型的打印图像，请使用此选项指定。

#### 示例：

`simulate --mask input_mask.glp  --output_format printedMax`

---

### `optimize` 命令：执行优化过程

**功能描述：**  该命令用于执行优化过程，根据您提供的目标图像 (target image) 文件，优化生成一个掩模 (mask) 文件，以期打印结果更接近目标图像。

#### 使用方法：

`optimize --target [目标图像文件路径] [可选参数]`

#### 必需选项：

* `--target [image_file]`:  目标图像文件路径。
    * 支持的文件格式： **glp** 和 **png** 格式。
    * 请确保提供的文件路径正确，且文件格式符合要求。

#### 可选选项：

* `--model [模型名称]`:  选择使用的优化模型。
    * 可选项： `simpleilt`, `levelset`, `multilevel`, `curvmulti`, `nerul-ilt` 等。
    * 默认值： `simpleilt`。
    * 您可以根据您的需求选择不同的模型进行优化。

* `--output_result [True/False]`:  控制是否输出最终的打印图像。
    * 可选项： `True`, `False`。
    * 默认值： `False`。
    * 设置为 `True` 将会额外输出优化后的打印图像。

* `--output_format [格式]`:  **仅当 `--output_result=True` 时生效**。 指定输出打印图像的格式。
    * 可选项： `printedNom`, `printedMax`, `printedMin`。
    * 默认值： `printedNom`。
    * 如果您需要特定类型的打印图像，请使用此选项指定。

* `--output_metrics [True/False]`:  控制是否输出评估指标 (例如 L2, pvb)。
    * 可选项： `True`, `False`。
    * 默认值： `False`。
    * 设置为 `True` 将会输出优化结果的评估指标。



#### 示例：

`optimize --target target_image.glp --model multilevel `

---

### `evaluate` 命令：执行评估过程

**功能描述：**  该命令用于执行评估过程，根据您提供的掩模 (mask) 文件和目标图像 (target image) 文件，计算并输出各项评估指标 (metrics)。

#### 使用方法：

`evaluate --mask [掩模文件路径] --target [目标图像文件路径]`

#### 必需选项：

* `--mask [image_file]`:  掩模文件路径。
    * 支持的文件格式： **glp** 和 **png** 格式。
    * 请确保提供的文件路径正确，且文件格式符合要求。

* `--target [image_file]`:  目标图像文件路径。
    * 支持的文件格式： **glp** 和 **png** 格式。
    * 请确保提供的文件路径正确，且文件格式符合要求。

#### 示例：

`evaluate --mask optimized_mask.glp --target target_image.png`

---

### 通用提示：

* **文件路径：** 请确保所有文件路径都正确无误，指向您想要使用的掩模或目标图像文件。
* **文件格式：**  目前仅支持 `.glp` 和 `.png` 两种图像格式。请确保您的输入文件格式正确。
* **默认值：**  如果您不指定可选选项，程序将使用默认值。您可以根据需要调整这些选项。

'''
    return html_str

# * `--tile_sizeX [整数]`:  设置tile的大小。
#     * 默认值： `2048`。
#     * 如果您需要调整瓦片大小以适应您的计算资源或输入图像，可以使用此选项。

# * `--tile_sizeY [整数]`:  设置 Y 方向的瓦片大小。
#     * 默认值： `2048`。
#     * 如果您需要调整瓦片大小以适应您的计算资源或输入图像，可以使用此选项。



# * `--tile_sizeX [整数]`:  设置 X 方向的瓦片大小。
#     * 默认值： `2048`。
#     * 如果您需要调整瓦片大小以适应您的计算资源或输入图像，可以使用此选项。

# * `--tile_sizeY [整数]`:  设置 Y 方向的瓦片大小。
#     * 默认值： `2048`。
#     * 如果您需要调整瓦片大小以适应您的计算资源或输入图像，可以使用此选项。

# * **查看帮助：**  您可能可以通过在命令行中输入命令名称后加上 `--help` 来查看更详细的帮助信息 (例如: `simulate --help`)。

################################################
## 使用 argparse 解析输入的命令行参数, 处理文件路径（如果未指定路径，会尝试自动查找文件）, 将命令分派到相应的处理函数
################################################
def parse_and_dispatch(input_command,test=True):
    
    # 创建参数解析器
    parser = create_parser()
    # folder_path='/data/Web-FabGPT/LLM/litho/litho_code/upload_image'

    # 获取上传文件的目录路径
    folder_path=get_upload_image_path()

    # try:
    # 解析输入命令（若包含simulate, optimize, evaluate...），并检查相应的参数
    args = parser.parse_args(input_command)
    if args.command == "simulate" :
        if args.mask is None:
            file_path=check_single_file_glob(folder_path)
            if  file_path is not None:
                args.mask=file_path
            else:
                raise RuntimeError("mask file not found")
        elif test is False:
            args.mask=os.path.join(folder_path,args.mask)
        
        if not os.path.exists(args.mask):
            raise RuntimeError("mask file not found")
    elif args.command == "optimize" :
        if args.target is None:
            file_path=check_single_file_glob(folder_path)
            if  file_path is not None:
                args.target=file_path
            else:
                raise RuntimeError("target file not found")
        elif test is False:
            args.target=os.path.join(folder_path,args.target)
        if not os.path.exists(args.target):
            raise RuntimeError("target file not found")
    elif args.command == "evaluate":
        if test is False:
            args.mask=os.path.join(folder_path,args.mask)
            args.target=os.path.join(folder_path,args.target)
        if not os.path.exists(args.mask):
            raise RuntimeError("mask file not found")
        if not os.path.exists(args.target):
            raise RuntimeError("target file not found")
    # except Exception as e:
    #     # 捕获异常
    #     error_message = str(e)  # 获取异常信息
    #     return {"status": "error", "message": error_message}
            

    handles={
            "optimize":optimize_handler,
             "simulate":simulate_handler,
             "evaluate":evaluate_handler}

    output=handles[args.command](args)
    

    # return {"status": "success", "output": output}
    return output