import base64
from io import BytesIO
from PIL import Image
import zipfile
import os

def image_to_base64(image_path):
    # try:
    #     with open(image_path, "rb") as image_file:
    #         image = Image.open(image_file)
    #         buffered = BytesIO()
    #         image.save(buffered, format=image.format)  # 关键：使用原图格式
    #         img_str = base64.b64encode(buffered.getvalue()).decode()
    #         return img_str
    # except FileNotFoundError:
    #     print(f"错误：文件未找到：{image_path}")
    #     return None
    # except Exception as e:
    #     print(f"发生错误：{e}")
    #     return None

    with open(image_path, "rb") as image_file:
        image = Image.open(image_file)
        buffered = BytesIO()
        image.save(buffered, format=image.format)  # 关键：使用原图格式
        img_str = base64.b64encode(buffered.getvalue()).decode()
        return img_str

def unzip_file(zip_filepath, extract_to="."):
    """解压 zip 文件。

    Args:
        zip_filepath: zip 文件路径。
        extract_to: 解压到的目录，默认为当前目录。
    """
    try:
        with zipfile.ZipFile(zip_filepath, 'r') as zip_ref:
            zip_ref.extractall(extract_to)  # 解压所有文件到指定目录
            print(f"成功解压 {zip_filepath} 到 {extract_to}")
    except FileNotFoundError:
        print(f"错误：文件 {zip_filepath} 未找到")
    except zipfile.BadZipFile:
        print(f"错误：{zip_filepath} 不是有效的 zip 文件")
    except Exception as e:
        print(f"解压过程中发生错误：{e}")
        
        
def check_file_type_by_suffix(filename):
    """根据后缀名判断文件类型。"""
    if not filename:
        return "文件名为空"
    base, ext = os.path.splitext(filename)  # 分离文件名和扩展名
    ext = ext.lower()  # 转换为小写，忽略大小写差异

    if ext == ".png":
        print(f"{filename} 可能是 PNG 图片")
        return "png"
    elif ext == ".zip":
        print(f"{filename} 可能是 ZIP 压缩文件")
        return "zip"
    elif ext == ".glp":
        print(f"{filename} 可能是 GLP 文件")
        return "glp"
    elif ext == "":
        return "没有后缀名"
    else:
        print(f"{filename} 的后缀名为: {ext}，未进行特殊处理")
        return "other"