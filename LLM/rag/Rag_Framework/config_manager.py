"""
配置管理模块 - 管理知识库配置
- 加载、保存和管理知识库配置。
config_manager.py
"""

import os
import time
import json
import logging
import hashlib
import traceback
import socket
import requests
from pathlib import Path

logger = logging.getLogger("ColPali-RAG-Manager")

# 全局配置
RAG_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = str(RAG_ROOT / "rag_data")
KNOWLEDGE_ROOT = str(RAG_ROOT / "knowledge_base")
CONFIG_FILE = str(RAG_ROOT / "rag_data" / "rag_configurations.json")
LOG_DIR = str(RAG_ROOT / "search_logs")  # 添加专门的日志目录
LEGACY_RAG_ROOT = "/data/Web-FabGPT/LLM/rag"
SYSTEM_CONFIG_IDS = {"default", "none"}
INTERNAL_COLLECTION_IDS = {"global_default", "none_placeholder"}
INTERNAL_DB_NAMES = {"rag_default", "rag_global_default", "rag_none_placeholder"}

# 端口配置
TCAD_PORT = int(os.environ.get("WEB_FABGPT_TCAD_PORT", "5004"))  # TCAD服务端口
CHATBOT_PORT = int(os.environ.get("WEB_FABGPT_CHATBOT_PORT", "5001"))  # Chatbot服务端口
RAG_MANAGER_PORT = int(os.environ.get("WEB_FABGPT_RAG_PORT", "5006"))  # RAG Manager服务端口
SOCKET_TIMEOUT = 5  # Socket连接超时时间（秒）
MILVUS_HOST = os.environ.get("WEB_FABGPT_MILVUS_HOST", "127.0.0.1")
MILVUS_PORT = int(os.environ.get("WEB_FABGPT_MILVUS_PORT", "19530"))
MILVUS_URI = os.environ.get("WEB_FABGPT_MILVUS_URI", f"http://{MILVUS_HOST}:{MILVUS_PORT}")
MILVUS_CONNECT_TIMEOUT = float(os.environ.get("WEB_FABGPT_MILVUS_CONNECT_TIMEOUT", "0.3"))

# 全局状态
rag_configurations = {}  # 存储知识库配置
_milvus_probe_cache = {"timestamp": 0.0, "available": False}


def is_milvus_available(force: bool = False) -> bool:
    """Return quickly whether the local Milvus endpoint is reachable."""
    now = time.monotonic()
    cache_ttl = 1.0
    if not force and now - _milvus_probe_cache["timestamp"] < cache_ttl:
        return _milvus_probe_cache["available"]

    try:
        with socket.create_connection((MILVUS_HOST, MILVUS_PORT), timeout=MILVUS_CONNECT_TIMEOUT):
            available = True
    except OSError:
        available = False

    _milvus_probe_cache["timestamp"] = now
    _milvus_probe_cache["available"] = available
    return available


def normalize_local_rag_path(path_value):
    """Map legacy absolute RAG paths into the current local checkout."""
    if not path_value:
        return path_value
    normalized = str(path_value)
    if normalized.startswith(LEGACY_RAG_ROOT):
        suffix = normalized[len(LEGACY_RAG_ROOT):].lstrip("/\\")
        return str(RAG_ROOT / suffix) if suffix else str(RAG_ROOT)
    return normalized

def get_user_collection_name(user_id, config_id):
    """生成用户特定的Milvus集合名称"""
    if config_id == 'none':
        return 'none_placeholder'
    elif config_id == 'default':
        # 默认库对所有用户共享，但只读
        return 'global_default'
    else:
        # 用户私有库
        # 确保用户ID和配置ID合法
        safe_user_id = "".join(c for c in str(user_id) if c.isalnum() or c in '_-').strip('_-')
        safe_config_id = "".join(c for c in str(config_id) if c.isalnum() or c in '_-').strip('_-')
        return f"user_{safe_user_id}_{safe_config_id}"


def get_collection_db_name(user_id, config_id):
    """Return the Milvus database name for a user/config pair."""
    return f"rag_{get_user_collection_name(user_id, config_id)}"


def get_public_doc_id(file_path):
    """Return the stable public doc id used by the frontend."""
    normalized_path = os.path.abspath(file_path)
    return hashlib.md5(normalized_path.encode()).hexdigest()


