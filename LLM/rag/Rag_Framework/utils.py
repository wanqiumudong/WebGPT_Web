"""
工具函数模块 - 提供各种辅助功能
utils.py
"""

import os
import glob
import logging
import torch
import hashlib
import json
logger = logging.getLogger("ColPali-RAG-Manager")

def check_file_size(file, max_size_mb=50):
    """检查文件大小是否超过限制"""
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)  # 重置文件指针
    
    max_size_bytes = max_size_mb * 1024 * 1024
    
    if file_size > max_size_bytes:
        logger.warning(f"文件过大: {file_size} 字节, 超过限制 {max_size_bytes} 字节")
        return False
    
    return True

def cleanup_socket_files():
    """清理Milvus套接字文件"""
    try:
        cleaned_files = []
        
        socket_patterns = [
            "/tmp/milvus*.sock*"
        ]
        
        for pattern in socket_patterns:
            for sock_file in glob.glob(pattern):
                try:
                    if os.path.exists(sock_file):
                        os.remove(sock_file)
                        cleaned_files.append(sock_file)
                        logger.info(f"已清理套接字文件: {sock_file}")
                except Exception as e:
                    logger.warning(f"清理套接字文件失败: {sock_file}, 错误: {str(e)}")
        
        return cleaned_files
    except Exception as e:
        logger.error(f"套接字文件清理过程出错: {str(e)}")
        return []

def optimize_gpu_memory():
    """优化GPU内存使用"""
    if torch.cuda.is_available():
        # 清理缓存
        torch.cuda.empty_cache()
        # 尝试触发垃圾回收
        import gc
        gc.collect()
        
        # 记录内存状态
        allocated = torch.cuda.memory_allocated() / (1024 ** 3)
        reserved = torch.cuda.memory_reserved() / (1024 ** 3)
        logger.info(f"GPU内存状态 - 已分配: {allocated:.2f} GB, 已预留: {reserved:.2f} GB")
    return True

def generate_doc_id(path):
    """从文件路径生成文档ID"""
    import hashlib
    hash_str = hashlib.md5(path.encode()).hexdigest()
    # 取前8位十六进制数转为整数
    doc_id = int(hash_str[:8], 16)
    return doc_id

def scan_and_process_knowledge_base(rag_instance, rag_configurations):
    """扫描所有知识库目录并处理发现的文件，确保使用一致的文档ID"""
    from tqdm import tqdm
    
    logger.info("开始扫描知识库目录...")
    
    # 获取活跃的知识库配置
    active_config = None
    for config_id, config in rag_configurations.items():
        if config.get('active', False):
            active_config = config
            break
    
    # 如果没有活跃配置，使用默认配置
    if not active_config:
        active_config = rag_configurations.get('default')
        if not active_config:
            logger.error("找不到活跃或默认知识库配置，无法扫描")
            return False
    
    # 获取当前活跃的知识库目录
    knowledge_dir = active_config.get('folder')
    config_id = active_config.get('id')
    
    if not knowledge_dir or not os.path.isdir(knowledge_dir):
        logger.error(f"知识库目录不存在: {knowledge_dir}")
        return False
    
    logger.info(f"扫描知识库目录: {knowledge_dir}")
    
    # 获取 RAG 实例
    if not rag_instance:
        logger.error("RAG 系统未初始化，无法扫描知识库")
        return False
    
    # 获取已知的文档路径，用于跳过已处理的文档
    processed_paths = set()
    processed_ids = set()
    if hasattr(rag_instance, 'documents'):
        for doc_id, doc_info in rag_instance.documents.items():
            file_path = doc_info.file_path if hasattr(doc_info, 'file_path') else None
            if file_path and hasattr(doc_info, 'processed') and doc_info.processed:
                processed_paths.add(os.path.abspath(file_path))
                processed_ids.add(doc_id)

    # 额外检查Milvus中的记录，确保没有遗漏
    if hasattr(rag_instance, 'retriever') and rag_instance.retriever:
        try:
            # 直接查询Milvus获取所有已处理的文档
            all_docs = rag_instance.retriever.client.query(
                collection_name=rag_instance.retriever.collection_name,
                filter="seq_id == 0",
                output_fields=["doc_id", "doc"],
                limit=10000
            )
            
            for doc in all_docs:
                doc_id = doc.get('doc_id')
                file_path = doc.get('doc')
                if doc_id and file_path:
                    # 将基础doc_id添加到已处理列表
                    base_doc_id = doc_id // 1000
                    processed_ids.add(base_doc_id)
                    if os.path.exists(file_path):
                        processed_paths.add(os.path.abspath(file_path))
            
            logger.info(f"从Milvus额外获取了{len(all_docs)}个文档记录用于状态检查")
        except Exception as milvus_err:
            logger.warning(f"从Milvus查询文档状态失败: {str(milvus_err)}")
    
    # 扫描目录中的所有PDF文件
    supported_extensions = {'.pdf'}
    files_to_process = []

    for root, _, files in os.walk(knowledge_dir):
        for filename in files:
            file_path = os.path.abspath(os.path.join(root, filename))
            file_ext = os.path.splitext(filename)[1].lower()
            
            # 检查是否是支持的文件类型
            if file_ext in supported_extensions:
                # 计算该文件的稳定ID
                file_id = int(hashlib.md5(file_path.encode()).hexdigest()[:8], 16)
                
                # 如果文件路径不在已处理列表中，且ID不在已处理ID列表中，则处理
                if file_path not in processed_paths and file_id not in processed_ids:
                    files_to_process.append((file_path, file_id))
    
    # 处理找到的文件
    if not files_to_process:
        logger.info("没有找到需要处理的新文件")
        return True
    
    logger.info(f"找到 {len(files_to_process)} 个新文件需要处理")
    
    # 按文件修改时间排序，优先处理较新的文件
    files_to_process.sort(key=lambda x: os.path.getmtime(x[0]), reverse=True)
    
    # 处理文件，添加进度条
    processed_count = 0
    for i, (file_path, file_id) in enumerate(tqdm(files_to_process, desc="处理知识库文件")):
        file_name = os.path.basename(file_path)
        logger.info(f"处理文件 [{i+1}/{len(files_to_process)}]: {file_name}, ID: {file_id}")
        
        try:
            # 创建输出目录
            page_output_dir = os.path.join(active_config['db_path'], 'pages')
            os.makedirs(page_output_dir, exist_ok=True)
            
            # 处理PDF，使用计算好的稳定ID
            result = rag_instance.process_file(
                file_path=file_path,
                output_dir=page_output_dir,
                doc_id=file_id
            )
            
            if result and (result.get('success') or result.get('already_processed')):
                processed_count += 1
                logger.info(f"文件处理成功: {file_name}, 使用ID: {file_id}")
            else:
                logger.error(f"文件处理失败: {file_name}, 使用ID: {file_id}")
        except Exception as e:
            logger.error(f"处理文件时出错: {file_name}, 错误: {str(e)}")
    
    logger.info(f"知识库扫描完成，成功处理 {processed_count}/{len(files_to_process)} 个文件")
    return processed_count > 0

