"""
ColPali RAG Manager - 基于ColPali PDFMiner.six和Milvus的多模态知识库管理服务
- 通过Flask提供Web API服务
- 管理多个知识库配置并处理用户请求。
colpali_rag_manager.py
"""

import os
import sys
import time
import json
import logging
import atexit
import signal
import threading
import hashlib
import traceback
import requests
from pathlib import Path
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from contextlib import nullcontext

# 多实例配置
INSTANCE_ID = int(os.environ.get('INSTANCE_ID', '1'))
RAG_MANAGER_INSTANCE = int(os.environ.get('RAG_MANAGER_INSTANCE', '1'))
RAG_MANAGER_PORT = int(os.environ.get('RAG_MANAGER_PORT', '5006'))
GPU_ID = os.environ.get('GPU_ID', '0')

# 设置CUDA设备可见性，确保只使用指定的GPU
import os
os.environ['CUDA_VISIBLE_DEVICES'] = str(GPU_ID)

# 设置日志 - 实例特定
log_filename = f"logs/rag_manager_gpu{GPU_ID}_instance_{INSTANCE_ID}.log"
os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(f"ColPali-RAG-Manager-GPU{GPU_ID}-Instance{INSTANCE_ID}")
logging.getLogger('werkzeug').setLevel(logging.WARNING)

# 导入框架模块和新的用户管理器
from Rag_Framework import config_manager
from Rag_Framework.config_manager import (
    load_rag_configurations,
    save_rag_configurations,
    initialize_knowledge_base_structure,
    create_user_config,
    set_user_active_config,
    get_user_active_config,
    get_user_active_config_with_fallback,
    cleanup_invalid_user_configs,
    TCAD_PORT,
    # RAG_MANAGER_PORT,  # 注释掉，使用环境变量
    SOCKET_TIMEOUT,
    LOG_DIR,
    KNOWLEDGE_ROOT,
    DATA_ROOT,
    CONFIG_FILE,
    user_manager
)
from Rag_Framework.api_handler import APIHandler
from Rag_Framework.task_manager import get_task_manager
from Rag_Framework.service_communicator import get_service_communicator

# 创建全局引用，指向config_manager模块中的rag_configurations
rag_configurations = config_manager.rag_configurations

# 创建API处理器实例
api_handler = APIHandler(user_manager, config_manager)

# 创建任务管理器实例
task_manager = get_task_manager()

# 创建服务通信器实例
service_comm = get_service_communicator()

from Rag_Framework.utils import cleanup_socket_files, optimize_gpu_memory
from Rag_Framework.colpali_manager import ColPaliManager
from Rag_Framework.milvus_retriever import MilvusColbertRetriever

# 创建应用
app = Flask(__name__)
CORS(app)

# 简化全局状态管理
rag_instance = None
rag_lock = threading.RLock()
is_shutting_down = False
shutdown_start_time = None

# 为了向后兼容，保留旧的任务管理变量（将逐步迁移到新任务管理器）
processing_tasks = {}  # 临时保留，用于向后兼容
user_tasks = {}       # 临时保留，用于向后兼容
task_users = {}       # 临时保留，用于向后兼容

# 任务存储路径和用户会话状态
TASKS_STORAGE_PATH = os.path.join(DATA_ROOT, "tasks")
user_session_states = {}  # 用户会话状态缓存

# 确保任务存储目录存在
os.makedirs(TASKS_STORAGE_PATH, exist_ok=True)
os.makedirs(os.path.join(TASKS_STORAGE_PATH, "tasks"), exist_ok=True)