def get_stable_doc_id(file_path):
    """Return the stable integer doc id used inside Milvus."""
    normalized_path = os.path.abspath(file_path)
    return int(hashlib.md5(normalized_path.encode()).hexdigest()[:8], 16)

def get_user_knowledge_path(user_id, config_id):
    """生成用户特定的知识库路径"""
    if config_id == 'none':
        return os.path.join(KNOWLEDGE_ROOT, "none")
    elif config_id == 'default':
        return os.path.join(KNOWLEDGE_ROOT, "global", "default")
    else:
        safe_user_id = "".join(c for c in str(user_id) if c.isalnum() or c in '_-').strip('_-')
        return os.path.join(KNOWLEDGE_ROOT, "users", f"user_{safe_user_id}", config_id)

def get_user_data_path(user_id, config_id):
    """生成用户特定的RAG数据路径"""
    if config_id == 'none':
        return os.path.join(DATA_ROOT, "none")
    elif config_id == 'default':
        return os.path.join(DATA_ROOT, "global", "default")
    else:
        safe_user_id = "".join(c for c in str(user_id) if c.isalnum() or c in '_-').strip('_-')
        return os.path.join(DATA_ROOT, "users", f"user_{safe_user_id}", config_id)

def is_readonly_config(config_id):
    """检查配置是否为只读"""
    return config_id == 'default'


def is_user_visible_config(config: dict, user_id: str) -> bool:
    """Return whether a config should be visible to the given user."""
    if not config:
        return False
    if config.get("id") in INTERNAL_COLLECTION_IDS:
        return False
    if config.get("readonly", False):
        return True
    owner_id = str(config.get("owner_id", "")).strip()
    if not owner_id:
        return True
    return owner_id == str(user_id)


def get_visible_configurations(user_id, active_collection_name=None, active_config_id=None):
    """Return user-visible configs with per-request active state."""
    visible_configs = []
    resolved_active_id = None

    if active_config_id and active_config_id in rag_configurations:
        config = rag_configurations.get(active_config_id)
        if is_user_visible_config(config, user_id):
            resolved_active_id = active_config_id

    if active_collection_name:
        for config_id, config in rag_configurations.items():
            if not is_user_visible_config(config, user_id):
                continue
            expected_collection = get_user_collection_name(user_id, config_id)
            if expected_collection == active_collection_name:
                resolved_active_id = config_id
                break

    if not resolved_active_id:
        for config_id, config in rag_configurations.items():
            if is_user_visible_config(config, user_id) and config.get("active", False):
                resolved_active_id = config_id
                break

    if not resolved_active_id and "default" in rag_configurations:
        resolved_active_id = "default"

    for config_id, config in rag_configurations.items():
        if not is_user_visible_config(config, user_id):
            continue
        config_copy = dict(config)
        config_copy["active"] = (config_id == resolved_active_id)
        visible_configs.append(config_copy)

    return visible_configs

def load_rag_configurations():
    """加载知识库配置"""
    global rag_configurations
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content: # 检查文件是否为空或只包含空白字符
                    logger.warning(f"配置文件 {CONFIG_FILE} 为空，将初始化为空配置。")
                    rag_configurations = {}
                else:
                    rag_configurations = json.loads(content) # 使用 json.loads 更安全
            logger.info(f"已加载 {len(rag_configurations)} 个知识库配置。")
        else:
            logger.info(f"配置文件 {CONFIG_FILE} 不存在，将创建新的空配置。")
            rag_configurations = {}
    except json.JSONDecodeError as jde:
        logger.error(f"加载知识库配置时JSON解析错误: {str(jde)}。文件 '{CONFIG_FILE}' 内容可能已损坏。将使用空配置。")
        rag_configurations = {} # JSON解析失败，重置为空
    except Exception as e:
        logger.error(f"加载知识库配置时发生未知错误: {str(e)}")
        traceback.print_exc()
        rag_configurations = {} # 其他未知错误，也重置为空


def save_rag_configurations():
    """保存知识库配置"""
    try:
        # 确保目录存在
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(rag_configurations, f, ensure_ascii=False, indent=2)
        logger.info("知识库配置已保存")
        
    except Exception as e:
        logger.error(f"保存知识库配置出错: {str(e)}")
        traceback.print_exc()

