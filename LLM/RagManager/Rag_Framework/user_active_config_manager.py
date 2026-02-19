"""
用户活跃配置管理器 - 每个用户独立的活跃知识库状态管理
解决多用户环境下共享活跃状态的问题
"""
import os
import json
import time
import logging
import threading
from typing import Dict, Optional

logger = logging.getLogger("ColPali-RAG-Manager")

class UserActiveConfigManager:
    """管理每个用户的活跃知识库配置状态"""
    
    def __init__(self, data_root: str):
        self.data_root = data_root
        self.user_active_configs = {}  # {user_id: config_id}
        self.user_active_file = os.path.join(data_root, "user_active_configs.json")
        self._lock = threading.RLock()
        
        # 初始化时加载用户活跃配置
        self._load_user_active_configs()
    
    def _load_user_active_configs(self):
        """加载用户活跃配置"""
        with self._lock:
            try:
                if os.path.exists(self.user_active_file):
                    with open(self.user_active_file, 'r', encoding='utf-8') as f:
                        self.user_active_configs = json.load(f)
                    logger.info(f"已加载 {len(self.user_active_configs)} 个用户的活跃配置")
                else:
                    self.user_active_configs = {}
                    logger.info("用户活跃配置文件不存在，初始化为空")
            except Exception as e:
                logger.error(f"加载用户活跃配置失败: {str(e)}")
                self.user_active_configs = {}
    
    def _save_user_active_configs(self):
        """保存用户活跃配置"""
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.user_active_file), exist_ok=True)
                
                # 原子写入
                temp_file = self.user_active_file + '.tmp'
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(self.user_active_configs, f, ensure_ascii=False, indent=2)
                
                os.replace(temp_file, self.user_active_file)
                logger.debug("用户活跃配置已保存")
                
            except Exception as e:
                logger.error(f"保存用户活跃配置失败: {str(e)}")
                # 清理临时文件
                temp_file = self.user_active_file + '.tmp'
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except:
                        pass
    
    def set_user_active_config(self, user_id: str, config_id: str):
        """设置用户的活跃配置"""
        with self._lock:
            if not user_id:
                user_id = 'anonymous'
                
            old_config = self.user_active_configs.get(user_id)
            self.user_active_configs[user_id] = config_id
            
            # 保存到文件
            self._save_user_active_configs()
            
            logger.info(f"用户 {user_id} 的活跃配置从 {old_config} 切换到 {config_id}")
    
    def get_user_active_config(self, user_id: str) -> Optional[str]:
        """获取用户的活跃配置"""
        with self._lock:
            if not user_id:
                user_id = 'anonymous'
                
            return self.user_active_configs.get(user_id)
    
    def remove_user_active_config(self, user_id: str):
        """移除用户的活跃配置"""
        with self._lock:
            if not user_id:
                user_id = 'anonymous'
                
            if user_id in self.user_active_configs:
                del self.user_active_configs[user_id]
                self._save_user_active_configs()
                logger.info(f"已移除用户 {user_id} 的活跃配置")
    
    def get_user_active_config_with_fallback(self, user_id: str, available_configs: Dict) -> Optional[str]:
        """获取用户活跃配置，包含回退逻辑"""
        with self._lock:
            if not user_id:
                user_id = 'anonymous'
            
            # 验证输入参数
            if not available_configs or not isinstance(available_configs, dict):
                logger.warning(f"用户 {user_id} 的可用配置为空或格式错误: {type(available_configs)}")
                return None
            
            # 1. 尝试获取用户的活跃配置
            active_config_id = self.user_active_configs.get(user_id)
            
            # 2. 验证配置是否仍然存在且用户有权限
            if active_config_id and active_config_id in available_configs:
                logger.debug(f"用户 {user_id} 使用已有的活跃配置: {active_config_id}")
                return active_config_id
            
            # 3. 如果用户有可用的配置，选择优先级顺序
            if available_configs:
                # 优先级：default > 第一个可用配置
                if 'default' in available_configs:
                    fallback_config = 'default'
                    logger.info(f"用户 {user_id} 的活跃配置无效或不存在，回退到 default")
                else:
                    fallback_config = next(iter(available_configs.keys()))
                    logger.info(f"用户 {user_id} 的活跃配置无效或不存在，回退到 {fallback_config}")
                    
                self.set_user_active_config(user_id, fallback_config)
                return fallback_config
            
            # 4. 如果用户没有任何可用配置，返回None
            logger.warning(f"用户 {user_id} 没有可用的知识库配置")
            return None
    
    def cleanup_invalid_configs(self, valid_config_ids: set):
        """清理无效的配置引用"""
        with self._lock:
            users_to_update = []
            
            for user_id, config_id in list(self.user_active_configs.items()):
                if config_id not in valid_config_ids:
                    users_to_update.append(user_id)
            
            if users_to_update:
                for user_id in users_to_update:
                    del self.user_active_configs[user_id]
                    
                self._save_user_active_configs()
                logger.info(f"已清理 {len(users_to_update)} 个用户的无效配置引用")
    
    def get_all_user_active_configs(self) -> Dict[str, str]:
        """获取所有用户的活跃配置（用于调试）"""
        with self._lock:
            return self.user_active_configs.copy()
    
    def migrate_from_global_active(self, rag_configurations: Dict):
        """从全局活跃配置迁移到用户特定配置（一次性迁移）"""
        with self._lock:
            # 查找当前全局活跃配置
            global_active_config = None
            for config_id, config_data in rag_configurations.items():
                if config_data.get('active', False):
                    global_active_config = config_id
                    break
            
            # 如果没有用户配置且存在全局活跃配置，为默认用户设置
            if not self.user_active_configs and global_active_config:
                self.set_user_active_config('anonymous', global_active_config)
                logger.info(f"迁移全局活跃配置 {global_active_config} 到默认用户")
            
            # 清除所有全局活跃标志
            modified = False
            for config_id, config_data in rag_configurations.items():
                if config_data.get('active', False):
                    config_data['active'] = False
                    modified = True
            
            return modified  # 返回是否需要保存rag_configurations