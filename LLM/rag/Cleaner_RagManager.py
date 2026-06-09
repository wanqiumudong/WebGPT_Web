"""
Milvus 数据完全清理工具
用于删除Milvus实例中的所有记录和数据（保留default数据库结构）
"""
import os
import time
import logging
import argparse
from pymilvus import MilvusClient
from pymilvus.exceptions import MilvusException

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("Milvus-Data-Cleaner")

# 默认连接参数
DEFAULT_MILVUS_URI = "http://localhost:19530"

def clear_all_milvus_data(uri: str, token: str = None, force: bool = False):
    """
    清理 Milvus 中的所有数据
    1. 清空所有数据库中的所有集合
    2. 删除所有自定义数据库（保留default）
    """
    try:
        logger.info(f"连接到 Milvus: {uri}")
        
        # 创建客户端连接
        if token:
            client = MilvusClient(uri=uri, token=token)
        else:
            client = MilvusClient(uri=uri)
        
        logger.info("成功连接到 Milvus")
        
        # 获取所有数据库
        all_databases = client.list_databases()
        logger.info(f"发现数据库: {all_databases}")
        
        # 清理每个数据库中的集合
        for db_name in all_databases:
            try:
                logger.info(f"正在处理数据库: {db_name}")
                
                # 切换到目标数据库
                client.use_database(db_name)
                
                # 获取该数据库中的所有集合
                collections = client.list_collections()
                
                if collections:
                    logger.info(f"数据库 '{db_name}' 中的集合: {collections}")
                    
                    # 删除每个集合中的所有数据
                    for collection_name in collections:
                        try:
                            logger.info(f"正在清空集合: {collection_name}")
                            
                            # 尝试使用通配符删除所有数据
                            try:
                                # 方法1：使用空过滤器删除所有数据
                                result = client.delete(
                                    collection_name=collection_name,
                                    filter=""  # 空过滤器匹配所有记录
                                )
                                logger.info(f"删除结果: {result}")
                            except Exception as e:
                                logger.warning(f"方法1失败，尝试方法2: {e}")
                                
                                # 方法2：如果方法1失败，直接删除并重建集合
                                # 获取集合的索引信息
                                try:
                                    # 删除集合（这会删除所有数据）
                                    client.drop_collection(collection_name)
                                    logger.info(f"已删除集合: {collection_name}")
                                except Exception as drop_err:
                                    logger.error(f"删除集合失败: {drop_err}")
                            
                        except Exception as coll_err:
                            logger.error(f"处理集合 {collection_name} 失败: {coll_err}")
                else:
                    logger.info(f"数据库 '{db_name}' 中没有集合")
                
                # 如果是非default数据库，询问是否删除整个数据库
                if db_name != "default":
                    if not force:
                        confirm = input(f"是否删除整个数据库 '{db_name}'? (y/n): ")
                        if confirm.lower() == 'y':
                            # 切换回default数据库
                            client.use_database("default")
                            # 删除整个数据库
                            client.drop_database(db_name)
                            logger.info(f"已删除数据库: {db_name}")
                    else:
                        # 在force模式下，直接删除非default数据库
                        client.use_database("default")
                        client.drop_database(db_name)
                        logger.info(f"已删除数据库: {db_name}")
                        
            except Exception as db_err:
                logger.error(f"处理数据库 {db_name} 失败: {db_err}")
                continue
        
        # 最后确保回到default数据库
        client.use_database("default")
        logger.info("数据清理完成")
        
    except MilvusException as e:
        logger.error(f"Milvus 操作失败: {e}")
    except Exception as e:
        logger.error(f"执行过程中发生错误: {e}")

def main():
    parser = argparse.ArgumentParser(description="Milvus 数据完全清理工具")
    
    parser.add_argument(
        '--uri', 
        type=str, 
        default=DEFAULT_MILVUS_URI,
        help=f"Milvus 服务 URI (默认: {DEFAULT_MILVUS_URI})"
    )
    
    parser.add_argument(
        '--token', 
        type=str, 
        default=None,
        help="Milvus 认证 token (如果需要)"
    )
    
    parser.add_argument(
        '--force', 
        action='store_true', 
        help="强制执行，不询问确认"
    )
    
    args = parser.parse_args()
    
    # 安全确认
    if not args.force:
        print("="*50)
        print("警告：此操作将删除 Milvus 中的所有数据！")
        print("这包括所有集合中的所有记录，以及所有非default数据库！")
        print("此操作不可逆！")
        print("="*50)
        
        confirm = input("确定要继续吗？输入 'DELETE' 确认: ")
        if confirm != 'DELETE':
            logger.info("操作已取消")
            return
    
    # 执行清理
    clear_all_milvus_data(
        uri=args.uri,
        token=args.token,
        force=args.force
    )

if __name__ == "__main__":
    main()