def save_user_tasks():
    """保存用户任务映射到文件"""
    try:
        os.makedirs(TASKS_STORAGE_PATH, exist_ok=True)
        user_tasks_file = os.path.join(TASKS_STORAGE_PATH, "user_tasks.json")
        with open(user_tasks_file, 'w', encoding='utf-8') as f:
            json.dump(user_tasks, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存用户任务映射失败: {str(e)}")

def get_rag_instance():
    """获取 RAG 实例，如果不存在则初始化"""
    global rag_instance
    
    with rag_lock:
        if rag_instance is None:
            initialize_rag()
            
        return rag_instance
        
def reset_rag_instance():
    """完全重置RAG实例并清理相关资源"""
    global rag_instance
    
    logger.info("开始重置ColPali RAG实例...")
    
    # 关闭现有实例
    if rag_instance is not None:
        try:
            rag_instance.close()
            logger.info("已关闭旧的ColPali RAG实例")
        except Exception as e:
            logger.error(f"关闭ColPali RAG实例时出错: {str(e)}")
    
    # 清理临时文件
    cleaned_files = cleanup_socket_files()
    if cleaned_files:
        logger.info(f"已清理 {len(cleaned_files)} 个套接字文件")
    
    # 重置实例
    rag_instance = None
    
    # 重新初始化
    initialize_success = initialize_rag()
    logger.info(f"ColPali RAG实例重置{'成功' if initialize_success else '失败'}")
    
    return initialize_success

def switch_knowledge_base(config_id, user_id='anonymous'):
    """切换知识库但保留模型实例"""
    global rag_instance
    
    logger.info(f"切换到知识库: {config_id}")
    
    with rag_lock:
        # 获取配置信息
        if config_id not in rag_configurations:
            logger.error(f"知识库配置 {config_id} 不存在")
            return False
                
        active_config = rag_configurations[config_id]
        
        # 生成用户特定的集合名称
        user_collection_name = config_manager.get_user_collection_name(user_id, config_id)
        
        # 特殊处理'none'配置 - 修复部分
        if config_id == 'none':
            logger.info(f"用户 {user_id} 切换到'无'知识库模式")
            
            # 设置用户的活跃配置（不再修改全局配置）
            set_user_active_config(user_id, config_id)
            
            # 对于'none'配置，需要清理RAG实例的状态，但不设置collection_name为None
            if rag_instance is not None:
                try:
                    # 清空文档集合
                    rag_instance.documents = {}
                    
                    # 重要修复：不要将collection_name设为None，而是使用一个占位符
                    if hasattr(rag_instance, 'retriever') and rag_instance.retriever:
                        # 使用特殊的占位符集合名称
                        rag_instance.retriever.collection_name = user_collection_name
                        
                        # 确保占位符集合存在但为空
                        try:
                            if not rag_instance.retriever.client.has_collection(user_collection_name):
                                # 临时创建占位符集合
                                rag_instance.retriever.collection_name = user_collection_name
                                rag_instance.retriever.create_collection()
                                logger.info(f"已创建占位符集合: {user_collection_name}")
                            else:
                                # 如果集合已存在，清空它
                                rag_instance.retriever.client.delete(
                                    collection_name=user_collection_name,
                                    filter=""  # 删除所有记录
                                )
                                rag_instance.retriever.client.flush(user_collection_name)
                                logger.info(f"已清空占位符集合: {user_collection_name}")
                        except Exception as placeholder_err:
                            logger.warning(f"处理占位符集合时出错: {str(placeholder_err)}")
                        
                        logger.info("已设置为无知识库模式（使用占位符集合）")
                except Exception as e:
                    logger.warning(f"清理RAG实例状态时出错: {str(e)}")
            
            logger.info("已成功切换到'无'知识库模式")
            return True
        
        # 如果RAG实例不存在，则完全初始化
        if rag_instance is None:
            logger.info("RAG 实例不存在，将进行完全初始化。")
            return initialize_rag()
                
        # 检查是否切换到当前已激活的知识库
        current_user_active_config = get_user_active_config(user_id)
        current_active_collection = None
        if hasattr(rag_instance, 'retriever') and rag_instance.retriever and \
           hasattr(rag_instance.retriever, 'collection_name') and rag_instance.retriever.collection_name:
            current_active_collection = rag_instance.retriever.collection_name
            
        # 如果已经是用户的活跃知识库，直接返回成功
        is_already_active = False
        if current_user_active_config == config_id and current_active_collection == user_collection_name:
            is_already_active = True
                    
        if is_already_active:
            logger.info(f"知识库 {config_id} 已是用户 {user_id} 当前活跃的知识库，无需执行切换流程。")
            return True
                
        # 设置用户的活跃配置（不再修改全局配置）
        logger.info(f"开始将知识库 {config_id} 设置为用户 {user_id} 的活跃状态")
        set_user_active_config(user_id, config_id)
        
        # 更新Milvus连接 - 使用用户特定的集合名称
        milvus_success = rag_instance.setup_milvus(collection_name=user_collection_name)
        
        if not milvus_success:
            logger.error(f"切换知识库时更新Milvus连接失败，集合: {user_collection_name}")
            return False
        
        # 验证集合可用性 - 使用简化的测试查询
        try:
            test_query = rag_instance.retriever.client.query(
                collection_name=user_collection_name,
                filter="seq_id == 0",
                output_fields=["doc_id"],
                limit=1
            )
            logger.info(f"集合 {user_collection_name} 已可用，测试查询成功")
        except Exception as query_err:
            logger.warning(f"测试查询失败，但继续处理: {str(query_err)}")
            
        # 文档同步逻辑 - 使用优化的同步方法
        try:
            # 使用优化的Standalone同步方法，传递用户特定的集合名称
            sync_success = rag_instance._sync_documents_standalone(user_collection_name)
            if sync_success:
                logger.info(f"已从Milvus同步文档信息，共 {len(rag_instance.documents)} 个文档")
            else:
                logger.warning(f"同步文档信息失败，将继续使用空文档集")
                # 确保有一个有效的空文档集
                rag_instance.documents = {}
        except Exception as sync_err:
            logger.error(f"同步过程中出错: {str(sync_err)}")
            # 确保有一个有效的空文档集
            rag_instance.documents = {}
            
        logger.info(f"已切换到知识库: {active_config['name']}")
        return True

# 移除旧的任务管理函数，使用新的任务管理器

def initialize_rag():
    """初始化或重新初始化ColPali RAG系统"""
    global rag_instance
    with rag_lock:
        if rag_instance is not None:
            try:
                rag_instance.close()
                logger.info("已关闭旧的ColPali RAG实例")
            except Exception as e:
                logger.error(f"关闭ColPali RAG实例时出错: {str(e)}")
        deleted_files = cleanup_socket_files()
        if deleted_files:
            logger.info(f"已删除 {len(deleted_files)} 个套接字文件")
        
        # 确保总是有正确的配置
        global rag_configurations
        # 重新从config_manager获取引用
        rag_configurations = config_manager.rag_configurations
        
        if not config_manager.rag_configurations:
            logger.error("RAG Configurations 在 initialize_rag 时为空！请检查初始化顺序。将尝试重新加载。")
            load_rag_configurations()
            # 重新建立引用
            rag_configurations = config_manager.rag_configurations
            if not config_manager.rag_configurations:
                logger.error("重新加载 RAG Configurations 失败。RAG 系统可能无法启动。")
                return False

        # === 迁移全局活跃配置到用户特定配置（一次性迁移）===
        migration_needed = config_manager.user_active_manager.migrate_from_global_active(rag_configurations)
        if migration_needed:
            save_rag_configurations()  # 保存去除全局活跃标志后的配置
            logger.info("已完成全局活跃配置到用户特定配置的迁移")

        # --- 确定默认用户的活跃配置 ---
        default_user_active_config = get_user_active_config('anonymous')
        selected_active_config = None
        selected_config_id = None
        
        if default_user_active_config and default_user_active_config in rag_configurations:
            # 使用默认用户的活跃配置
            selected_active_config = rag_configurations[default_user_active_config]
            selected_config_id = default_user_active_config
            logger.info(f"使用默认用户的活跃配置: {selected_active_config.get('name', selected_config_id)}")
        else:
            # 如果没有默认用户的活跃配置，选择默认配置
            if 'default' in rag_configurations:
                selected_active_config = rag_configurations['default']
                selected_config_id = 'default'
                set_user_active_config('anonymous', 'default')
                logger.info("设置默认用户的活跃配置为 'default'")
            elif 'none' in rag_configurations:
                selected_active_config = rag_configurations['none']
                selected_config_id = 'none'
                set_user_active_config('anonymous', 'none')
                logger.info("设置默认用户的活跃配置为 'none'")
            else:
                logger.error("无法确定活跃的知识库配置，RAG系统无法初始化。")
                return False
        # 如果代码执行到这里，说明 selected_active_config 和 selected_config_id 已成功确定
        logger.info(f"最终选定的活跃知识库: {selected_active_config.get('name', selected_config_id)} (ID: {selected_config_id})")

        # 使用 selected_active_config 和 selected_config_id 进行后续初始化
        config_id_to_use = selected_config_id # 或者 selected_active_config['id']
        active_config_details = selected_active_config
        # --- 结束修改确定活跃配置的逻辑 ---

        # 设置模型路径和Milvus数据库路径
        model_path = "/data/yphu/Web-FabGPT/LLM/RagManager/models/colpali/colpali-v1.3"
        base_model_path = "/data/yphu/Web-FabGPT/LLM/RagManager/models/colpali/paligemma-3b-mix-448"

        try:
            logger.info(f"正在初始化ColPali RAG系统，使用知识库：{active_config_details['name']}，ID：{config_id_to_use}")
            logger.info(f"使用Milvus Standalone模式")
            logger.info(f"知识库文件夹: {active_config_details['folder']}")
            logger.info(f"RAG数据文件夹 (db_path): {active_config_details['db_path']}")
            logger.info(f"使用GPU: {GPU_ID}")

            rag_instance = ColPaliManager(
                model_path=model_path,
                base_model_path=base_model_path,
                device=f"cuda:0"  # 使用相对GPU ID 0，因为CUDA_VISIBLE_DEVICES已设置
            )
            # rag_instance.config_dir = rag_configurations # 这一行可能不再需要，或者需要确认其用途
            # ColPaliManager 是否真的需要整个配置字典的引用，
            # 还是只需要当前活跃的配置信息？

            model_success = rag_instance.load_model()
            if not model_success:
                logger.error("ColPali模型加载失败")
                return False
            
            # 对于初始化，使用默认用户的集合名称
            default_user_collection_name = config_manager.get_user_collection_name('anonymous', config_id_to_use)
            
            milvus_success = rag_instance.setup_milvus(collection_name=default_user_collection_name)
            if not milvus_success:
                logger.error("Milvus设置失败")
                return False
            
            rag_instance.documents = {}

            # 检查默认用户的集合
            try:
                stats = rag_instance.retriever.client.get_collection_stats(default_user_collection_name)
                row_count = stats.get("row_count", 0)
                
                if row_count > 0:
                    logger.info(f"集合 {default_user_collection_name} 中有 {row_count} 行数据，开始同步")
                    sync_success = rag_instance._sync_documents_standalone(default_user_collection_name)
                    if sync_success:
                        logger.info(f"已从Milvus同步文档信息，共 {len(rag_instance.documents)} 个文档")
                    else:
                        logger.warning("Standalone同步失败，将使用空文档集")
                        rag_instance.documents = {}
                else:
                    logger.info(f"集合 {default_user_collection_name} 为空，跳过同步操作")
                    rag_instance.documents = {}
            except Exception as stats_err:
                logger.warning(f"获取集合统计信息失败: {str(stats_err)}，将使用空文档集")
                rag_instance.documents = {}
            
            logger.info("ColPali RAG系统初始化成功")
            return True
        except Exception as e:
            logger.error(f"初始化ColPali RAG系统时出错: {str(e)}")
            traceback.print_exc()
            return False

# API端点
@app.route('/get_rag_configurations', methods=['GET'])
def get_rag_configurations():
    """获取用户可见的知识库配置，包含用户特定的活跃状态"""
    try:
        user_id, _ = api_handler.extract_user_and_config()
        
        # 使用用户管理器过滤配置（返回List[Dict]格式）
        filtered_configs_list = user_manager.filter_configs_for_user(user_id, rag_configurations)
        
        # 转换为Dict格式，方便活跃配置处理
        filtered_configs_dict = {cfg['id']: cfg for cfg in filtered_configs_list}
        
        # 获取用户的活跃配置
        user_active_config = get_user_active_config_with_fallback(user_id, filtered_configs_dict)
        
        # 为List中的每个配置设置正确的活跃状态
        for config in filtered_configs_list:
            config['active'] = (config['id'] == user_active_config)
        
        # 返回List格式，保持前端兼容性
        return api_handler.create_success_response({
            'configurations': filtered_configs_list,  # 保持数组格式
            'count': len(filtered_configs_list),
            'user_id': user_id,
            'active_config': user_active_config
        })
    except Exception as e:
        return api_handler.handle_api_error('获取知识库配置', e)

@app.route('/get_user_active_config', methods=['GET'])
def get_user_active_config_api():
    """获取用户的活跃配置API"""
    try:
        # 从查询参数获取用户ID
        user_id = request.args.get('user_id')
        
        if not user_id:
            return jsonify({
                'success': False,
                'error': '缺少user_id参数',
                'active_config': None
            }), 400
        
        # 获取用户可见的配置
        filtered_configs_dict = user_manager.filter_configs_for_user(user_id, rag_configurations)
        
        # 获取用户的活跃配置
        user_active_config = get_user_active_config_with_fallback(user_id, filtered_configs_dict)
        
        logger.info(f"查询用户 {user_id} 的活跃配置: {user_active_config}")
        
        return jsonify({
            'success': True,
            'user_id': user_id,
            'active_config': user_active_config,
            'available_configs': list(filtered_configs_dict.keys()) if isinstance(filtered_configs_dict, dict) else []
        }), 200
        
    except Exception as e:
        logger.error(f'获取用户活跃配置时出错: {str(e)}')
        return jsonify({
            'success': False,
            'error': str(e),
            'active_config': None
        }), 500

@app.route('/create_rag_configuration', methods=['POST'])
def create_rag_configuration():
    """创建用户知识库配置"""
    try:
        user_id, _ = api_handler.extract_user_and_config()
        data = request.get_json() or {}
        name = data.get('name')
        is_sync_request = data.get('is_sync_request', False)  # 标记是否为同步请求
        
        logger.info(f"创建知识库配置请求: user_id={user_id}, name={name}, is_sync_request={is_sync_request}")
        
        if not name:
            return api_handler.create_error_response('名称不能为空')
        
        # 使用简化的配置创建函数
        new_config = create_user_config(user_id, name)
        logger.info(f"成功创建配置: {new_config['id']}")
        
        # 如果不是同步请求，则同步到其他RAG Manager实例
        if not is_sync_request:
            logger.info(f"开始同步配置到其他实例: {name}")
            sync_config_to_other_instances(user_id, name, 'create')
            logger.info(f"完成同步配置: {name}")
        else:
            logger.info(f"跳过同步(同步请求): {name}")
        
        return api_handler.create_success_response({
            'message': f"已创建知识库配置: {name}",
            'config': new_config
        })
    except Exception as e:
        logger.error(f"创建知识库配置失败: {str(e)}")
        traceback.print_exc()
        return api_handler.handle_api_error('创建知识库配置', e)

def sync_config_to_other_instances(user_id, name, action_type):
    """同步配置到其他RAG Manager实例 (并行处理)"""
    try:
        import concurrent.futures
        import threading
        
        # RAG Manager 8实例端口 - 修正为完整的8个实例
        rag_ports = [5006, 5016, 5026, 5036, 5046, 5056, 5066, 5076]
        current_port = RAG_MANAGER_PORT  # 使用正确的变量名
        
        # 从当前请求获取Cookie信息用于同步
        cookie_header = request.headers.get('Cookie', '')
        headers = {
            'Content-Type': 'application/json',
            'Cookie': cookie_header
        }
        
        logger.info(f"开始并行同步到其他实例，当前端口: {current_port}")
        
        def sync_to_instance(port):
            """向单个实例发送同步请求"""
            if port == current_port:  # 不向自己发送请求
                return port, "skipped", "自身实例跳过"
                
            try:
                sync_data = {
                    'name': name,
                    'user_id': user_id,  # 添加用户ID到同步数据
                    'is_sync_request': True  # 标记为同步请求，避免无限循环
                }
                
                if action_type == 'create':
                    url = f"http://127.0.0.1:{port}/create_rag_configuration"
                elif action_type == 'delete':
                    url = f"http://127.0.0.1:{port}/delete_rag_configuration"
                    sync_data['config_id'] = name  # 对于删除，name实际上是config_id
                else:
                    return port, "error", f"不支持的操作类型: {action_type}"
                
                logger.info(f"向实例{port}发送同步请求: {action_type} {name}")
                response = requests.post(url, json=sync_data, headers=headers, timeout=30)
                if response.status_code == 200:
                    logger.info(f"成功同步配置到实例{port}: {action_type} {name}")
                    return port, "success", f"HTTP {response.status_code}"
                else:
                    logger.warning(f"同步配置到实例{port}失败: {response.status_code}")
                    return port, "error", f"HTTP {response.status_code}"
                    
            except Exception as e:
                logger.warning(f"同步配置到实例{port}时出错: {str(e)}")
                return port, "error", str(e)
        
        # 使用线程池并行处理所有实例
        sync_results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            # 提交所有任务
            future_to_port = {executor.submit(sync_to_instance, port): port for port in rag_ports}
            
            # 收集结果
            for future in concurrent.futures.as_completed(future_to_port):
                port = future_to_port[future]
                try:
                    port_result, status, message = future.result()
                    sync_results[port_result] = {'status': status, 'message': message}
                except Exception as exc:
                    logger.error(f'实例{port}同步时产生异常: {exc}')
                    sync_results[port] = {'status': 'error', 'message': str(exc)}
        
        # 统计结果
        success_count = sum(1 for result in sync_results.values() if result['status'] == 'success')
        skipped_count = sum(1 for result in sync_results.values() if result['status'] == 'skipped')
        error_count = sum(1 for result in sync_results.values() if result['status'] == 'error')
        
        logger.info(f"并行同步完成: 成功 {success_count}, 跳过 {skipped_count}, 失败 {error_count}")
        
        # 如果有错误，记录详细信息
        if error_count > 0:
            error_ports = [str(port) for port, result in sync_results.items() if result['status'] == 'error']
            logger.warning(f"同步失败的实例: {', '.join(error_ports)}")
                    
    except Exception as e:
        logger.error(f"并行同步配置到其他实例时出错: {str(e)}")
        import traceback
        traceback.print_exc()

@app.route('/set_active_configuration', methods=['POST'])
def set_active_configuration():
    """设置活跃的知识库配置"""
    try:
        data = request.json
        config_id = data.get('config_id')
        user_id = data.get('user_id', 'anonymous')  # 添加用户ID支持
        is_sync_request = data.get('is_sync_request', False)  # 标记是否为同步请求
        
        if not config_id:
            return jsonify({'error': '未提供配置ID'}), 400
        
        # 检查配置是否存在
        if config_id not in rag_configurations:
            return jsonify({'error': f'配置ID无效: {config_id}'}), 400
        
        # 检查权限：默认库只读
        if config_id == 'default' and user_id != 'admin':
            logger.info("切换到默认库（只读模式）")
        
        # 使用switch_knowledge_base函数切换知识库，传递用户ID
        switch_success = switch_knowledge_base(config_id, user_id)
        
        # 同步通知TCAD和Chatbot服务，但仅当不是同步请求时
        sync_status = {'tcad': 'Not attempted', 'chatbot': 'Not attempted'}
        
        if switch_success and not is_sync_request:
            try:
                import requests
                
                # 通知TCAD服务
                try:
                    tcad_response = requests.post(
                        f"http://10.98.64.22:{TCAD_PORT}/set_active_configuration",
                        json={"config_id": config_id, "is_sync_request": True},
                        timeout=3
                    )
                    sync_status['tcad'] = tcad_response.status_code
                    logger.info(f"通知TCAD服务切换知识库结果: {tcad_response.status_code}")
                except Exception as tcad_err:
                    sync_status['tcad'] = f"Error: {str(tcad_err)}"
                    logger.warning(f"通知TCAD服务时出错: {str(tcad_err)}")
                
                # 通知智能助手服务(端口5005)
                try:
                    chatbot_response = requests.post(
                        "http://10.98.64.22:5002/set_active_configuration",
                        json={"config_id": config_id, "is_sync_request": True},
                        timeout=3
                    )
                    sync_status['chatbot'] = chatbot_response.status_code
                    logger.info(f"通知智能助手服务切换知识库结果: {chatbot_response.status_code}")
                except Exception as chatbot_err:
                    sync_status['chatbot'] = f"Error: {str(chatbot_err)}"
                    logger.warning(f"通知智能助手服务时出错: {str(chatbot_err)}")
                    
            except Exception as notify_err:
                logger.warning(f"通知其他服务切换知识库时出错: {str(notify_err)}")
                sync_status = {'error': str(notify_err)}
        
        if switch_success:
            return jsonify({
                'message': f"已将 '{rag_configurations[config_id]['name']}' 设置为当前知识库",
                'active_config': rag_configurations[config_id],
                'sync_status': sync_status
            }), 200
        else:
            return jsonify({
                'message': f"已将 '{rag_configurations[config_id]['name']}' 设置为当前知识库,但切换过程中出现问题",
                'active_config': rag_configurations[config_id],
                'warning': '知识库切换过程中遇到问题,部分功能可能受限',
                'sync_status': sync_status
            }), 200
    except Exception as e:
        logger.error(f'设置活跃知识库配置错误: {str(e)}')
        traceback.print_exc()
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/delete_rag_configuration', methods=['POST'])
def delete_rag_configuration():
    """删除知识库配置"""
    try:
        user_id, _ = api_handler.extract_user_and_config()
        data = request.json
        config_id = data.get('config_id')
        force_delete = data.get('force_delete', False)
        is_sync_request = data.get('is_sync_request', False)  # 标记是否为同步请求
        
        if not config_id:
            return jsonify({'error': '未提供配置ID'}), 400
        
        # 不允许删除默认配置
        if config_id == 'default' and not force_delete:
            return jsonify({'error': '默认知识库配置不能删除'}), 400
        
        # 如果配置ID不存在
        if config_id not in rag_configurations:
            return jsonify({'error': '知识库配置不存在'}), 404
        
        # 保存要删除的配置信息
        deleted_config = rag_configurations[config_id].copy()
        
        # 如果删除的是活跃配置，将默认配置设为活跃
        if rag_configurations[config_id].get('active', False):
            rag_configurations['default']['active'] = True
        
        # 首先关闭 RAG 实例
        global rag_instance
        if rag_instance is not None:
            try:
                rag_instance.close()
                rag_instance = None
                logger.info("已关闭 RAG 实例以释放资源")
            except Exception as close_err:
                logger.error(f"关闭 RAG 实例时出错: {str(close_err)}")
        
        # 清理所有套接字文件
        cleanup_socket_files()
        
        # 收集要删除的文件夹
        folders_to_delete = []
        
        # 知识库文件夹
        knowledge_folder = os.path.abspath(deleted_config.get('folder', ''))
        if knowledge_folder and os.path.exists(knowledge_folder) and os.path.isdir(knowledge_folder):
            folders_to_delete.append(knowledge_folder)
        
        # RAG数据文件夹
        rag_data_folder = os.path.abspath(deleted_config.get('db_path', os.path.join(DATA_ROOT, config_id)))
        if os.path.exists(rag_data_folder) and os.path.isdir(rag_data_folder):
            folders_to_delete.append(rag_data_folder)
            
        # 从配置中删除配置项
        del rag_configurations[config_id]
        
        # 保存配置
        save_rag_configurations()
        
        # 执行文件夹删除
        deleted_folders = []
        errors = []
        
        for folder in folders_to_delete:
            try:
                import shutil
                shutil.rmtree(folder, ignore_errors=True)
                deleted_folders.append(folder)
            except Exception as e:
                errors.append(f"删除文件夹失败: {folder}, 错误: {str(e)}")
                # 尝试使用系统命令
                try:
                    import subprocess
                    subprocess.run(['rm', '-rf', folder], check=True)
                    deleted_folders.append(folder)
                    errors.pop()  # 移除之前的错误
                except Exception as cmd_err:
                    errors.append(f"系统命令删除失败: {folder}, 错误: {str(cmd_err)}")
        
        # 尝试删除对应的 Milvus 数据库
        milvus_db_deleted = False
        db_delete_error = None
        try:
            from pymilvus import MilvusClient
            client = MilvusClient(uri="http://localhost:19530")
            db_name_to_delete = f"rag_{config_id}"
            # 检查数据库是否存在
            all_dbs = client.list_databases()
            logger.info(f"现有Milvus数据库: {all_dbs}")
            if db_name_to_delete in all_dbs:
                logger.info(f"正在处理 Milvus 数据库: {db_name_to_delete}")
                # 在删除前执行压缩操作,清理已删除的数据
                try:
                    # 切换到目标数据库
                    client.use_database(db_name_to_delete)
                    
                    # 压缩集合
                    if client.has_collection(collection_name=config_id):
                        logger.info(f"对集合 {config_id} 执行压缩操作")
                        compact_result = client.compact(config_id)
                        logger.info(f"压缩结果: {compact_result}")
                        
                        # 刷新集合 
                        client.flush(config_id)  # 修改为直接传入字符串
                        logger.info(f"已刷新集合 {config_id}")
                except Exception as compact_err:
                    logger.warning(f"压缩集合失败: {str(compact_err)}")

                # 切换到目标数据库
                client.use_database(db_name_to_delete)
                logger.info(f"已切换到数据库: {db_name_to_delete}")

                # 列出并删除该数据库中的所有 collection
                collections_in_db = client.list_collections()
                logger.info(f"数据库 '{db_name_to_delete}' 中的 collections: {collections_in_db}")

                for collection_name in collections_in_db:
                    try:
                        logger.info(f"正在删除 collection: {collection_name} 在数据库 {db_name_to_delete} 中")
                        client.drop_collection(collection_name)
                        logger.info(f"Collection {collection_name} 已删除")
                    except Exception as coll_e:
                        logger.error(f"删除 collection {collection_name} 失败: {str(coll_e)}")

                # 返回到默认数据库
                client.use_database("default")
                logger.info("已切换回 default 数据库")

                # 再次检查数据库是否存在
                all_dbs_after_collections_drop = client.list_databases()
                if db_name_to_delete in all_dbs_after_collections_drop:
                     # 删除数据库
                    logger.info(f"正在删除 Milvus 数据库: {db_name_to_delete}")
                    client.drop_database(db_name=db_name_to_delete)
                    milvus_db_deleted = True
                    logger.info(f"Milvus 数据库 {db_name_to_delete} 已删除")
                else:
                    logger.info(f"Milvus 数据库 {db_name_to_delete} 已不存在")
                    milvus_db_deleted = True

            else:
                logger.info(f"Milvus 数据库 {db_name_to_delete} 不存在或已被删除")
                milvus_db_deleted = True

        except Exception as e:
            db_delete_error = f"删除 Milvus 数据库或其 collections 失败: {str(e)}"
            logger.error(db_delete_error)
            traceback.print_exc()
        
        # 重新初始化 RAG 实例
        initialize_rag()
        
        # 判断是否完全成功
        fully_success = len(errors) == 0 and milvus_db_deleted
        partial_success = (len(deleted_folders) > 0 or milvus_db_deleted) and (len(errors) > 0 or not milvus_db_deleted)
        
        # 如果不是同步请求，则同步到其他RAG Manager实例
        if not is_sync_request:
            try:
                sync_config_to_other_instances(user_id, config_id, 'delete')
            except Exception as sync_err:
                logger.warning(f"同步删除配置到其他实例时出错: {str(sync_err)}")
        
        return jsonify({
            'message': f"知识库配置 '{deleted_config['name']}' 已删除",
            'deleted': True,
            'success': True,
            'fully_success': fully_success,
            'partial_success': partial_success,
            'deleted_folders': deleted_folders,
            'errors': errors,
            'milvus_db_deleted': milvus_db_deleted,
            'db_delete_error': db_delete_error
        }), 200
        
    except Exception as e:
        logger.error(f'删除知识库配置错误: {str(e)}')
        traceback.print_exc()
        return jsonify({
            'message': f"删除过程中出现错误，但配置可能已被移除",
            'error': str(e),
            'success': True,
            'deleted': True
        }), 200

@app.route('/get_rag_documents', methods=['GET'])
def get_rag_documents():
    """获取 RAG 数据库内容列表"""
    try:
        # 从请求中获取查询参数
        config_id = request.args.get('config_id', 'default')
        user_id = request.args.get('user_id', 'anonymous')  # 添加用户ID支持
        force_refresh = request.args.get('force_refresh', 'false').lower() == 'true'
        recovery_mode = request.args.get('recovery_mode', 'false').lower() == 'true'
        
        # 如果配置 ID 不存在，返回错误
        if config_id not in rag_configurations:
            return jsonify({'error': f'配置ID无效: {config_id}'}), 400
            
        # 获取配置信息
        config = rag_configurations[config_id]
        
        # 生成用户特定的集合名称
        user_collection_name = config_manager.get_user_collection_name(user_id, config_id)
        
        # 特殊处理 'none' 配置
        if config_id == 'none':
            return jsonify({
                'documents': [],
                'milvus_status': {
                    'collection_name': user_collection_name,
                    'database_name': 'none',
                    'row_count': 0,
                    'connected': False,
                    'message': '当前为无知识库模式'
                },
                'documents_count': 0,
                'processing_count': 0,
                'processing_pages': 0,
                'total_pages_count': 0,
                'total_chunks': 0,
                'config': config,
                'diagnostics': None,
                'database_name': 'none',
                'collection_name': user_collection_name
            }), 200
        
        # 获取 RAG 实例
        rag = get_rag_instance()
        if not rag:
            return jsonify({
                'error': 'RAG 系统未初始化', 
                'recovery_mode': recovery_mode,
                'documents': []
            }), 500
            
        # 使用Standalone模式
        logger.info(f"使用Milvus Standalone模式")
        
        # 在查询文档前，切换到用户特定的集合
        # 这保证了重新登录时也能获取到正确的用户文档数据
        if hasattr(rag, 'retriever') and hasattr(rag.retriever, 'client'):
            try:
                # 确保使用用户特定的集合
                if rag.retriever.collection_name != user_collection_name:
                    logger.info("切换到用户特定集合")
                    # 切换到用户特定的知识库
                    switch_success = switch_knowledge_base(config_id, user_id)
                    if not switch_success:
                        logger.warning(f"切换到用户集合失败: {user_collection_name}")
                
                # 确保集合已加载
                if hasattr(rag.retriever.client, 'has_collection') and rag.retriever.client.has_collection(user_collection_name):
                    rag.retriever.client.load_collection(user_collection_name)
                    logger.info("已加载用户集合")
                    
                    # 获取文档记录数以验证
                    stats = rag.retriever.client.get_collection_stats(user_collection_name)
                    row_count = stats.get("row_count", 0)
                    logger.info(f"数据库中共有 {row_count} 条记录")
                    
                    # 不仅依赖内存中的文档记录，直接查询Milvus获取所有文档信息
                    if row_count > 0 and (force_refresh or not hasattr(rag, 'documents') or not rag.documents):
                        # 同步用户特定的文档信息
                        sync_result = rag._sync_documents_standalone(user_collection_name)
                        logger.info(f"同步文档信息{'成功' if sync_result else '失败'}")
                else:
                    logger.info("用户集合不存在，将创建")
            except Exception as load_err:
                logger.error(f"加载用户文档信息失败: {str(load_err)}")
            
        # 获取当前数据库和集合名称
        database_name = f"rag_{config_id}"
        collection_name = config_id
        
        logger.info(f"当前使用的数据库: {database_name}, 集合: {collection_name}")
            
        # 模拟从知识库获取文档列表逻辑
        documents_info = []
        
        # 使用用户特定的知识库路径
        user_folder_path = config_manager.get_user_knowledge_path(user_id, config_id)
        folder_path = os.path.abspath(user_folder_path)
        
        # 确保用户目录存在
        os.makedirs(folder_path, exist_ok=True)
        
        try:
            file_list = os.listdir(folder_path)
        except Exception as e:
            logger.error(f"无法读取知识库目录 '{folder_path}': {str(e)}")
            file_list = []
        
        # 扫描目录中的文件
        supported_extensions = ['.pdf', '.py', '.sh', '.cmd', '.md', '.txt']
        for file in file_list:
            file_ext = os.path.splitext(file)[1].lower()
            if file_ext in supported_extensions:
                file_path = os.path.join(folder_path, file)
                
                try:
                    file_stat = os.stat(file_path)
                    
                    # 生成文档ID
                    doc_id = hashlib.md5(file_path.encode()).hexdigest()
                    
                    # 检查是否已处理 - 尝试多种方式获取处理状态
                    hash_obj = hashlib.md5(file_path.encode())
                    doc_id_int = int(hash_obj.hexdigest(), 16) % (10 ** 9)
                    # Default values
                    total_pages = 0 
                    chunks_count = 0 
                    processed_status = "unprocessed"
                    text_content_preview = "" 
                    # 1. 尝试从PDF文件本身获取总页数
                    try:
                        file_ext = os.path.splitext(file)[1].lower()
                        if file_ext in supported_extensions:
                            from PyPDF2 import PdfReader
                            reader = PdfReader(file_path)
                            total_pages = len(reader.pages)
                            logger.info(f"从PDF文件读取总页数: {file_path}, 页数: {total_pages}")
                    except Exception as e:
                        logger.warning(f"无法从PDF读取总页数 ({file_path}): {str(e)}")
                    
                    # 2. 尝试从内存中的rag.documents获取处理状态和块数
                    doc_info_mem = None
                    # Use the integer doc_id as the primary key in rag.documents
                    if hasattr(rag, 'documents') and doc_id_int in rag.documents:
                        doc_info_mem = rag.documents[doc_id_int]
                    if doc_info_mem:
                        # Use values from the in-memory object if available
                        if total_pages == 0: 
                            total_pages = getattr(doc_info_mem, 'page_count', 0)
                        
                        chunks_count = getattr(doc_info_mem, 'processed_count', 0)
                        processed_status = getattr(doc_info_mem, 'status', "processed" if chunks_count > 0 else "unprocessed")
                        # Check if a processing task is active for this file
                        task_exists = any(task.get('file_path') == file_path and task.get('status') == 'processing' for task in processing_tasks.values())
                        if task_exists:
                            processed_status = "processing"
                            # If processing, get current page info from task
                            for task in processing_tasks.values():
                                if task.get('file_path') == file_path and task.get('status') == 'processing':
                                    if task.get('total_pages', 0) > 0:
                                        total_pages = task['total_pages']
                                    chunks_count = task.get('processed_pages', 0)
                                    break
                    else:
                        # 3. 尝试从Milvus查询块数和部分信息
                        if hasattr(rag.retriever, 'client'):
                            try:
                                # Query Milvus using the integer doc_id prefix
                                filter_expr = f"doc_id >= {doc_id_int * 1000} AND doc_id < {(doc_id_int + 1) * 1000}"
                                milvus_results = rag.retriever.client.query(
                                    collection_name=rag.retriever.collection_name,
                                    filter=filter_expr,
                                    output_fields=["doc_id", "page_num", "text_content"],
                                    limit=5000
                                )
                                
                                if milvus_results:
                                    chunks_count = len(milvus_results)
                                    processed_status = "processed"
                                    # Try to get text content preview from one of the results
                                    if chunks_count > 0 and 'text_content' in milvus_results[0]:
                                        # Use the text content from one of the indexed chunks
                                        text_content_preview = milvus_results[0]['text_content']
                                    logger.info(f"从Milvus获取到文档 {file_path} 的 {chunks_count} 个块")
                                else:
                                    processed_status = "unprocessed"
                            except Exception as e:
                                logger.debug(f"查询Milvus集合时出错: {str(e)}")
                        # 4. 再次检查是否有处理任务正在进行
                        task_exists = any(task.get('file_path') == file_path and task.get('status') == 'processing' for task in processing_tasks.values())
                        if task_exists:
                            processed_status = "processing"
                            # If processing, get current page info from task
                            for task in processing_tasks.values():
                                if task.get('file_path') == file_path and task.get('status') == 'processing':
                                    if task.get('total_pages', 0) > 0:
                                        total_pages = task['total_pages']
                                    chunks_count = task.get('processed_pages', 0)
                                    break
                        # 5. 如果总页数仍然是0，使用文件大小估算
                        if total_pages == 0:
                            total_pages = max(1, file_stat.st_size // (150 * 1024))
                            logger.warning(f"使用文件大小估算页数: {file_path}, 估算页数: {total_pages}")
                    # Ensure chunks_count doesn't exceed total_pages
                    if chunks_count > total_pages and total_pages > 0:
                        chunks_count = total_pages
                    # Generate text preview from fetched text_content_preview
                    text_preview = text_content_preview[:200] + "..." if len(text_content_preview) > 200 else text_content_preview
                    # 构建文档信息字典
                    documents_info.append({
                        'doc_id': doc_id,
                        'filename': file,
                        'file_path': file_path,
                        'last_modified': file_stat.st_mtime,
                        'last_modified_time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(file_stat.st_mtime)),
                        'file_size': file_stat.st_size,
                        'file_exists': True,
                        'file_size_formatted': f"{file_stat.st_size / 1024 / 1024:.2f} MB",
                        'images_count': total_pages,
                        'chunks_count': chunks_count,
                        'processed': processed_status == "processed",
                        'is_processing': processed_status == "processing",  # 添加明确的处理中标志
                        'config_id': config_id,
                        'status': processed_status,
                        'text_preview': text_preview,
                        'has_text': bool(text_content_preview),
                        'total_pages': total_pages,
                        'int_doc_id': doc_id_int
                    })
                except Exception as file_err:
                    logger.error(f"处理文件 {file} 时出错: {str(file_err)}")
        
        # 计算总页数和总块数
        processed_docs = [doc for doc in documents_info if 
                  doc.get('processed') or 
                  doc.get('status') == 'processed' or 
                  doc.get('chunks_count', 0) > 0 or
                  doc.get('images_count', 0) > 0]

        # 确保使用正确的字段名
        total_pages_count = 0
        for doc in processed_docs:
            # 优先使用total_pages，如果没有则使用images_count
            page_count = doc.get('total_pages', 0) or doc.get('images_count', 0)
            total_pages_count += page_count
            
        total_chunks_count = sum(doc.get('chunks_count', 0) or doc.get('images_count', 0) 
                                for doc in processed_docs)
        
        # 使用实际已处理文档数
        actual_processed_count = len(processed_docs)

        # 添加处理中的文档计数
        processing_docs = [doc for doc in documents_info if doc.get('status') == 'processing']
        processing_count = len(processing_docs)
        processing_pages = sum(doc.get('total_pages', 0) for doc in processing_docs)
        
        if processing_count > 0:
            logger.info(f"发现 {processing_count} 个处理中的文档，总计 {processing_pages} 页")
        
        # 获取 Milvus 状态
        milvus_status = {}
        try:
            if hasattr(rag.retriever, 'client'):
                # 查询集合统计信息
                try:
                    stats = rag.retriever.client.get_collection_stats(rag.retriever.collection_name)
                    row_count = stats.get("row_count", 0)
                    
                    # 获取PDF文本处理统计信息
                    text_cache_size = len(rag.retriever.text_cache) if hasattr(rag.retriever, 'text_cache') else 0
                    pdf_text_extracted_count = rag.retriever.pdf_text_extracted_count if hasattr(rag.retriever, 'pdf_text_extracted_count') else 0
                    
                    # 获取数据库信息
                    db_info = {}
                    try:
                        db_list = rag.retriever.client.list_databases()
                        db_info = {
                            "databases": db_list,
                            "current_db": database_name
                        }
                    except Exception as db_err:
                        logger.warning(f"获取数据库列表失败: {str(db_err)}")
                    
                    # 检查最近完成的任务
                    recent_tasks = [task for task in processing_tasks.values() 
                                   if task.get('status') == 'completed' and 
                                   (time.time() - task.get('start_time', 0)) < 300]  # 5分钟内完成的任务
                    
                    milvus_status = {
                        'collection_name': rag.retriever.collection_name,
                        'database_name': database_name,
                        'row_count': row_count,
                        'index_type': "已建立",
                        'connected': True,
                        'text_cache_size': text_cache_size,
                        'pdf_text_extracted_count': pdf_text_extracted_count,
                        'database_info': db_info,
                        'recent_changes': len(recent_tasks) > 0  # 添加最近变更标记
                    }
                except Exception as stats_error:
                    logger.error(f"获取集合统计信息失败: {str(stats_error)}")
                    milvus_status = {
                        'collection_name': rag.retriever.collection_name,
                        'error': str(stats_error),
                        'connected': False,
                    }
        except Exception as milvus_err:
            logger.error(f"获取 Milvus 状态出错: {str(milvus_err)}")
            milvus_status = {'error': str(milvus_err), 'connected': False}
        
        # 诊断信息
        diagnostics = None
        if force_refresh:
            diagnostics = {
                "missing_files": [],
                "untracked_files": [],
                "processing_files": []
            }
            
            # 检查是否有文件缺失
            for doc in documents_info:
                if not os.path.exists(doc['file_path']):
                    diagnostics["missing_files"].append(doc['filename'])
                elif doc['status'] == 'processing':
                    diagnostics["processing_files"].append(doc['filename'])
        
        # 构建响应
        return jsonify({
            'documents': sorted(documents_info, key=lambda x: x['last_modified'], reverse=True),
            'milvus_status': milvus_status,
            'documents_count': len(documents_info),
            'processing_count': processing_count,  # 处理中文档数量
            'processing_pages': processing_pages,  # 处理中文档的总页数
            'total_pages_count': total_pages_count,
            'total_chunks': total_chunks_count,
            'config': config,
            'diagnostics': diagnostics,
            'database_name': database_name,
            'collection_name': collection_name
        }), 200
        
    except Exception as e:
        logger.error(f'获取 RAG 文档列表错误: {e}')
        traceback.print_exc()
        return jsonify({
            'error': str(e), 
            'documents': [],
            'recovery_mode': True,
            'milvus_status': {'connected': False, 'error': str(e)}
        }), 500

@app.route('/upload_rag_document', methods=['POST'])
def upload_rag_document():
    """上传 RAG 知识库文档接口"""
    try:
        file = request.files['file']
        config_id = request.form.get('config_id', 'default')
        user_id = request.form.get('user_id', 'anonymous')  # 获取用户ID
        
        # 检查配置是否存在
        if config_id not in rag_configurations:
            return jsonify({'error': f'知识库配置 "{config_id}" 不存在'}), 404
        
        # 获取配置信息
        config = rag_configurations[config_id]
        
        # 权限检查：只读配置不允许上传
        if config_manager.is_readonly_config(config_id):
            return jsonify({'error': f'配置 {config_id} 为只读，不允许上传文档'}), 403
        
        if config_id == 'none':
            return jsonify({'error': '无知识库模式不允许上传文档'}), 403
        
        # 检查文件大小
        from Rag_Framework.utils import check_file_size
        if not check_file_size(file, max_size_mb=50):
            return jsonify({'error': '文件过大，请上传小于50MB的文件'}), 413
            
        # 检查文件类型
        filename = file.filename.lower()
        supported_extensions = ['.pdf']
        file_ext = os.path.splitext(filename)[1].lower()

        if file_ext not in supported_extensions:
            return jsonify({'error': '只支持PDF文件格式'}), 400
        
        # 保存文件到用户特定的知识库目录
        user_knowledge_dir = config_manager.get_user_knowledge_path(user_id, config_id)
        os.makedirs(user_knowledge_dir, exist_ok=True)
        
        # 使用安全的文件名
        from werkzeug.utils import secure_filename
        secure_name = secure_filename(file.filename)
        
        # 如果文件已存在，添加时间戳避免覆盖
        timestamp = int(time.time())
        file_path = os.path.abspath(os.path.join(user_knowledge_dir, secure_name))
        
        # 检查文件是否已存在
        file_exists = os.path.exists(file_path)
        
        # 如果文件已存在且内容相同，则不覆盖
        if file_exists:
            # 计算上传文件的MD5
            file_content = file.read()
            upload_md5 = hashlib.md5(file_content).hexdigest()
            file.seek(0)  # 重置文件指针
            
            # 计算现有文件的MD5
            with open(file_path, 'rb') as existing_file:
                existing_md5 = hashlib.md5(existing_file.read()).hexdigest()
            
            # 如果内容不同，使用时间戳创建新名称
            if upload_md5 != existing_md5:
                name, ext = os.path.splitext(secure_name)
                secure_name = f"{name}_{timestamp}{ext}"
                file_path = os.path.abspath(os.path.join(knowledge_base_dir, secure_name))
        
        # 保存文件
        file.save(file_path)
        logger.info(f"知识库文件保存成功: {file_path}, 配置: {config['name']}")
        
        # 创建稳定的文档ID (使用文件绝对路径生成哈希)
        doc_id_int = int(hashlib.md5(file_path.encode()).hexdigest()[:8], 16)
        
        # 创建任务 ID
        task_id = f"task_{timestamp}_{hashlib.md5(file_path.encode()).hexdigest()[:8]}"
        
        # 检查文件是否已经在当前用户的当前配置中处理过
        rag = get_rag_instance()
        already_processed = False
        if rag and hasattr(rag, 'documents'):
            # 检查当前RAG实例是否使用正确的配置
            current_user_collection = config_manager.get_user_collection_name(user_id, config_id)
            rag_collection = getattr(rag.retriever, 'collection_name', None) if hasattr(rag, 'retriever') else None
            
            # 只有在RAG实例使用相同用户配置时才检查重复
            if rag_collection == current_user_collection and doc_id_int in rag.documents:
                doc_info = rag.documents[doc_id_int]
                if hasattr(doc_info, 'processed') and doc_info.processed:
                    already_processed = True
                    logger.info(f"文件 {file_path} 在用户 {user_id} 的配置 {config_id} 中已处理过，跳过处理")
            else:
                logger.info(f"文件 {file_path} 在用户 {user_id} 的配置 {config_id} 中未处理过，将进行处理")
        
        # 尝试预先获取PDF总页数
        total_pages = 0
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(file_path)
            total_pages = len(reader.pages)
            logger.info(f"上传文件预处理 - PDF总页数: {total_pages}")
        except Exception as e:
            logger.warning(f"上传文件预处理 - 无法获取PDF页数: {str(e)}")
        
        # 创建任务进度记录
        processing_tasks[task_id] = {
            'status': 'processing' if not already_processed else 'completed',
            'progress': 0 if not already_processed else 100,
            'file_name': secure_name,
            'original_name': file.filename,
            'file_path': file_path,
            'config_id': config_id,
            'error': None,
            'start_time': time.time(),
            'total_pages': total_pages,
            'current_step': 'initializing' if not already_processed else 'completed',
            'processed_pages': 0 if not already_processed else total_pages,
            'current_page': 0,
            'doc_id': doc_id_int,
            'already_processed': already_processed,
            'user_id': user_id  # 添加用户ID字段
        }
        
        # 将任务ID添加到用户任务列表
        if user_id not in user_tasks:
            user_tasks[user_id] = []
        user_tasks[user_id].append(task_id)
        
        logger.info(f"任务 {task_id} 已创建并关联到用户 {user_id}")
        
        # 保存用户任务映射
        save_user_tasks()
        
        # 如果文件已处理过，直接返回成功
        if already_processed:
            return jsonify({
                'message': f"文件 '{file.filename}' 上传成功，已处理过，无需重复处理",
                'filename': secure_name,
                'original_name': file.filename,
                'status': 'completed',
                'task_id': task_id,
                'config_id': config_id,
                'total_pages': total_pages,
                'already_processed': True
            }), 200
        
        def update_processing_progress(task_id, progress_info):
            """处理进度更新回调函数"""
            if task_id in processing_tasks:
                # 进度保护 - 确保进度只增不减
                if 'progress' in progress_info and 'progress' in processing_tasks[task_id]:
                    if progress_info['progress'] < processing_tasks[task_id]['progress']:
                        # 如果新进度小于当前进度，保持当前进度
                        progress_info['progress'] = processing_tasks[task_id]['progress']
                
                # 更新处理任务状态
                for key, value in progress_info.items():
                    processing_tasks[task_id][key] = value
                
                # 实时记录进度更新
                logger.info(f"任务 {task_id} 进度更新: {progress_info}")
                
                # 立即保存状态确保实时反馈
                save_task_state(task_id)
        
        def save_task_state(task_id):
            """保存单个任务状态到持久化存储"""
            try:
                if task_id not in processing_tasks:
                    return
                    
                # 确保目录存在
                tasks_dir = os.path.join(TASKS_STORAGE_PATH, "tasks")
                os.makedirs(tasks_dir, exist_ok=True)
                
                # 保存任务状态
                task_file = os.path.join(tasks_dir, f"{task_id}.json")
                with open(task_file, 'w', encoding='utf-8') as f:
                    json.dump(processing_tasks[task_id], f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"保存任务状态失败 {task_id}: {str(e)}")
                    
        # 使用线程处理文档，避免阻塞请求
        def process_document_thread():
            try:
                # 获取 RAG 实例
                rag = get_rag_instance()
                if not rag:
                    processing_tasks[task_id]['status'] = 'failed'
                    processing_tasks[task_id]['error'] = 'RAG 系统未初始化'
                    return
                
                with rag_lock:
                    processing_tasks[task_id]['progress'] = 20
                    processing_tasks[task_id]['current_step'] = 'initializing_directories'
                    
                    # 创建输出目录
                    page_output_dir = os.path.join(config['db_path'], 'pages')
                    os.makedirs(page_output_dir, exist_ok=True)
                    
                    processing_tasks[task_id]['progress'] = 40
                    processing_tasks[task_id]['current_step'] = 'processing_pdf'
                    
                    # 处理PDF，传入稳定的文档ID
                    file_path_abs = os.path.abspath(file_path)
                    result = rag.process_file(
                        file_path=file_path_abs,
                        output_dir=page_output_dir,
                        doc_id=doc_id_int,  # 使用稳定的文档ID
                        task_id=task_id,
                        progress_callback=update_processing_progress,
                        config=config
                    )
                    
                    processing_tasks[task_id]['progress'] = 80
                    processing_tasks[task_id]['current_step'] = 'finalizing'
                    
                    # 更新处理状态
                    logger.info(f"文档处理结果: {result}")
                    
                    if result and (result.get('success') or result.get('already_processed')):
                        processing_tasks[task_id]['progress'] = 100
                        processing_tasks[task_id]['status'] = 'completed'
                        processing_tasks[task_id]['doc_id'] = result.get('doc_id', doc_id_int)
                        processing_tasks[task_id]['processed_pages'] = result.get('processed_pages', 0)
                        processing_tasks[task_id]['total_pages'] = result.get('total_pages', 0)
                        processing_tasks[task_id]['text_content'] = result.get('text_content', '')
                        processing_tasks[task_id]['already_processed'] = result.get('already_processed', False)
                        processing_tasks[task_id]['current_step'] = 'completed'
                        
                        logger.info(f"任务 {task_id} 处理完成: 状态={processing_tasks[task_id]['status']}, 进度={processing_tasks[task_id]['progress']}")

                        # 强制保存任务状态并确保文档信息同步到Milvus
                        save_task_state(task_id)
                    else:
                        processing_tasks[task_id]['status'] = 'failed'
                        processing_tasks[task_id]['progress'] = 0
                        processing_tasks[task_id]['current_step'] = 'failed'
                        
                        if isinstance(result, dict) and 'error' in result:
                            processing_tasks[task_id]['error'] = result['error']
                        else:
                            processing_tasks[task_id]['error'] = '文档处理失败'
                        
                        logger.error(f"任务 {task_id} 处理失败: {processing_tasks[task_id]['error']}")
                        save_task_state(task_id)
            except Exception as e:
                logger.error(f"处理知识库文件时出错: {str(e)}")
                traceback.print_exc()
                
                # 更新任务状态为失败
                if task_id in processing_tasks:
                    processing_tasks[task_id]['status'] = 'failed'
                    processing_tasks[task_id]['error'] = str(e)
        
        # 启动处理线程
        processing_thread = threading.Thread(target=process_document_thread)
        processing_thread.daemon = True
        processing_thread.start()
        
        return jsonify({
            'message': f"文件 '{file.filename}' 上传成功，正在后台处理",
            'filename': secure_name,
            'original_name': file.filename,
            'status': 'processing',
            'task_id': task_id,
            'config_id': config_id,
            'total_pages': total_pages,
            'doc_id': doc_id_int,
            'tracking_id': doc_id_int  # 明确指出用于追踪的ID
        }), 200
        
    except Exception as e:
        logger.error(f'上传知识库文件错误: {str(e)}')
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# 添加正确的全局变量用于缓存上次响应
progress_response_cache = {}
last_response_time = {}

@app.route('/check_processing_progress', methods=['GET'])
def check_processing_progress():
    """检查文档处理进度,带有节流和缓存机制"""
    try:
        # 从请求中获取各种可能的任务ID和用户ID
        task_id = request.args.get('task_id')
        doc_id = request.args.get('doc_id')  # 同时支持doc_id
        tracking_id = request.args.get('tracking_id')  # 支持新的tracking_id字段
        user_id = request.args.get('user_id')
        
        # 确定要使用的ID
        lookup_id = None
        
        # 优先使用doc_id或tracking_id进行查找
        if doc_id:
            try:
                # 尝试将字符串转换为整数（如果可能）
                lookup_id = int(doc_id) if str(doc_id).isdigit() else doc_id
                logger.debug(f"使用doc_id进行查找: {lookup_id}")
            except:
                lookup_id = doc_id
        elif tracking_id:
            try:
                lookup_id = int(tracking_id) if str(tracking_id).isdigit() else tracking_id
                logger.debug(f"使用tracking_id进行查找: {lookup_id}")
            except:
                lookup_id = tracking_id
        elif task_id:
            lookup_id = task_id
            logger.debug(f"使用task_id进行查找: {lookup_id}")
        
        if not lookup_id:
            return jsonify({
                'status': 'error',
                'error': '未提供有效的任务ID',
                'progress': 0
            }), 400
            
        # 如果提供了用户ID,验证任务是否属于该用户
        if user_id and task_id in task_users and task_users[task_id] != user_id:
            return jsonify({
                'task_id': task_id,
                'status': 'unauthorized',
                'error': '没有权限访问此任务',
                'progress': 0
            }), 403
            
        # 获取当前时间
        current_time = time.time()
        
        # 检查是否应该使用缓存响应
        if task_id in last_response_time and task_id in progress_response_cache:
            last_time = last_response_time[task_id]
            cached_response = progress_response_cache[task_id]
            
            # 查看任务状态
            task_status = None
            if task_id in processing_tasks:
                task_status = processing_tasks[task_id].get('status')
                
            # 对于仍在处理中的任务和大型文档,使用缓存来减少服务器负担
            time_diff = current_time - last_time
            if (task_status not in ['completed', 'failed'] and 
                time_diff < 3.0 and 
                "total_pages" in cached_response and 
                cached_response.get("total_pages", 0) > 100):
                
                # 每3秒只真正处理一次请求
                logger.debug(f"使用缓存的处理进度响应,任务ID: {task_id}, 上次更新: {time_diff:.1f}秒前")
                return jsonify(cached_response), 200
        
        # 查找匹配的任务
        found_task_id = None
        # 1. 检查是否直接匹配
        if lookup_id in processing_tasks:
            found_task_id = lookup_id
            logger.debug(f"直接匹配到任务ID: {found_task_id}")
        else:
            # 2. 尝试通过doc_id查找
            # 查找doc_id字段等于lookup_id的任务
            for tid, task in processing_tasks.items():
                if task.get('doc_id') == lookup_id:
                    found_task_id = tid
                    logger.debug(f"通过doc_id匹配到任务: {found_task_id}")
                    break
            # 3. 如果还是找不到，尝试更宽松的匹配
            if not found_task_id and isinstance(lookup_id, str) and lookup_id.isdigit():
                # 尝试将字符串转换为整数再查找
                numeric_id = int(lookup_id)
                for tid, task in processing_tasks.items():
                    if task.get('doc_id') == numeric_id:
                        found_task_id = tid
                        logger.debug(f"通过转换后的doc_id匹配到任务: {found_task_id}")
                        break
        # 检查本地任务记录
        if found_task_id:
            task_info = processing_tasks[found_task_id].copy()
            # 确保返回的任务包含doc_id
            if 'doc_id' not in task_info and 'doc_id' in processing_tasks[found_task_id]:
                task_info['doc_id'] = processing_tasks[found_task_id]['doc_id']
                
            # 如果文档ID存在，尝试获取更多信息（无论文档状态如何）
            if 'doc_id' in task_info:
                doc_id = task_info['doc_id']
                
                # 尝试从RAG实例获取文档信息
                rag = get_rag_instance()
                if rag and hasattr(rag, 'documents') and doc_id in rag.documents:
                    doc_info = rag.documents[doc_id]
                    
                    # 更新页数信息
                    if hasattr(doc_info, 'page_count'):
                        task_info['total_pages'] = doc_info.page_count
                        
                    # 如果文档对象有处理计数属性
                    if hasattr(doc_info, 'processed_count'):
                        task_info['processed_pages'] = doc_info.processed_count
                        
                    # 包含PDF提取的文本内容(可选)
                    if hasattr(doc_info, 'text_content'):
                        # 获取前300个字符的文本预览
                        text_preview = doc_info.text_content[:300] + "..." if len(doc_info.text_content) > 300 else doc_info.text_content
                        task_info['text_preview'] = text_preview
            
            # 添加：如果任务已完成，确保Milvus数据已同步
            if task_info['status'] == 'completed':
                # 记录任务完成标符
                task_info['completion_id'] = f"{doc_id}_{int(time.time())}"
                # 将此标识符保存回原始任务,以确保后续响应保持一致
                processing_tasks[found_task_id]['completion_id'] = task_info['completion_id']

                # 尝试从RAG实例获取文档信息并确认同步
                rag = get_rag_instance()
                if rag and hasattr(rag, 'retriever') and hasattr(rag.retriever, 'client'):
                    doc_id = task_info.get('doc_id')
                    if doc_id:
                        try:
                            # 确保集合已加载
                            rag.retriever.client.load_collection(rag.retriever.collection_name)
                            
                            # 刷新集合以确保所有写入已完成
                            rag.retriever.client.flush(rag.retriever.collection_name)
                            
                            # 查询文档记录以验证同步状态
                            min_page_doc_id = doc_id * 1000
                            max_page_doc_id = (doc_id + 1) * 1000
                            query_expr = f"doc_id >= {min_page_doc_id} AND doc_id < {max_page_doc_id}"
                            
                            results = rag.retriever.client.query(
                                collection_name=rag.retriever.collection_name,
                                filter=query_expr,
                                output_fields=["doc_id"],
                                limit=1
                            )
                            
                            # 更新任务信息中的同步状态
                            task_info['milvus_synced'] = len(results) > 0
                            logger.info(f"任务完成状态检查 - Milvus记录状态: {'已同步' if task_info['milvus_synced'] else '未同步'}")
                            
                            # 如果未同步，尝试重新同步
                            if not task_info['milvus_synced'] and hasattr(rag, '_sync_documents_standalone'):
                                logger.info(f"检测到Milvus数据未同步，尝试强制同步")
                                sync_retry = rag._sync_documents_standalone(rag.retriever.collection_name)
                                logger.info(f"强制同步结果: {'成功' if sync_retry else '失败'}")
                                
                                # 重新检查同步状态
                                results = rag.retriever.client.query(
                                    collection_name=rag.retriever.collection_name,
                                    filter=query_expr,
                                    output_fields=["doc_id"],
                                    limit=1
                                )
                                task_info['milvus_synced'] = len(results) > 0
                                task_info['sync_retry_result'] = sync_retry
                        except Exception as sync_err:
                            logger.error(f"检查Milvus同步状态时出错: {str(sync_err)}")
                            task_info['milvus_synced'] = False
                            task_info['sync_error'] = str(sync_err)
                    
            # 确保progress字段存在
            if 'progress' not in task_info:
                task_info['progress'] = 100 if task_info['status'] == 'completed' else 0
            
            # 更新缓存和时间戳
            progress_response_cache[task_id] = task_info
            last_response_time[task_id] = current_time
            
            # 如果任务已完成,确保Milvus数据已同步
            if task_info['status'] == 'completed':
                # 记录任务完成标符
                task_info['completion_id'] = f"{doc_id}_{int(time.time())}"
                # 将此标识符保存回原始任务,以确保后续响应保持一致
                processing_tasks[found_task_id]['completion_id'] = task_info['completion_id']

            # 如果任务已完成或失败,清理缓存
            if task_info['status'] in ['completed', 'failed']:
                if task_id in progress_response_cache:
                    del progress_response_cache[task_id]
                if task_id in last_response_time:
                    del last_response_time[task_id]
            
            return jsonify(task_info), 200
        
        # 如果找不到任务记录,返回不存在状态而不是错误
        return jsonify({
            'task_id': lookup_id,
            'doc_id': lookup_id if doc_id else None,
            'status': 'not_found',
            'error': '找不到指定的任务',
            'progress': 0
        }), 200  # 返回200而不是404,这样前端可以继续处理
        
    except Exception as e:
        logger.error(f'检查处理进度错误: {e}')
        traceback.print_exc()
        return jsonify({
            'error': str(e),
            'status': 'error',
            'progress': 0
        }), 500

@app.route('/delete_rag_document', methods=['POST'])
def delete_rag_document():
    """删除 RAG 知识库文档"""
    try:
        data = request.json
        doc_id = data.get('doc_id')
        config_id = data.get('config_id', 'default')
        user_id = data.get('user_id', 'anonymous')  # 获取用户ID
        physical_delete = data.get('physical_delete', True)
        
        if not doc_id:
            return jsonify({'error': '未提供文档 ID'}), 400
        
        # 权限检查
        if config_manager.is_readonly_config(config_id):
            return jsonify({'error': f'配置 {config_id} 为只读，不允许删除文档'}), 403
        
        # 获取 RAG 实例
        rag = get_rag_instance()
        if not rag:
            return jsonify({'error': 'RAG 系统未初始化'}), 500
        
        # 获取配置信息
        if config_id not in rag_configurations:
            return jsonify({'error': '知识库配置不存在'}), 404
            
        config = rag_configurations[config_id]
        
        # 使用用户特定的知识库路径
        user_knowledge_dir = config_manager.get_user_knowledge_path(user_id, config_id)
        
        # 查找文件路径
        file_path = None
        supported_extensions = ['.pdf']
        if os.path.exists(user_knowledge_dir):
            for file in os.listdir(user_knowledge_dir):
                file_ext = os.path.splitext(file)[1].lower()
                if file_ext in supported_extensions:
                    current_path = os.path.join(user_knowledge_dir, file)
                    current_id = hashlib.md5(current_path.encode()).hexdigest()
                    if current_id == doc_id:
                        file_path = current_path
                        break
        
        if not file_path:
            return jsonify({'error': '无法确定文件路径'}), 404
        
        logger.info(f"准备删除文档: {doc_id}, 文件路径: {file_path}, 用户: {user_id}")
        
        # 生成用户特定的集合名称
        user_collection_name = config_manager.get_user_collection_name(user_id, config_id)
        
        # 执行从Milvus中删除记录的操作
        milvus_success = False
        deleted_records = 0
        
        try:
            # 生成正确的文档ID - 与存储时保持一致
            abs_path = os.path.abspath(file_path)
            doc_id_int = int(hashlib.md5(abs_path.encode()).hexdigest()[:8], 16)
            
            logger.info(f"计算出的文档ID: {doc_id_int} (0x{doc_id_int:x}), 集合: {user_collection_name}")
            
            # 删除文档相关的所有记录
            min_page_doc_id = doc_id_int * 1000
            max_page_doc_id = (doc_id_int + 1) * 1000
            delete_filter = f"doc_id >= {min_page_doc_id} AND doc_id < {max_page_doc_id}"
            
            logger.info(f"执行Milvus删除，过滤条件: {delete_filter}")
            result = rag.retriever.client.delete(
                collection_name=rag.retriever.collection_name,
                filter=delete_filter
            )
            
            # 刷新集合确保删除生效
            rag.retriever.client.flush(rag.retriever.collection_name)
            logger.info("Milvus删除操作完成")
            
            # 从内存中删除文档信息
            if hasattr(rag, 'documents'):
                for key in [doc_id_int, int(doc_id_int/1000)]:
                    if key in rag.documents:
                        del rag.documents[key]
            
            milvus_success = True
            
        except Exception as e:
            logger.error(f"从Milvus删除记录时出错: {str(e)}")
            traceback.print_exc()
        
        # 物理删除文件
        physical_success = False
        pages_deleted = False
        if physical_delete and os.path.exists(file_path):
            try:
                os.remove(file_path)
                physical_success = True
                logger.info(f"物理删除文件成功: {file_path}")
                
                # 删除对应的pages目录
                try:
                    pages_base_dir = os.path.join(config.get('db_path'), 'pages')
                    
                    # 先尝试直接匹配
                    pages_dir = os.path.join(pages_base_dir, str(doc_id_int))
                    
                    if os.path.exists(pages_dir):
                        import shutil
                        shutil.rmtree(pages_dir)
                        pages_deleted = True
                        logger.info(f"成功删除pages目录: {pages_dir}")
                    else:
                        # 如果直接匹配失败，从Milvus查询实际的doc_id
                        try:
                            # 查询这个文件在Milvus中的实际记录
                            filter_expr = f"doc_id >= {doc_id_int * 1000} AND doc_id < {(doc_id_int + 1) * 1000}"
                            milvus_docs = rag.retriever.client.query(
                                collection_name=rag.retriever.collection_name,
                                filter=filter_expr,
                                output_fields=["doc_id"],
                                limit=1
                            )
                            
                            if milvus_docs:
                                actual_doc_id = milvus_docs[0]['doc_id']
                                actual_base_doc_id = actual_doc_id // 1000
                                actual_pages_dir = os.path.join(pages_base_dir, str(actual_base_doc_id))
                                
                                if os.path.exists(actual_pages_dir):
                                    import shutil
                                    shutil.rmtree(actual_pages_dir)
                                    pages_deleted = True
                                    logger.info(f"通过Milvus查询找到并删除pages目录: {actual_pages_dir}")
                                else:
                                    logger.warning(f"通过Milvus查询的pages目录也不存在: {actual_pages_dir}")
                            else:
                                logger.warning(f"在Milvus中未找到文档记录，无法确定pages目录")
                                
                        except Exception as milvus_query_err:
                            logger.warning(f"Milvus查询失败，尝试遍历pages目录: {str(milvus_query_err)}")
                            
                except Exception as pages_err:
                    logger.error(f"删除pages目录过程出错: {str(pages_err)}")
                    
            except Exception as e:
                logger.error(f"物理删除文件失败: {file_path}, 错误: {str(e)}")
        
        # 返回结果
        return jsonify({
            'message': f"文档 '{os.path.basename(file_path)}' 已从知识库中删除",
            'deleted': True,
            'doc_id': doc_id,
            'file_path': file_path,
            'status': 'deleted',
            'filename': os.path.basename(file_path),
            'processed': False,
            'milvus_success': milvus_success,
            'physical_success': physical_success,
            'pages_deleted': pages_deleted
        }), 200
        
    except Exception as e:
        logger.error(f'删除知识库文档错误: {str(e)}')
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/stats', methods=['GET'])
def get_instance_stats():
    """获取实例统计信息，供负载均衡器使用"""
    try:
        # 统计当前活跃任务数
        active_tasks = len([task for task in task_manager._tasks.values() 
                           if task.status in ['pending', 'processing']])
        
        # 统计总处理任务数
        total_processed = len([task for task in task_manager._tasks.values() 
                             if task.status == 'completed'])
        
        # 获取RAG实例状态
        rag_status = "loaded" if rag_instance else "not_loaded"
        
        # 计算内存使用情况
        try:
            import psutil
            process = psutil.Process()
            memory_info = process.memory_info()
            memory_usage_mb = round(memory_info.rss / 1024 / 1024, 2)
        except:
            memory_usage_mb = 0
        
        stats = {
            'timestamp': time.time(),
            'instance_id': INSTANCE_ID,
            'gpu_id': GPU_ID,
            'port': RAG_MANAGER_PORT,
            'active_tasks': active_tasks,
            'total_processed': total_processed,
            'rag_status': rag_status,
            'memory_usage_mb': memory_usage_mb,
            'health': 'healthy'
        }
        
        return jsonify(stats)
        
    except Exception as e:
        logger.error(f"获取实例统计信息失败: {str(e)}")
        return jsonify({
            'error': str(e),
            'timestamp': time.time(),
            'instance_id': INSTANCE_ID,
            'health': 'degraded'
        }), 500

@app.route('/health', methods=['GET'])
def health_check():
    """健康检查接口"""
    # 检查配置状态
    config_status = "OK" if rag_configurations else "Not Loaded"
    
    # 检查与TCAD服务的连接 (异步，不阻塞)
    tcad_status = "Unknown"
    try:
        import requests
        response = requests.get(f"http://10.98.64.22:{TCAD_PORT}/health", timeout=2)  # 减少超时时间
        if response.status_code == 200:
            tcad_status = "Connected"
        else:
            tcad_status = f"Error: {response.status_code}"
    except Exception as e:
        tcad_status = f"Skipped: {str(e)[:50]}"  # 不作为错误，只是跳过
    
    # 检查ColPali模型状态
    colpali_status = "Loaded" if rag_instance and rag_instance.model else "Not Loaded"
    
    # 检查PDF文本提取状态
    text_extract_status = "PDFMiner Ready"

    # 获取文本提取统计
    text_extract_count = 0
    if rag_instance and hasattr(rag_instance, 'retriever'):
        if hasattr(rag_instance.retriever, 'pdf_text_extracted_count'):
            text_extract_count = rag_instance.retriever.pdf_text_extracted_count
    
    # 返回状态信息
    return jsonify({
        "status": "healthy",
        "config_status": config_status,
        "tcad_connection": tcad_status,
        "colpali_status": colpali_status,
        "text_extract_status": text_extract_status,
        "text_extract_count": text_extract_count,
        "version": "1.0.0",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    }), 200

@app.route('/get_relevant_context', methods=['POST'])
def get_relevant_context():
    """为TCAD服务提供RAG查询功能,支持延迟PDF处理,自定义top_k"""
    try:
        data = request.json
        query = data.get('query')
        # max_tokens = data.get('max_tokens', 8196)
        config_id = data.get('config_id', 'default')
        service = data.get('service', 'unknown')  # 记录请求来源服务
        top_k = data.get('top_k', 5)  # 从请求中获取top_k值,默认为5
        
        # 确保top_k在合理范围内
        try:
            top_k = int(top_k)
            if top_k < 3:
                top_k = 3
            elif top_k > 10:
                top_k = 10
        except (ValueError, TypeError):
            top_k = 5  # 如果转换失败，使用默认值
        
        # 创建搜索ID，用于日志记录
        search_id = f"{int(time.time())}_{hashlib.md5(query.encode()).hexdigest()[:8]}"
        
        # 添加更多日志
        logger.info(f"收到RAG查询请求: 查询='{query[:50]}...'，配置ID={config_id}，top_k={top_k}，来源服务={service}，搜索ID={search_id}")
        
        if not query:
            return jsonify({'error': '查询内容不能为空'}), 400
            
        # 验证配置ID是否存在
        if config_id not in rag_configurations:
            logger.warning(f"配置ID不存在: {config_id}，将使用默认配置")
            config_id = 'default'
        
        # 获取知识库名称
        knowledge_base_name = rag_configurations[config_id]['name']
        
        # 使用ColPali模型进行查询
        rag = get_rag_instance()
        if not rag:
            logger.error("获取RAG实例失败")
            return jsonify({'error': 'RAG系统未初始化'}), 500
        
        # 检查RAG实例当前使用的配置是否正确
        # 从请求中获取用户ID或使用anonymous
        request_user_id = data.get('user_id', 'anonymous')
        expected_collection = config_manager.get_user_collection_name(request_user_id, config_id)
        current_collection = getattr(rag.retriever, 'collection_name', None) if hasattr(rag, 'retriever') else None
        
        if current_collection != expected_collection:
            logger.warning(f"RAG实例集合不匹配 (当前: {current_collection}, 期望: {expected_collection} for user: {request_user_id})，正在自动切换...")
            
            # 自动切换到正确的知识库配置
            try:
                switch_success = switch_knowledge_base(config_id, request_user_id)
                if switch_success:
                    logger.info(f"✅ 已自动切换到正确的知识库配置: {config_id}")
                    # 重新获取RAG实例以确保使用新配置
                    rag = rag_instance
                else:
                    logger.error(f"❌ 自动切换知识库失败，将使用当前配置继续查询")
            except Exception as e:
                logger.error(f"❌ 自动切换知识库时出错: {str(e)}，将使用当前配置继续查询")
        
        # 执行搜索，传入搜索ID用于日志记录以及自定义的top_k值
        try:
            results = rag.search(query, top_k=top_k, search_id=search_id)
            
            if not results:
                logger.warning(f"未在知识库中找到相关上下文")
                return jsonify({
                    "context": "在知识库中未找到与查询相关的内容。",
                    "knowledge_base_name": knowledge_base_name,
                    "config_id": config_id,
                    "search_id": search_id
                }), 200
                
            # 构建上下文文本
            context_parts = []
            
            for i, result in enumerate(results):
                score = result['score']
                file_path = result['file_path']
                text_content = result.get('text_content', '')
                page_num = result['page_num']
                image_path = result.get('image_path', '')
                
                # 添加文档来源信息
                # file_name = os.path.basename(file_path)
                # source_info = f"[来源文档 {i+1}] {file_name}, 页码: {page_num}, 相关度: {score:.4f}"
                
                # 添加文本内容（如果有）
                if text_content:
                    context_parts.append(
                        # f"{source_info}\n\n"
                        f"页面内容:\n{text_content}\n"
                    )
            
            # 组合最终上下文
            context = "以下是与查询相关的内容:\n\n" + "\n".join(context_parts)
            
            # 记录搜索结果统计
            logger.info(f"搜索成功，ID={search_id}，找到 {len(results)} 个结果")
            
            return jsonify({
                "context": context,
                "knowledge_base_name": knowledge_base_name,
                "config_id": config_id,
                "results": results,  # 包含原始搜索结果
                "search_id": search_id  # 返回搜索ID以便后续跟踪
            }), 200
            
        except Exception as search_err:
            logger.error(f"搜索时出错: {str(search_err)}")
            traceback.print_exc()
            return jsonify({
                "context": "在知识库中查询时出错。",
                "knowledge_base_name": knowledge_base_name,
                "config_id": config_id,
                "error": str(search_err),
                "search_id": search_id
            }), 200
    
    except Exception as e:
        logger.error(f'处理RAG查询错误: {str(e)}')
        traceback.print_exc()
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/rag_query', methods=['POST'])
def rag_query():
    """提供RAG查询服务，TCAD服务可以调用此接口"""
    # 调用相同的内部方法处理请求
    return get_relevant_context()

@app.route('/chatbot_rag_query', methods=['POST'])
def chatbot_rag_query_endpoint():
    """为智能助手提供RAG查询服务,返回格式与get_relevant_context统一"""
    try:
        data = request.json
        query = data.get('message')  # 使用'message'字段
        if not query and 'messages' in data:  # 兼容'messages'字段
            query = data.get('messages')
        user_id = data.get('user_id', 'unknown')
        conversation_id = data.get('conversation_id', 'default')
        config_id = data.get('config_id', 'default')
        
        # 记录请求信息
        logger.info(f"收到Chatbot RAG查询请求: 查询='{query[:50] if query else 'None'}...',用户ID: {user_id}, 对话ID: {conversation_id}, 知识库ID: {config_id}")
        
        # 检查查询是否为空
        if not query:
            return jsonify({'error': '查询内容不能为空'}), 400
            
        # 创建搜索ID,用于日志记录
        search_id = f"{int(time.time())}_{hashlib.md5(query.encode()).hexdigest()[:8]}"
        
        # 验证配置ID是否存在
        if config_id not in rag_configurations:
            logger.warning(f"配置ID不存在: {config_id},将使用默认配置")
            config_id = 'default'
            
        # 获取知识库名称
        knowledge_base_name = rag_configurations[config_id]['name']
        
        # 使用ColPali模型进行查询
        rag = get_rag_instance()
        if not rag:
            logger.error("获取RAG实例失败")
            return jsonify({'error': 'RAG系统未初始化'}), 500
            
        # 执行搜索,传入搜索ID用于日志记录
        results = rag.search(query, top_k=top_k, search_id=search_id)
        
        if not results:
            logger.warning(f"未在知识库中找到相关上下文")
            return jsonify({
                "context": "在知识库中未找到与查询相关的内容。",
                "knowledge_base_name": knowledge_base_name,
                "config_id": config_id,
                "search_id": search_id
            }), 200
            
        # 构建上下文文本
        context_parts = []
        
        # 处理所有结果并执行PDF提取
        for i, result in enumerate(results):
            # 获取文本内容
            text_content = result.get('text_content', '')
            
            # 如果没有文本内容但有图片路径,尝试执行PDF文本提取
            if not text_content and 'image_path' in result and result['image_path'] and os.path.exists(result['image_path']):
                try:
                    logger.info(f"对页面执行PDF文本提取 (chatbot_rag_query_endpoint): {result['image_path']}")
                    # 使用rag实例执行PDF文本提取
                    extract_response = rag.extract_text_from_pdf_by_image_path(result['image_path'])
                    if extract_response and extract_response.get("success"):
                        text_content = extract_response["text"]
                        # 更新结果中的文本内容
                        result['text_content'] = text_content
                        logger.info(f"PDF文本提取成功: 提取了 {len(text_content)} 字符")
                    else:
                        error_msg = extract_response.get("error", "未知错误") if extract_response else "提取失败"
                        logger.warning(f"PDF文本提取未成功: {error_msg}")
                except Exception as extract_err:
                    logger.error(f"PDF文本提取过程中出错: {str(extract_err)}")
                    traceback.print_exc()
            
            # 只添加有效的文本内容
            if text_content and text_content.strip():
                # 裁剪文本内容,避免过长
                max_text_length = 800
                if len(text_content) > max_text_length:
                    text_content = text_content[:max_text_length] + "..."
                
                # 只添加核心内容,不加元数据,与get_relevant_context一致
                context_parts.append(f"检索信息 {i+1}：{text_content.strip()}")
        
        # 组合最终上下文 
        if context_parts:
            context = "以下是查询相关的信息:\n\n" + "\n\n".join(context_parts)
        else:
            context = "在知识库中未找到与查询相关的内容。"
        
        # 记录搜索结果统计
        logger.info(f"Chatbot搜索成功,ID={search_id},找到 {len(results)} 个结果")
        
        return jsonify({
            "context": context,
            "knowledge_base_name": knowledge_base_name,
            "config_id": config_id,
            "results": results,
            "search_id": search_id
        }), 200
        
    except Exception as e:
        logger.error(f'处理Chatbot RAG查询错误: {str(e)}')
        traceback.print_exc()
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/clear_milvus_collection', methods=['POST'])
def clear_milvus_collection():
    """清空指定知识库配置的Milvus集合"""
    try:
        data = request.json
        config_id = data.get('config_id', 'default')
        
        # 获取 RAG 实例
        rag = get_rag_instance()
        if not rag:
            return jsonify({'error': 'RAG 系统未初始化'}), 500
        
        # 检查是否是当前集合
        if rag.retriever.collection_name != config_id:
            # 尝试切换到指定数据库
            db_name = f"rag_{config_id}"
            try:
                if hasattr(rag.retriever.client, 'use_database'):
                    rag.retriever.client.use_database(db_name)
                    logger.info(f"已切换到数据库: {db_name}")
            except Exception as db_switch_err:
                logger.error(f"切换数据库失败: {str(db_switch_err)}")
                return jsonify({'error': f'无法切换到数据库 {db_name}'}), 500
            
            # 尝试切换集合
            try:
                rag.retriever.collection_name = config_id
                rag.retriever.client.load_collection(config_id)
                logger.info(f"已切换到集合: {config_id}")
            except Exception as coll_switch_err:
                logger.error(f"切换集合失败: {str(coll_switch_err)}")
                return jsonify({'error': f'无法切换到集合 {config_id}'}), 500
        
        # 确保集合已加载
        try:
            rag.retriever.client.load_collection(config_id)
        except Exception as load_err:
            logger.warning(f"加载集合失败: {str(load_err)}")
        
        # 清空集合数据
        try:
            # 删除所有记录
            result = rag.retriever.client.delete(
                collection_name=config_id,
                filter=""  # 空过滤器删除所有记录
            )
            
            logger.info(f"已清空集合 {config_id}, 结果: {result}")
            
            # 强制刷新集合,确保删除生效
            try:
                rag.retriever.client.flush([config_id])
                logger.info(f"已刷新集合 {config_id}")
            except Exception as flush_err:
                logger.warning(f"刷新集合失败: {str(flush_err)}")
            
            # 执行压缩操作,回收空间
            try:
                compact_result = rag.retriever.client.compact(config_id)
                logger.info(f"压缩结果: {compact_result}")
            except Exception as compact_err:
                logger.warning(f"压缩集合失败: {str(compact_err)}")
            
            # 同步清理文档集合
            rag.documents = {}
            
            # 检查操作后的集合状态
            try:
                stats_after = rag.retriever.client.get_collection_stats(config_id)
                row_count_after = stats_after.get("row_count", 0)
                logger.info(f"清空后集合记录数: {row_count_after}")
            except Exception as stats_err:
                logger.warning(f"获取集合统计信息失败: {str(stats_err)}")
            
            return jsonify({
                'message': f'已清空集合 {config_id}',
                'success': True
            }), 200
        except Exception as e:
            logger.error(f"清空集合时出错: {str(e)}")
            traceback.print_exc()
            return jsonify({
                'error': f'清空集合时出错: {str(e)}',
                'success': False
            }), 500
            
    except Exception as e:
        logger.error(f'清空Milvus集合错误: {str(e)}')
        traceback.print_exc()
        return jsonify({'error': '服务器内部错误'}), 500

# Milvus Standalone知识库检查
@app.route('/inspect_milvus_data', methods=['GET'])
def inspect_milvus_data():
    """检查Milvus数据库中的数据"""
    try:
        config_id = request.args.get('config_id', 'default')
        limit = int(request.args.get('limit', '20'))
        
        # 获取RAG实例
        rag = get_rag_instance()
        if not rag or not hasattr(rag, 'retriever') or not hasattr(rag.retriever, 'client'):
            return jsonify({'error': 'RAG系统未初始化'}), 500
            
        # 获取当前集合名称
        collection_name = rag.retriever.collection_name
        
        # 查询数据样本
        try:
            # 获取集合统计信息
            stats = rag.retriever.client.get_collection_stats(collection_name)
            row_count = stats.get("row_count", 0)
            
            # 获取seq_id为0的记录样本(文档元数据)
            metadata_samples = rag.retriever.client.query(
                collection_name=collection_name,
                filter="seq_id == 0",
                output_fields=["doc_id", "doc", "text_content", "page_num", "image_path"],
                limit=limit
            )
            
            # 获取部分向量记录
            vector_samples = rag.retriever.client.query(
                collection_name=collection_name,
                filter="",
                output_fields=["doc_id", "seq_id"],
                limit=10
            )
            
            return jsonify({
                'collection_name': collection_name,
                'total_rows': row_count,
                'metadata_samples': metadata_samples,
                'vector_samples': vector_samples,
                'database_name': f"rag_{config_id}"
            }), 200
            
        except Exception as e:
            logger.error(f"检查Milvus数据出错: {str(e)}")
            return jsonify({'error': str(e)}), 500
            
    except Exception as e:
        logger.error(f'检查Milvus数据错误: {str(e)}')
        traceback.print_exc()
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/get_user_tasks', methods=['GET'])
def get_user_tasks():
    """获取特定用户的所有任务"""
    try:
        user_id = request.args.get('user_id')
        
        if not user_id:
            return jsonify({'error': '未提供用户ID'}), 400
            
        # 获取用户任务ID列表
        user_task_ids = user_tasks.get(user_id, [])
        
        # 过滤出有效的任务
        valid_tasks = []
        for task_id in user_task_ids:
            if task_id in processing_tasks:
                task_info = processing_tasks[task_id].copy()
                
                # 如果任务已经标记为需要删除且已经超过保留时间
                if 'keep_until' in task_info and time.time() > task_info['keep_until']:
                    # 使用新任务管理器删除过期任务
                    task_manager.delete_task(task_id)
                    continue
                    
                # 确保task_info中包含doc_id字段，这是前端跟踪进度的关键
                if 'doc_id' not in task_info and 'doc_id' in processing_tasks[task_id]:
                    task_info['doc_id'] = processing_tasks[task_id]['doc_id']
                # 确保ID字段保持一致    
                if 'task_id' not in task_info:
                    task_info['task_id'] = task_id
                valid_tasks.append(task_info)
        
        # 返回用户任务列表，按开始时间排序
        valid_tasks.sort(key=lambda x: x.get('start_time', 0), reverse=True)
        
        return jsonify({
            'user_id': user_id,
            'tasks': valid_tasks,
            'count': len(valid_tasks)
        }), 200
        
    except Exception as e:
        logger.error(f'获取用户任务错误: {str(e)}')
        traceback.print_exc()
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/get_processing_tasks', methods=['GET'])
def get_processing_tasks():
    """获取所有处理中的任务"""
    try:
        user_id = request.args.get('user_id', 'anonymous')
        
        # 查找用户关联的所有处理中任务
        user_processing_tasks = []
        
        # 如果指定了用户ID，只获取该用户的任务
        if user_id:
            if user_id in user_tasks:
                task_ids = user_tasks[user_id]
                for task_id in task_ids:
                    if task_id in processing_tasks and processing_tasks[task_id].get('status') == 'processing':
                        user_processing_tasks.append(processing_tasks[task_id])
        else:
            # 获取所有处理中的任务
            for task_id, task in processing_tasks.items():
                if task.get('status') == 'processing':
                    user_processing_tasks.append(task)
        
        return jsonify({
            'tasks': user_processing_tasks,
            'count': len(user_processing_tasks)
        }), 200
    except Exception as e:
        logger.error(f'获取处理中任务错误: {str(e)}')
        traceback.print_exc()
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/get_task_by_filename', methods=['GET'])
def get_task_by_filename():
    """通过文件名查询任务状态"""
    try:
        filename = request.args.get('filename')
        user_id = request.args.get('user_id', 'anonymous')
        
        if not filename:
            return jsonify({'error': '未提供文件名'}), 400
        
        # 查找匹配的任务
        matching_tasks = []
        
        # 如果提供了用户ID，只在该用户的任务中查找
        if user_id and user_id in user_tasks:
            task_ids = user_tasks[user_id]
            for task_id in task_ids:
                if task_id in processing_tasks:
                    task = processing_tasks[task_id]
                    if (task.get('file_name') == filename or 
                        task.get('original_name') == filename):
                        matching_tasks.append(task)
        else:
            # 在所有任务中查找
            for task_id, task in processing_tasks.items():
                if (task.get('file_name') == filename or 
                    task.get('original_name') == filename):
                    matching_tasks.append(task)
        
        # 如果找到匹配任务，返回最新的一个
        if matching_tasks:
            # 按开始时间排序，返回最新的
            matching_tasks.sort(key=lambda x: x.get('start_time', 0), reverse=True)
            return jsonify(matching_tasks[0]), 200
        else:
            return jsonify({'error': '未找到匹配的任务'}), 404
            
    except Exception as e:
        logger.error(f'通过文件名查询任务错误: {str(e)}')
        traceback.print_exc()
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/save_user_session_state', methods=['POST'])
def save_user_session_state():
    """保存用户会话状态,包括文档列表和统计数据"""
    try:
        data = request.json
        user_id = data.get('user_id', 'anonymous')
        config_id = data.get('config_id', 'default')
        documents = data.get('documents', [])
        stats = data.get('stats', {})
        
        # 保存会话状态
        session_key = f"{user_id}_{config_id}"
        user_session_states[session_key] = {
            'documents': documents,
            'stats': stats,
            'timestamp': time.time()
        }
        
        logger.info(f"已保存用户{user_id}的会话状态,包含{len(documents)}个文档")
        
        return jsonify({
            'success': True,
            'message': '会话状态已保存'
        }), 200
        
    except Exception as e:
        logger.error(f'保存会话状态错误: {str(e)}')
        return jsonify({'error': str(e)}), 500

@app.route('/get_user_session_state', methods=['GET'])
def get_user_session_state():
    """获取用户会话状态"""
    try:
        user_id = request.args.get('user_id', 'anonymous')
        config_id = request.args.get('config_id', 'default')
        
        session_key = f"{user_id}_{config_id}"
        session_data = user_session_states.get(session_key, {})
        
        # 检查是否过期(30分钟)
        if session_data and time.time() - session_data.get('timestamp', 0) > 1800:
            del user_session_states[session_key]
            return jsonify({
                'success': False,
                'message': '会话状态已过期',
                'has_state': False
            }), 200
        
        return jsonify({
            'success': True,
            'has_state': bool(session_data),
            'documents': session_data.get('documents', []),
            'stats': session_data.get('stats', {}),
            'timestamp': session_data.get('timestamp')
        }), 200
        
    except Exception as e:
        logger.error(f'获取会话状态错误: {str(e)}')
        return jsonify({'error': str(e), 'has_state': False}), 500

@app.route('/clear_user_session_state', methods=['POST'])
def clear_user_session_state():
    """清除用户会话状态"""
    try:
        user_id = request.args.get('user_id', 'anonymous')
        config_id = request.args.get('config_id', 'default')
        
        session_key = f"{user_id}_{config_id}"
        if session_key in user_session_states:
            del user_session_states[session_key]
            logger.info(f"已清除用户{user_id}的会话状态")
        
        return jsonify({
            'success': True,
            'message': '会话状态已清除'
        }), 200
        
    except Exception as e:
        logger.error(f'清除会话状态错误: {str(e)}')
        return jsonify({'error': str(e)}), 500

# 资源清理函数
def cleanup_resources():
    """在应用关闭时清理资源，并清除所有用户任务"""
    global rag_instance, processing_tasks, user_tasks, task_users
    
    logger.info("正在清理ColPali RAG Manager资源...")
    
    # 清理Milvus中的无效数据
    if rag_instance is not None and hasattr(rag_instance, 'retriever') and hasattr(rag_instance.retriever, 'client'):
        try:
            logger.info("开始清理Milvus中的无效数据...")
            
            # 清理所有配置的无效数据
            client = rag_instance.retriever.client
            
            for cfg_id, cfg in rag_configurations.items():
                collection_name = cfg_id
                config_folder = cfg.get('folder')
                
                if not config_folder:
                    continue
                    
                logger.info(f"清理配置 {cfg_id} 的无效数据...")
                
                # 首先检查集合是否存在
                try:
                    # 检查集合是否存在
                    if not client.has_collection(collection_name):
                        logger.info(f"集合 {collection_name} 不存在，跳过清理")
                        continue
                        
                    # 获取集合统计信息
                    stats = client.get_collection_stats(collection_name)
                    row_count = stats.get("row_count", 0)
                    logger.info(f"集合 {collection_name} 中有 {row_count} 行数据")
                    
                    # 只有在有数据的情况下才执行清理
                    if row_count > 0:
                        # 查询所有seq_id=0的记录（文档元数据）
                        doc_records = client.query(
                            collection_name=collection_name,
                            filter="seq_id == 0",
                            output_fields=["doc_id", "doc"],
                            limit=5000
                        )
                        
                        logger.info(f"发现 {len(doc_records)} 条文档记录")
                        
                        # 检查路径是否有效
                        valid_count = 0
                        invalid_count = 0
                        invalid_doc_ids = []
                        
                        for record in doc_records:
                            doc_id = record.get('doc_id')
                            file_path = record.get('doc', '')
                            
                            # 检查文件是否存在
                            file_exists = False
                            
                            # 1. 检查原始路径
                            if file_path and os.path.exists(file_path):
                                file_exists = True
                            else:
                                # 2. 检查基于配置目录的路径
                                possible_path = os.path.join(config_folder, file_path)
                                if os.path.exists(possible_path):
                                    file_exists = True
                                else:
                                    # 3. 尝试使用文件名
                                    file_name = os.path.basename(file_path)
                                    possible_path = os.path.join(config_folder, file_name)
                                    if os.path.exists(possible_path):
                                        file_exists = True
                            
                            if file_exists:
                                valid_count += 1
                            else:
                                invalid_count += 1
                                invalid_doc_ids.append(doc_id)
                        
                        logger.info(f"集合 {collection_name} 中有 {valid_count} 条有效记录，{invalid_count} 条无效记录")
                        
                        # 删除无效记录
                        if invalid_count > 0:
                            logger.info(f"正在删除 {len(invalid_doc_ids)} 条无效记录")
                            
                            # 分批删除，避免表达式过长
                            batch_size = 50  # 减少批量大小
                            for i in range(0, len(invalid_doc_ids), batch_size):
                                batch = invalid_doc_ids[i:i+batch_size]
                                if not batch:
                                    continue
                                
                                # 构建精确的doc_id删除表达式
                                doc_id_list = ",".join(str(doc_id) for doc_id in batch)
                                delete_expr = f"doc_id in [{doc_id_list}]"
                                
                                try:
                                    client.delete(
                                        collection_name=collection_name,
                                        filter=delete_expr
                                    )
                                    logger.info(f"已删除批次 {i//batch_size + 1}/{(len(invalid_doc_ids) + batch_size - 1)//batch_size} 的无效记录 ({len(batch)}个)")
                                except Exception as batch_err:
                                    logger.error(f"删除批次 {i//batch_size + 1} 失败: {str(batch_err)}")
                        else:
                            logger.info(f"集合 {collection_name} 无需清理无效数据")
                    else:
                        logger.info(f"集合 {collection_name} 无数据，跳过清理")
                        
                except Exception as stats_err:
                    if "collection not found" in str(stats_err):
                        logger.info(f"集合 {collection_name} 不存在，跳过清理")
                    else:
                        logger.error(f"清理配置 {cfg_id} 时出错: {str(stats_err)}")
            
            logger.info("无效数据清理完成")
        except Exception as e:
            logger.error(f"清理无效数据时出错: {str(e)}")
            traceback.print_exc()
    
    # 清理 RAG 实例
    if rag_instance is not None:
        try:
            rag_instance.close()
            logger.info("RAG 资源已关闭")
        except Exception as e:
            logger.error(f"关闭 RAG 资源时出错: {str(e)}")
            traceback.print_exc()
    
    # 清空实例引用
    rag_instance = None
    
    # 保存配置
    try:
        save_rag_configurations()
        logger.info("已保存RAG配置")
    except Exception as e:
        logger.error(f"保存配置时出错: {str(e)}")
    
    # 清理套接字文件
    cleanup_socket_files()
    
    # 清理用户任务数据 - 新增
    try:
        # 记录当前数据大小
        total_tasks = len(processing_tasks)
        total_user_tasks = sum(len(tasks) for tasks in user_tasks.values())
        
        # 清理用户任务目录
        tasks_dir = os.path.join(TASKS_STORAGE_PATH, "tasks")
        if os.path.exists(tasks_dir):
            try:
                import shutil
                # 记录删除前的文件数量
                file_count = len([f for f in os.listdir(tasks_dir) if f.endswith('.json')])
                logger.info(f"准备删除 {file_count} 个任务文件")
                
                # 删除目录中的所有文件
                for filename in os.listdir(tasks_dir):
                    file_path = os.path.join(tasks_dir, filename)
                    try:
                        if os.path.isfile(file_path):
                            os.unlink(file_path)
                        elif os.path.isdir(file_path):
                            shutil.rmtree(file_path)
                    except Exception as e:
                        logger.error(f"删除文件 {file_path} 失败: {str(e)}")
                
                logger.info(f"已清理任务文件目录")
            except Exception as dir_err:
                logger.error(f"清理任务目录失败: {str(dir_err)}")
        
        # 清空任务映射文件
        user_tasks_file = os.path.join(TASKS_STORAGE_PATH, "user_tasks.json")
        task_users_file = os.path.join(TASKS_STORAGE_PATH, "task_users.json")
        
        # 写入空映射
        with open(user_tasks_file, 'w', encoding='utf-8') as f:
            json.dump({}, f)
            
        with open(task_users_file, 'w', encoding='utf-8') as f:
            json.dump({}, f)
            
        # 清空内存中的任务数据
        processing_tasks.clear()
        user_tasks.clear()
        task_users.clear()
        
        logger.info(f"已成功清理所有用户任务数据: {total_tasks} 个处理任务, {total_user_tasks} 个用户关联任务")
    except Exception as e:
        logger.error(f"清理用户任务数据时出错: {str(e)}")
        traceback.print_exc()
    
    # 清理会话状态缓存
    try:
        global user_session_states
        session_count = len(user_session_states)
        user_session_states.clear()
        logger.info(f"已清理 {session_count} 个用户会话状态")
    except Exception as e:
        logger.error(f"清理会话状态时出错: {str(e)}")
    
    logger.info("资源清理完成")

# 信号处理函数
def signal_handler(sig, frame):
    """处理终止信号，确保清理所有资源和用户任务"""
    global is_shutting_down, shutdown_start_time
    
    # 检查是否已在关闭过程中
    if is_shutting_down:
        current_time = time.time()
        # 如果关闭过程超过10秒，强制退出
        if shutdown_start_time and (current_time - shutdown_start_time > 10):
            logger.warning("关闭过程超时，强制退出")
            os._exit(1)
        return
    
    is_shutting_down = True
    shutdown_start_time = time.time()
    
    logger.info(f"接收到信号 {sig}，正在清理资源和用户任务...")
    
    # 调用清理函数
    cleanup_resources()
    
    # 确保任务数据已被清空
    if processing_tasks or user_tasks or task_users:
        logger.warning("检测到任务数据未完全清空，强制清空...")
        processing_tasks.clear()
        user_tasks.clear()
        task_users.clear()
    
    logger.info("资源和用户任务清理完成，正常退出")
    sys.exit(0)

# 主函数
if __name__ == '__main__':
    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 初始化知识库结构
    initialize_knowledge_base_structure()

    # 清理可能残留的套接字文件
    cleanup_socket_files()
    
    # 任务管理器会自动加载任务（已在导入时完成）
    logger.info("任务管理器已就绪")

    # 初始化 RAG 系统
    initialize_rag()
    
    # 使用工具函数扫描知识库目录并处理文件
    from Rag_Framework.utils import scan_and_process_knowledge_base
    scan_and_process_knowledge_base(rag_instance, rag_configurations)
    
    # 注册程序退出清理函数
    atexit.register(cleanup_resources)
    
    logger.info("=" * 50)
    logger.info(f"ColPali RAG Manager GPU{GPU_ID}实例{INSTANCE_ID}已启动（Milvus Standalone模式）")
    logger.info(f"运行于 http://10.98.64.22:{RAG_MANAGER_PORT}")
    logger.info("并行增强: 支持多用户并发知识检索")
    logger.info("=" * 50)
    
    print("✅ RAG Manager 服务启动完成")
    print(f"📍 服务地址: http://10.98.64.22:{RAG_MANAGER_PORT}")
    print("📋 主要端点: /get_relevant_context")
    print(f"🔗 状态检查: http://10.98.64.22:{RAG_MANAGER_PORT}/health")
    
    # 启动 Flask 应用
    app.run(debug=False, host='0.0.0.0', port=RAG_MANAGER_PORT, threaded=True)

'''
工作流程
---1. 初始化和配置
初始化过程:
1. 加载知识库配置 (config_manager.py)
2. 初始化知识库目录结构 (config_manager.py)
3. 加载ColPali模型和处理器 (colpali_manager.py)
4. 设置Milvus连接和集合 (colpali_manager.py + milvus_retriever.py)
5. 从Milvus数据库同步文档信息 (colpali_manager.py)

---2. 文档处理
文档处理流程:
1. 接收PDF文件上传 (colpali_rag_manager.py - /upload_rag_document)
2. 创建处理任务并启动线程 (colpali_rag_manager.py)
3. 将PDF转换为图像 (colpali_manager.py - process_file)
4. 使用ColPali模型生成页面向量嵌入 (colpali_manager.py)
5. 将嵌入向量和元数据插入Milvus (colpali_manager.py + milvus_retriever.py)
6. 更新处理状态 (colpali_rag_manager.py)

---3. 检索过程
检索流程:
1. 接收用户查询 (colpali_rag_manager.py - /get_relevant_context 或 /rag_query)
2. 使用ColPali模型将查询转换为向量 (colpali_manager.py - search)
3. 执行初始向量相似度搜索 (milvus_retriever.py - search)
4. 对初步结果进行批处理和重排序 (milvus_retriever.py)
5. 对最终结果应用PDF处理 (milvus_retriever.py + colpali_manager.py)
6. 格式化和返回结果 (colpali_manager.py + colpali_rag_manager.py)

---4. 知识库管理
知识库管理功能:
1. 创建新知识库配置 (colpali_rag_manager.py - /create_rag_configuration)
2. 切换活跃知识库 (colpali_rag_manager.py - /set_active_configuration)
3. 删除知识库配置和相关资源 (colpali_rag_manager.py - /delete_rag_configuration)
4. 获取知识库中的文档列表 (colpali_rag_manager.py - /get_rag_documents)
5. 删除文档并清理相关资源 (colpali_rag_manager.py - /delete_rag_document)
6. 清空知识库集合 (colpali_rag_manager.py - /clear_milvus_collection)
'''