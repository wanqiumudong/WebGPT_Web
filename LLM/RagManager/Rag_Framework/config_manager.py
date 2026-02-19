"""
配置管理模块 - 管理知识库配置，增强线程安全性
config_manager.py
"""

import os
import time
import json
import logging
import hashlib
import traceback
import threading
from typing import Dict, Any

logger = logging.getLogger("ColPali-RAG-Manager")

# 全局配置
DATA_ROOT = "/data/yphu/Web-FabGPT/LLM/RagManager/rag_data"
KNOWLEDGE_ROOT = "/data/yphu/Web-FabGPT/LLM/RagManager/knowledge_base"
CONFIG_FILE = "/data/yphu/Web-FabGPT/LLM/RagManager/rag_data/rag_configurations.json"
LOG_DIR = "/data/yphu/Web-FabGPT/LLM/RagManager/search_logs"

# 端口配置
TCAD_PORT = 5002
RAG_MANAGER_PORT = 5006
SOCKET_TIMEOUT = 5

# 全局状态和锁
rag_configurations = {}
_config_lock = threading.RLock()

# 导入用户管理器
from .user_manager import UserManager
user_manager = UserManager(KNOWLEDGE_ROOT, DATA_ROOT)

# 导入用户活跃配置管理器
from .user_active_config_manager import UserActiveConfigManager
user_active_manager = UserActiveConfigManager(DATA_ROOT)

# 为了向后兼容，保留原有函数接口
def get_user_collection_name(user_id, config_id):
    return user_manager.get_user_collection_name(user_id, config_id)

def get_user_knowledge_path(user_id, config_id):
    return user_manager.get_user_knowledge_path(user_id, config_id)

def get_user_data_path(user_id, config_id):
    return user_manager.get_user_data_path(user_id, config_id)

def is_readonly_config(config_id):
    return user_manager.is_readonly_config(config_id)

# 用户活跃配置管理函数
def set_user_active_config(user_id, config_id):
    """设置用户的活跃配置"""
    return user_active_manager.set_user_active_config(user_id, config_id)

def get_user_active_config(user_id):
    """获取用户的活跃配置"""
    return user_active_manager.get_user_active_config(user_id)

def get_user_active_config_with_fallback(user_id, available_configs):
    """获取用户活跃配置，包含回退逻辑"""
    return user_active_manager.get_user_active_config_with_fallback(user_id, available_configs)

def cleanup_invalid_user_configs(valid_config_ids):
    """清理无效的用户配置引用"""
    return user_active_manager.cleanup_invalid_configs(valid_config_ids)

def load_rag_configurations():
    """加载知识库配置，线程安全"""
    global rag_configurations
    with _config_lock:
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if not content:
                        logger.warning(f"配置文件 {CONFIG_FILE} 为空，将初始化为空配置。")
                        rag_configurations = {}
                    else:
                        loaded_configs = json.loads(content)
                        # 验证配置数据完整性
                        if isinstance(loaded_configs, dict):
                            rag_configurations = loaded_configs
                        else:
                            logger.error("配置文件格式错误，使用空配置")
                            rag_configurations = {}
                logger.info(f"已加载 {len(rag_configurations)} 个知识库配置。")
            else:
                logger.info(f"配置文件 {CONFIG_FILE} 不存在，将创建新的空配置。")
                rag_configurations = {}
        except json.JSONDecodeError as jde:
            logger.error(f"加载知识库配置时JSON解析错误: {str(jde)}。将使用空配置。")
            rag_configurations = {}
        except Exception as e:
            logger.error(f"加载知识库配置时发生未知错误: {str(e)}")
            traceback.print_exc()
            rag_configurations = {}

def save_rag_configurations():
    """保存知识库配置，线程安全"""
    with _config_lock:
        try:
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            
            # 创建临时文件，原子写入
            temp_file = CONFIG_FILE + '.tmp'
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(rag_configurations, f, ensure_ascii=False, indent=2)
            
            # 原子移动文件
            os.replace(temp_file, CONFIG_FILE)
            logger.info("知识库配置已保存")
            
        except Exception as e:
            logger.error(f"保存知识库配置出错: {str(e)}")
            traceback.print_exc()
            # 清理临时文件
            temp_file = CONFIG_FILE + '.tmp'
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass

def initialize_knowledge_base_structure():
    """初始化知识库目录结构，线程安全"""
    global rag_configurations
    with _config_lock:
        # 确保基础目录存在
        os.makedirs(KNOWLEDGE_ROOT, exist_ok=True)
        os.makedirs(DATA_ROOT, exist_ok=True)
        os.makedirs(LOG_DIR, exist_ok=True)
        
        # 创建用户隔离的目录结构
        os.makedirs(os.path.join(KNOWLEDGE_ROOT, "users"), exist_ok=True)
        os.makedirs(os.path.join(KNOWLEDGE_ROOT, "global"), exist_ok=True)
        os.makedirs(os.path.join(KNOWLEDGE_ROOT, "none"), exist_ok=True)
        os.makedirs(os.path.join(DATA_ROOT, "users"), exist_ok=True)
        os.makedirs(os.path.join(DATA_ROOT, "global"), exist_ok=True)
        os.makedirs(os.path.join(DATA_ROOT, "none"), exist_ok=True)

        # 创建默认配置目录
        user_manager.create_user_directories('anonymous', 'default')

        load_rag_configurations()

        # 确保必要的默认配置存在
        _ensure_default_configs()

        save_rag_configurations()
        logger.info(f"知识库结构初始化完成，当前共有 {len(rag_configurations)} 个配置。")

def _ensure_default_configs():
    """确保默认配置存在，线程安全"""
    global rag_configurations
    
    # 确保 'default' 配置存在
    if 'default' not in rag_configurations:
        default_knowledge_dir, default_rag_dir = user_manager.create_user_directories('anonymous', 'default')
        rag_configurations['default'] = {
            'id': 'default',
            'name': '默认知识库',
            'display_name': '默认知识库',
            'folder': default_knowledge_dir,
            'db_path': default_rag_dir,
            'active': False,
            'created_time': time.time() - 86400,
            'readonly': True
        }
        logger.info("创建了默认知识库配置。")

    # 确保 'none' 配置存在
    if 'none' not in rag_configurations:
        none_knowledge_dir, none_rag_dir = user_manager.create_user_directories('anonymous', 'none')
        rag_configurations['none'] = {
            'id': 'none',
            'name': '无',
            'display_name': '无',
            'folder': none_knowledge_dir,
            'db_path': none_rag_dir,
            'active': False,
            'created_time': time.time() - 86400 * 2,
            'readonly': True
        }
        logger.info("创建了'无'知识库配置。")

    # 确保至少有一个配置是活跃的
    is_any_active = any(cfg.get('active', False) for cfg in rag_configurations.values())
    if not is_any_active:
        if 'default' in rag_configurations:
            rag_configurations['default']['active'] = True
            logger.info("将默认知识库设为活跃。")

def create_user_config(user_id: str, name: str, config_id: str = None) -> dict:
    """创建用户特定的配置，线程安全"""
    with _config_lock:
        if not config_id:
            # 使用用户管理器的安全ID生成
            config_id = user_manager._generate_secure_config_id(user_id, name)
        
        knowledge_folder, rag_folder = user_manager.create_user_directories(user_id, config_id)
        
        new_config = {
            'id': config_id,
            'name': name,
            'display_name': name,
            'folder': knowledge_folder,
            'db_path': rag_folder,
            'active': False,
            'created_time': time.time(),
            'user_id': user_id,
            'readonly': False  # 用户创建的配置默认不是只读
        }
        
        rag_configurations[config_id] = new_config
        save_rag_configurations()
        
        logger.info(f"为用户 {user_id} 创建配置 {config_id}: {name}")
        return new_config

def get_user_configs(user_id: str) -> Dict[str, Any]:
    """获取用户可见的配置，线程安全"""
    with _config_lock:
        return user_manager.filter_configs_for_user(user_id, rag_configurations)

def update_config(config_id: str, updates: Dict[str, Any], user_id: str = None) -> bool:
    """更新配置，线程安全"""
    with _config_lock:
        if config_id not in rag_configurations:
            logger.error(f"配置 {config_id} 不存在")
            return False
            
        config = rag_configurations[config_id]
        
        # 权限检查
        if user_id and not user_manager.can_user_access_config(user_id, config, config_id):
            logger.error(f"用户 {user_id} 无权修改配置 {config_id}")
            return False
            
        if user_manager.is_readonly_config(config_id):
            logger.error(f"配置 {config_id} 为只读，不能修改")
            return False
        
        # 更新配置
        for key, value in updates.items():
            if key not in ['id', 'created_time']:  # 保护关键字段
                config[key] = value
        
        config['updated_time'] = time.time()
        save_rag_configurations()
        
        logger.info(f"更新配置 {config_id}: {list(updates.keys())}")
        return True

def delete_config(config_id: str, user_id: str = None) -> bool:
    """删除配置，线程安全"""
    with _config_lock:
        if config_id not in rag_configurations:
            logger.error(f"配置 {config_id} 不存在")
            return False
            
        config = rag_configurations[config_id]
        
        # 权限检查
        if user_id:
            if not user_manager.validate_user_permissions(user_id, config_id, "delete"):
                logger.error(f"用户 {user_id} 无权删除配置 {config_id}")
                return False
        
        # 删除配置
        del rag_configurations[config_id]
        save_rag_configurations()
        
        logger.info(f"删除配置 {config_id}")
        return True