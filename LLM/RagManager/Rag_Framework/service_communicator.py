"""
服务通信模块 - 统一管理与其他服务(Chatbot, TCAD)的通信
"""
import requests
import logging
import time
import threading
from typing import Dict, Any, Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("ColPali-RAG-Manager")

class ServiceCommunicator:
    """服务间通信管理器，提供统一的HTTP通信接口"""
    
    def __init__(self):
        self._session_lock = threading.RLock()
        self._sessions: Dict[str, requests.Session] = {}
        
        # 服务配置
        self.service_configs = {
            'chatbot': {
                'base_url': 'http://10.98.64.22:8080',
                'timeout': 30,
                'retries': 3
            },
            'tcad': {
                'base_url': 'http://10.98.64.22:5004',
                'timeout': 10,
                'retries': 2
            }
        }
    
    def _get_session(self, service_name: str) -> requests.Session:
        """获取或创建服务专用的会话"""
        with self._session_lock:
            if service_name not in self._sessions:
                session = requests.Session()
                
                # 配置重试策略
                config = self.service_configs.get(service_name, {})
                retries = config.get('retries', 2)
                
                retry_strategy = Retry(
                    total=retries,
                    backoff_factor=0.3,
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["HEAD", "GET", "OPTIONS", "POST"]
                )
                
                adapter = HTTPAdapter(max_retries=retry_strategy)
                session.mount("http://", adapter)
                session.mount("https://", adapter)
                
                self._sessions[service_name] = session
            
            return self._sessions[service_name]
    
    def health_check(self, service_name: str) -> bool:
        """检查服务健康状态"""
        try:
            config = self.service_configs.get(service_name)
            if not config:
                logger.error(f"未知的服务: {service_name}")
                return False
            
            session = self._get_session(service_name)
            url = f"{config['base_url']}/health"
            
            response = session.get(url, timeout=2)
            return response.status_code == 200
            
        except Exception as e:
            logger.debug(f"服务 {service_name} 健康检查失败: {str(e)}")
            return False
    
    def post_to_service(self, service_name: str, endpoint: str, 
                       data: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
        """向指定服务发送POST请求"""
        try:
            config = self.service_configs.get(service_name)
            if not config:
                logger.error(f"未知的服务: {service_name}")
                return None
            
            session = self._get_session(service_name)
            url = f"{config['base_url']}/{endpoint.lstrip('/')}"
            
            default_headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
            
            if headers:
                default_headers.update(headers)
            
            logger.debug(f"发送请求到 {service_name}: {url}")
            
            response = session.post(
                url, 
                json=data, 
                headers=default_headers,
                timeout=config.get('timeout', 10)
            )
            
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    logger.warning(f"服务 {service_name} 返回非JSON响应")
                    return {'response': response.text}
            else:
                logger.error(f"服务 {service_name} 返回错误: {response.status_code}")
                return None
                
        except requests.exceptions.Timeout:
            logger.error(f"服务 {service_name} 请求超时")
            return None
        except requests.exceptions.ConnectionError:
            logger.error(f"无法连接到服务 {service_name}")
            return None
        except Exception as e:
            logger.error(f"向服务 {service_name} 发送请求时出错: {str(e)}")
            return None
    
    def get_from_service(self, service_name: str, endpoint: str, 
                        params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """向指定服务发送GET请求"""
        try:
            config = self.service_configs.get(service_name)
            if not config:
                logger.error(f"未知的服务: {service_name}")
                return None
            
            session = self._get_session(service_name)
            url = f"{config['base_url']}/{endpoint.lstrip('/')}"
            
            logger.debug(f"发送GET请求到 {service_name}: {url}")
            
            response = session.get(
                url, 
                params=params,
                timeout=config.get('timeout', 10)
            )
            
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    return {'response': response.text}
            else:
                logger.error(f"服务 {service_name} 返回错误: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"从服务 {service_name} 获取数据时出错: {str(e)}")
            return None
    
    def notify_config_update(self, user_id: str, config_id: str, active: bool) -> Dict[str, bool]:
        """通知其他服务配置更新"""
        notification_data = {
            'user_id': user_id,
            'config_id': config_id,
            'active': active,
            'timestamp': time.time()
        }
        
        results = {}
        
        # 通知TCAD服务
        tcad_result = self.post_to_service('tcad', 'rag_config_update', notification_data)
        results['tcad'] = tcad_result is not None
        
        # 通知Chatbot服务
        chatbot_result = self.post_to_service('chatbot', 'rag_config_update', notification_data)
        results['chatbot'] = chatbot_result is not None
        
        logger.info(f"配置更新通知结果: {results}")
        return results
    
    def close_all_sessions(self):
        """关闭所有会话"""
        with self._session_lock:
            for session in self._sessions.values():
                try:
                    session.close()
                except:
                    pass
            self._sessions.clear()

# 全局服务通信器实例
_global_service_communicator = None

def get_service_communicator() -> ServiceCommunicator:
    """获取全局服务通信器实例"""
    global _global_service_communicator
    if _global_service_communicator is None:
        _global_service_communicator = ServiceCommunicator()
    return _global_service_communicator