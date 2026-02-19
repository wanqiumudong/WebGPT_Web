"""
用户管理模块 - 统一处理用户隔离相关的逻辑，增强安全性
"""
import os
import re
import logging
import hashlib
import threading
from typing import Optional, Dict, List

logger = logging.getLogger("ColPali-RAG-Manager")

class UserManager:
    """用户管理器，处理用户隔离和权限控制，增强安全性"""
    
    def __init__(self, knowledge_root: str, data_root: str):
        self.knowledge_root = knowledge_root
        self.data_root = data_root
        self._lock = threading.RLock()  # 添加线程锁
        
    def _sanitize_identifier(self, identifier: str) -> str:
        """安全地清理标识符，确保只包含安全字符"""
        if not identifier:
            return "anonymous"
        
        # 只保留字母数字和下划线，连字符转换为下划线（Milvus兼容）
        safe_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(identifier))
        safe_id = safe_id.replace('-', '_')  # Milvus不支持连字符，转换为下划线
        
        # 限制长度，避免路径过长
        safe_id = safe_id[:50]
        
        # 如果清理后为空，使用默认值
        if not safe_id:
            safe_id = "anonymous"
        
        return safe_id.lower()  # 统一小写
        
    def _generate_secure_config_id(self, user_id: str, name: str) -> str:
        """生成安全的配置ID"""
        safe_user_id = self._sanitize_identifier(user_id)
        safe_name = self._sanitize_identifier(name)
        
        # 使用时间戳和哈希生成唯一ID
        import time
        timestamp = int(time.time())
        hash_input = f"{safe_user_id}_{safe_name}_{timestamp}"
        hash_id = hashlib.md5(hash_input.encode()).hexdigest()[:8]
        
        return f"config_{timestamp}_{hash_id}"
        
    def get_user_collection_name(self, user_id: str, config_id: str) -> str:
        """生成用户特定的Milvus集合名称，增强安全性"""
        with self._lock:
            if config_id == 'none':
                return 'none_placeholder'
            elif config_id == 'default':
                return 'default'
            else:
                safe_user_id = self._sanitize_identifier(user_id)
                safe_config_id = self._sanitize_identifier(config_id)
                
                # 生成确定性的集合名称，限制长度
                collection_name = f"user_{safe_user_id}_{safe_config_id}"
                
                # Milvus集合名称限制：不能超过255个字符
                if len(collection_name) > 200:
                    # 如果太长，使用哈希值
                    hash_suffix = hashlib.md5(collection_name.encode()).hexdigest()[:8]
                    collection_name = f"user_{safe_user_id[:50]}_{hash_suffix}"
                
                return collection_name
    
    def get_user_knowledge_path(self, user_id: str, config_id: str) -> str:
        """生成用户特定的知识库路径"""
        with self._lock:
            if config_id == 'none':
                return os.path.join(self.knowledge_root, "none")
            elif config_id == 'default':
                return os.path.join(self.knowledge_root, "global", "default")
            else:
                safe_user_id = self._sanitize_identifier(user_id)
                safe_config_id = self._sanitize_identifier(config_id)
                return os.path.join(self.knowledge_root, "users", f"user_{safe_user_id}", safe_config_id)
    
    def get_user_data_path(self, user_id: str, config_id: str) -> str:
        """生成用户特定的RAG数据路径"""
        with self._lock:
            if config_id == 'none':
                return os.path.join(self.data_root, "none")
            elif config_id == 'default':
                return os.path.join(self.data_root, "global", "default")
            else:
                safe_user_id = self._sanitize_identifier(user_id)
                safe_config_id = self._sanitize_identifier(config_id)
                return os.path.join(self.data_root, "users", f"user_{safe_user_id}", safe_config_id)
    
    def is_readonly_config(self, config_id: str) -> bool:
        """检查配置是否为只读"""
        return config_id in ['default', 'none']
    
    def can_user_access_config(self, user_id: str, config: Dict, config_id: str) -> bool:
        """检查用户是否可以访问指定配置"""
        # 系统默认配置对所有用户可见
        if config_id in ['default', 'none']:
            return True
            
        # 用户私有配置只对对应用户可见
        config_user_id = config.get('user_id', '')
        return self._sanitize_identifier(config_user_id) == self._sanitize_identifier(user_id)
    
    def can_user_modify_config(self, user_id: str, config_id: str) -> bool:
        """检查用户是否可以修改指定配置"""
        if self.is_readonly_config(config_id):
            return False
        return True
    
    def filter_configs_for_user(self, user_id: str, all_configs: Dict) -> List[Dict]:
        """过滤用户可见的配置列表"""
        with self._lock:
            filtered_configs = []
            
            for cfg_id, cfg in all_configs.items():
                if self.can_user_access_config(user_id, cfg, cfg_id):
                    # 创建配置副本，避免修改原配置
                    filtered_cfg = cfg.copy()
                    filtered_cfg['id'] = cfg_id
                    filtered_configs.append(filtered_cfg)
            
            # 按创建时间排序，最新的在前
            filtered_configs.sort(key=lambda x: x.get('created_time', 0), reverse=True)
            
            return filtered_configs
    
    def create_user_directories(self, user_id: str, config_id: str) -> tuple:
        """创建用户特定的目录结构"""
        with self._lock:
            knowledge_path = self.get_user_knowledge_path(user_id, config_id)
            data_path = self.get_user_data_path(user_id, config_id)
            
            # 安全地创建目录，防止路径遍历攻击
            try:
                # 检查路径是否在允许的根目录内
                knowledge_abs = os.path.abspath(knowledge_path)
                data_abs = os.path.abspath(data_path)
                knowledge_root_abs = os.path.abspath(self.knowledge_root)
                data_root_abs = os.path.abspath(self.data_root)
                
                if not knowledge_abs.startswith(knowledge_root_abs):
                    raise ValueError(f"Invalid knowledge path: {knowledge_path}")
                if not data_abs.startswith(data_root_abs):
                    raise ValueError(f"Invalid data path: {data_path}")
                
                os.makedirs(knowledge_path, exist_ok=True)
                os.makedirs(data_path, exist_ok=True)
                
                logger.info(f"创建用户目录: {user_id}, 配置: {config_id}")
                
            except Exception as e:
                logger.error(f"创建用户目录失败: {str(e)}")
                raise
            
            return knowledge_path, data_path
            
    def validate_user_permissions(self, user_id: str, config_id: str, operation: str = "read") -> bool:
        """验证用户权限"""
        with self._lock:
            if operation == "read":
                # 读操作：检查是否可以访问
                return True  # 基本检查，可以根据需要扩展
            elif operation == "write":
                # 写操作：检查是否可以修改
                return self.can_user_modify_config(user_id, config_id)
            elif operation == "delete":
                # 删除操作：只能删除自己的非只读配置
                return (not self.is_readonly_config(config_id) and 
                       self.can_user_modify_config(user_id, config_id))
            
            return False