def initialize_knowledge_base_structure():
    """初始化知识库目录结构"""
    global rag_configurations # 使用本模块的全局变量

    # 确保基础目录存在
    os.makedirs(KNOWLEDGE_ROOT, exist_ok=True)
    os.makedirs(DATA_ROOT, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True) # 确保日志目录
    
    # 创建用户隔离的目录结构
    os.makedirs(os.path.join(KNOWLEDGE_ROOT, "users"), exist_ok=True)
    os.makedirs(os.path.join(KNOWLEDGE_ROOT, "global"), exist_ok=True)
    os.makedirs(os.path.join(KNOWLEDGE_ROOT, "none"), exist_ok=True)
    os.makedirs(os.path.join(DATA_ROOT, "users"), exist_ok=True)
    os.makedirs(os.path.join(DATA_ROOT, "global"), exist_ok=True)
    os.makedirs(os.path.join(DATA_ROOT, "none"), exist_ok=True)

    # 全局默认知识库的路径（所有用户共享，只读）
    default_knowledge_dir = os.path.join(KNOWLEDGE_ROOT, "global", "default")
    default_rag_dir = os.path.join(DATA_ROOT, "global", "default")
    none_knowledge_dir = os.path.join(KNOWLEDGE_ROOT, "none")
    none_rag_dir = os.path.join(DATA_ROOT, "none")
    os.makedirs(default_knowledge_dir, exist_ok=True)
    os.makedirs(default_rag_dir, exist_ok=True)

    load_rag_configurations() # 加载现有配置到 rag_configurations

    # 与Milvus数据库同步的逻辑 - 适配用户隔离
    try:
        if not is_milvus_available():
            logger.warning(
                "Milvus 当前不可达，跳过启动期数据库同步: %s",
                MILVUS_URI,
            )
            raise RuntimeError("milvus_unavailable")

        from pymilvus import MilvusClient
        client = MilvusClient(uri=MILVUS_URI)
        all_dbs = client.list_databases()
        logger.info(f"检测到 Milvus 数据库: {all_dbs}")

        # 只同步以前的旧格式配置，用户隔离的配置由用户动态创建
        for db_name in all_dbs:
            if db_name in INTERNAL_DB_NAMES:
                continue
            if db_name.startswith('rag_') and not db_name.startswith('rag_user_'):
                db_config_id = db_name[4:]
                if (
                    db_config_id not in rag_configurations
                    and db_config_id not in SYSTEM_CONFIG_IDS
                    and db_config_id not in INTERNAL_COLLECTION_IDS
                ):
                    logger.info(f"发现旧格式Milvus数据库 {db_name}，将转换为新格式配置。")
                    # 为检测到的旧数据库创建新配置 (这些现在变成模板配置)
                    new_config_knowledge_dir = os.path.join(KNOWLEDGE_ROOT, "users", "template", db_config_id)
                    new_config_rag_dir = os.path.join(DATA_ROOT, "users", "template", db_config_id)
                    os.makedirs(new_config_knowledge_dir, exist_ok=True)
                    os.makedirs(new_config_rag_dir, exist_ok=True)

                    creation_time = time.time() - (86400 * 2) # 默认为两天前
                    try:
                        if db_config_id.startswith('config_') and len(db_config_id.split('_')) > 1 and db_config_id.split('_')[1].isdigit():
                            timestamp = int(db_config_id.split('_')[1])
                            if 1000000000 < timestamp < time.time() + 86400: # 合理的时间戳范围
                                creation_time = timestamp
                    except Exception:
                        pass

                    rag_configurations[db_config_id] = {
                        'id': db_config_id,
                        'name': f'迁移的知识库 {db_config_id.split("_")[1]}' if "_" in db_config_id else f'迁移的知识库 {db_config_id}',
                        'display_name': f'迁移的知识库 {db_config_id.split("_")[1]}' if "_" in db_config_id else f'迁移的知识库 {db_config_id}',
                        'folder': new_config_knowledge_dir,
                        'db_path': new_config_rag_dir,
                        'active': False,
                        'created_time': creation_time,
                        'is_legacy': True  # 标记为旧格式配置
                    }
    except Exception as db_err:
        if str(db_err) == "milvus_unavailable":
            db_err = "Milvus unavailable during initialization"
        logger.warning(f"初始化时检查 Milvus 数据库出错: {str(db_err)}")

    # 清理误暴露到配置层的内部集合名，避免 UI 出现“迁移的知识库 default”。
    removed_internal_ids = []
    for internal_id in list(rag_configurations.keys()):
        if internal_id in INTERNAL_COLLECTION_IDS:
            removed_internal_ids.append(internal_id)
            del rag_configurations[internal_id]
    if removed_internal_ids:
        logger.info("已移除内部配置项: %s", removed_internal_ids)

    # 确保 'default' 配置存在（全局共享，只读）
    if 'default' not in rag_configurations:
        rag_configurations['default'] = {
            'id': 'default',
            'name': '默认知识库',
            'display_name': '默认知识库（全局只读）',
            'folder': default_knowledge_dir,
            'db_path': default_rag_dir,
            'active': False, # 稍后决定是否激活
            'created_time': time.time() - 86400, # 默认创建时间为一天前，以示区别
            'readonly': True  # 标记为只读
        }
        logger.info("在 initialize_knowledge_base_structure 中创建了默认知识库配置（全局只读）。")

    # 确保 'none' 配置存在（用于表示无知识库状态）
    if 'none' not in rag_configurations:
        rag_configurations['none'] = {
            'id': 'none',
            'name': '无',
            'display_name': '无',
            'folder': none_knowledge_dir,
            'db_path': none_rag_dir,
            'active': False,
            'created_time': time.time() - 86400 * 2,  # 创建时间为两天前
            'readonly': True  # 无知识库也是只读的（不能修改）
        }
        logger.info("在 initialize_knowledge_base_structure 中创建了'无'知识库配置。")

    # 规范化所有配置项，确保路径和必要字段存在
    for config_id_key, config_value in list(rag_configurations.items()): # 使用list允许迭代中修改
        # 确保ID正确
        if config_value.get('id') != config_id_key:
            logger.warning(f"配置ID与其键不匹配: {config_value.get('id')} vs {config_id_key}。将使用键作为ID。")
            config_value['id'] = config_id_key

        # 确保文件夹路径 - 根据新的用户隔离结构
        if config_id_key == 'default':
            config_value['folder'] = default_knowledge_dir
        elif config_id_key == 'none':
            config_value['folder'] = none_knowledge_dir
        elif not config_value.get('folder'):
            config_value['folder'] = os.path.join(KNOWLEDGE_ROOT, "users", "template", config_id_key)
        else:
            config_value['folder'] = normalize_local_rag_path(config_value['folder'])
        os.makedirs(config_value['folder'], exist_ok=True)

        if config_id_key == 'default':
            config_value['db_path'] = default_rag_dir
        elif config_id_key == 'none':
            config_value['db_path'] = none_rag_dir
        elif not config_value.get('db_path'):
            config_value['db_path'] = os.path.join(DATA_ROOT, "users", "template", config_id_key)
        else:
            config_value['db_path'] = normalize_local_rag_path(config_value['db_path'])
        os.makedirs(config_value['db_path'], exist_ok=True)

        if 'display_name' not in config_value:
            config_value['display_name'] = config_value.get('name', f'知识库 {config_id_key}')
        if 'created_time' not in config_value:
            config_value['created_time'] = time.time()
        if 'active' not in config_value: # 确保 active 字段存在
            config_value['active'] = False
        if 'readonly' not in config_value: # 确保 readonly 字段存在
            config_value['readonly'] = (config_id_key in ['default', 'none'])

    # 确保至少有一个配置是活跃的
    is_any_active = any(cfg.get('active', False) for cfg in rag_configurations.values())
    if not is_any_active:
        if 'default' in rag_configurations:
            rag_configurations['default']['active'] = True
            logger.info("在 initialize_knowledge_base_structure 中将默认知识库设为活跃，因无其他活跃库。")
        elif rag_configurations: # 如果没有default但有其他配置
            first_config_key = next(iter(rag_configurations))
            rag_configurations[first_config_key]['active'] = True
            logger.info(f"在 initialize_knowledge_base_structure 中将知识库 '{first_config_key}' 设为活跃。")

    save_rag_configurations() # 在所有操作完成后，统一保存一次
    logger.info(f"知识库结构初始化完成，当前共有 {len(rag_configurations)} 个配置。")
