"""
统一的后端API响应格式和错误处理工具
所有模块使用相同的响应格式，确保前端处理的一致性
"""
from flask import jsonify
from datetime import datetime
import traceback
import logging

logger = logging.getLogger(__name__)

class ApiResponse:
    """统一的API响应类"""
    
    @staticmethod
    def success(data=None, message="操作成功"):
        """成功响应"""
        return jsonify({
            "success": True,
            "data": data,
            "message": message,
            "timestamp": datetime.now().isoformat(),
            "error": None
        }), 200
    
    @staticmethod
    def error(message="操作失败", error_code=500, details=None):
        """错误响应"""
        return jsonify({
            "success": False,
            "data": None,
            "message": message,
            "timestamp": datetime.now().isoformat(),
            "error": {
                "code": error_code,
                "message": message,
                "details": details
            }
        }), error_code
    
    @staticmethod
    def not_found(resource="资源"):
        """404响应"""
        return ApiResponse.error(
            message=f"{resource}未找到",
            error_code=404
        )
    
    @staticmethod
    def bad_request(message="请求参数无效"):
        """400响应"""
        return ApiResponse.error(
            message=message,
            error_code=400
        )
    
    @staticmethod
    def unauthorized(message="未授权访问"):
        """401响应"""
        return ApiResponse.error(
            message=message,
            error_code=401
        )
    
    @staticmethod
    def forbidden(message="访问被禁止"):
        """403响应"""
        return ApiResponse.error(
            message=message,
            error_code=403
        )
    
    @staticmethod
    def internal_error(message="服务器内部错误", exception=None):
        """500响应"""
        details = None
        if exception:
            details = str(exception)
            logger.error(f"Internal error: {details}")
            logger.error(traceback.format_exc())
        
        return ApiResponse.error(
            message=message,
            error_code=500,
            details=details
        )

class StreamResponse:
    """统一的流式响应格式"""
    
    @staticmethod
    def start(request_id, conversation_id=None, inference_type=None):
        """开始流式响应"""
        return {
            "chunk": "",
            "is_complete": False,
            "start_streaming": True,
            "request_id": request_id,
            "conversation_id": conversation_id,
            "inference_type": inference_type,
            "timestamp": datetime.now().isoformat()
        }
    
    @staticmethod
    def chunk(content, conversation_id=None, metadata=None):
        """数据块"""
        return {
            "chunk": content,
            "is_complete": False,
            "conversation_id": conversation_id,
            "metadata": metadata,
            "timestamp": datetime.now().isoformat()
        }
    
    @staticmethod
    def complete(conversation_id=None, summary=None):
        """完成响应"""
        return {
            "chunk": "",
            "is_complete": True,
            "conversation_id": conversation_id,
            "summary": summary,
            "timestamp": datetime.now().isoformat()
        }
    
    @staticmethod
    def error(error_message, conversation_id=None):
        """错误响应"""
        return {
            "chunk": "",
            "is_complete": True,
            "error": error_message,
            "conversation_id": conversation_id,
            "timestamp": datetime.now().isoformat()
        }
    
    @staticmethod
    def aborted(conversation_id=None):
        """中止响应"""
        return {
            "chunk": "\n\n[回答已中止]",
            "is_complete": True,
            "aborted": True,
            "conversation_id": conversation_id,
            "timestamp": datetime.now().isoformat()
        }

def validate_request_data(data, required_fields):
    """验证请求数据"""
    if not data:
        return False, "请求数据为空"
    
    missing_fields = [field for field in required_fields if field not in data]
    if missing_fields:
        return False, f"缺少必要字段: {', '.join(missing_fields)}"
    
    return True, None

def handle_database_error(operation, exception):
    """处理数据库错误"""
    error_msg = f"数据库{operation}失败"
    logger.error(f"{error_msg}: {str(exception)}")
    return ApiResponse.internal_error(error_msg, exception)

def handle_file_error(operation, exception):
    """处理文件操作错误"""
    error_msg = f"文件{operation}失败"
    logger.error(f"{error_msg}: {str(exception)}")
    return ApiResponse.internal_error(error_msg, exception)

def handle_api_error(operation, exception):
    """处理API调用错误"""
    error_msg = f"API{operation}失败"
    logger.error(f"{error_msg}: {str(exception)}")
    return ApiResponse.internal_error(error_msg, exception)

def safe_execute(func, error_context="操作"):
    """安全执行函数，统一错误处理"""
    try:
        return func()
    except ValueError as e:
        return ApiResponse.bad_request(f"{error_context}参数错误: {str(e)}")
    except FileNotFoundError as e:
        return ApiResponse.not_found(f"{error_context}文件")
    except PermissionError as e:
        return ApiResponse.forbidden(f"{error_context}权限不足")
    except ConnectionError as e:
        return ApiResponse.internal_error(f"{error_context}连接失败", e)
    except TimeoutError as e:
        return ApiResponse.internal_error(f"{error_context}超时", e)
    except Exception as e:
        return ApiResponse.internal_error(f"{error_context}失败", e)

class ModelConfig:
    """模型配置映射"""
    MODEL_NAMES = {
        0: "Chatbot",
        1: "FabGPT", 
        2: "Guangke",
        3: "TCAD",
        5: "CircuitThink",
        6: "RAGManager"
    }
    
    @classmethod
    def get_model_name(cls, model_id):
        return cls.MODEL_NAMES.get(model_id, f"Unknown-{model_id}")

def format_sse_data(data):
    """格式化SSE数据"""
    import json
    try:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    except Exception as e:
        logger.error(f"SSE数据序列化失败: {e}")
        return f"data: {json.dumps({'error': 'Data serialization failed'})}\n\n"