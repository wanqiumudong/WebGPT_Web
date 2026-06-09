import os


### 得到litho_code的路径
def get_project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_upload_image_path():
    return os.path.join(get_project_root(), "upload_image")


def get_output_image_path():
    return os.path.join(get_project_root(), "output_image")

def get_backend_port():
    return int(os.environ.get("WEB_FABGPT_LITHO_PORT", "5003"))

def get_frontend_port():
    return int(os.environ.get("WEB_FABGPT_WEB_PORT", "3000"))

def get_backend_ip():
    return os.environ.get("WEB_FABGPT_HOST", "10.98.193.46")

def get_backend_url():
    return "http://"+get_backend_ip() +":"+str(get_backend_port())

if __name__ == '__main__':
    print(get_project_root())
    print(get_upload_image_path())
    print(get_output_image_path())
    print(get_backend_url())