# 数据库一致性检查和修复功能
def cleanup_orphaned_data(client, collection_name, config_folder):
    """清理没有对应实际文件的孤立数据库记录,并刷新和压缩数据"""
    supported_extensions = ['.pdf']
    try:
        logger.info(f"开始清理集合 {collection_name} 中的孤立数据")
        
        # 获取当前目录中所有PDF文件的路径
        existing_files = []
        file_names = set()
        
        for root, _, files in os.walk(config_folder):
            for file in files:
                file_ext = os.path.splitext(file)[1].lower()
                if file_ext in supported_extensions:
                    file_path = os.path.join(root, file)
                    existing_files.append(file_path)
                    file_names.add(file)
        
        logger.info(f"目录中找到 {len(existing_files)} 个PDF文件")
        
        # 从Milvus中获取所有文档记录
        try:
            # 先检查集合统计信息
            stats_before = client.get_collection_stats(
                collection_name=collection_name
            )
            row_count_before = stats_before.get("row_count", 0)
            logger.info(f"清理前集合记录数: {row_count_before}")
            
            doc_records = client.query(
                collection_name=collection_name,
                filter="seq_id == 0",  # 只获取元数据记录
                output_fields=["doc_id", "doc"],
                limit=10000  # 设置一个较大的限制
            )
            
            logger.info(f"从Milvus中找到 {len(doc_records)} 条文档记录")
            
            # 收集要删除的doc_id批次,以批量删除提高效率
            delete_batches = []
            orphaned_count = 0
            
            # 检查每条记录是否有对应的文件
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
                    possible_path = os.path.join(config_folder, os.path.basename(file_path))
                    if os.path.exists(possible_path):
                        file_exists = True
                    else:
                        # 3. 检查文件名是否在目录中
                        file_name = os.path.basename(file_path) if file_path else None
                        if file_name in file_names:
                            file_exists = True
                
                # 如果文件不存在,收集删除表达式
                if not file_exists:
                    orphaned_count += 1
                    
                    # 计算要删除的doc_id范围
                    # 对于一个文档,doc_id范围是 min_doc_id 到 max_doc_id - 1
                    min_doc_id = (doc_id // 1000) * 1000
                    max_doc_id = min_doc_id + 1000
                    delete_expr = f"doc_id >= {min_doc_id} AND doc_id < {max_doc_id}"
                    delete_batches.append(delete_expr)
                    
                    logger.info(f"标记孤立记录: doc_id={doc_id}, file_path={file_path}")
            
            # 批量删除收集的所有孤立记录
            if delete_batches:
                # 将多个表达式合并为一个OR表达式
                combined_expr = " OR ".join([f"({expr})" for expr in delete_batches])
                
                try:
                    logger.info(f"执行批量删除,删除表达式: {combined_expr[:200]}..." if len(combined_expr) > 200 else combined_expr)
                    delete_result = client.delete(
                        collection_name=collection_name,
                        filter=combined_expr
                    )
                    logger.info(f"批量删除结果: {delete_result}")
                    
                    # 强制刷新集合,确保删除生效
                    client.flush([collection_name])
                    logger.info(f"已刷新集合 {collection_name}")
                    
                    # 执行压缩操作,回收空间
                    compact_result = client.compact(collection_name)
                    logger.info(f"压缩结果: {compact_result}")
                    
                    # 重新检查集合统计
                    stats_after = client.get_collection_stats(
                        collection_name=collection_name
                    )
                    row_count_after = stats_after.get("row_count", 0)
                    deleted_rows = row_count_before - row_count_after
                    logger.info(f"清理后集合记录数: {row_count_after}, 删除了 {deleted_rows} 行")
                except Exception as del_err:
                    logger.error(f"批量删除孤立记录失败: {str(del_err)}")
            
            logger.info(f"已标记 {orphaned_count} 条孤立数据库记录")
            
            return {
                "success": True,
                "orphaned_records_cleaned": orphaned_count,
                "total_records": len(doc_records),
                "existing_files": len(existing_files),
                "rows_before": row_count_before,
                "rows_after": stats_after.get("row_count", 0) if 'stats_after' in locals() else "未知"
            }
        
        except Exception as query_err:
            logger.error(f"查询文档记录失败: {str(query_err)}")
            return {
                "success": False,
                "error": f"查询文档记录失败: {str(query_err)}"
            }
    
    except Exception as e:
        logger.error(f"清理孤立数据失败: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }