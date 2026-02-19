#!/usr/bin/env python3
"""
清理无效的Milvus集合和数据库 - 修复连字符问题
"""
import os
import re
import sys
import json
import logging
from pymilvus import MilvusClient, utility, connections

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def sanitize_name(name):
    """清理名称，确保Milvus兼容性"""
    if not name:
        return "anonymous"
    # 只保留字母数字和下划线，连字符转换为下划线
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '', str(name))
    safe_name = safe_name.replace('-', '_')
    return safe_name.lower()[:50]

def cleanup_invalid_collections():
    """清理包含连字符的无效集合和数据库"""
    try:
        # 连接到Milvus
        client = MilvusClient(uri="http://localhost:19530")
        
        # 获取所有数据库
        databases = client.list_databases()
        logger.info(f"发现 {len(databases)} 个数据库")
        
        invalid_databases = []
        valid_mappings = {}
        
        for db_name in databases:
            if db_name in ['default']:
                continue
                
            logger.info(f"检查数据库: {db_name}")
            
            # 检查数据库名称是否包含连字符
            if '-' in db_name:
                invalid_databases.append(db_name)
                
                # 生成有效的数据库名称
                sanitized_name = sanitize_name(db_name)
                valid_mappings[db_name] = sanitized_name
                
                logger.warning(f"发现无效数据库（包含连字符）: {db_name}")
                logger.info(f"建议的有效名称: {sanitized_name}")
            
            # 切换到数据库并检查集合
            try:
                client.use_database(db_name)
                collections = client.list_collections()
                
                for collection_name in collections:
                    if '-' in collection_name:
                        logger.warning(f"发现无效集合（包含连字符）: {collection_name} (在数据库 {db_name} 中)")
                        
                        # 生成有效的集合名称
                        sanitized_collection = sanitize_name(collection_name)
                        logger.info(f"建议的有效集合名称: {sanitized_collection}")
                        
            except Exception as e:
                logger.error(f"检查数据库 {db_name} 的集合时出错: {e}")
        
        if invalid_databases:
            logger.warning(f"发现 {len(invalid_databases)} 个包含连字符的无效数据库:")
            for db in invalid_databases:
                logger.warning(f"  - {db} -> {valid_mappings.get(db, 'unknown')}")
            
            # 询问是否进行清理
            print("\n是否要删除这些无效的数据库？(y/N): ", end='')
            response = input().strip().lower()
            
            if response == 'y':
                logger.info("开始清理无效数据库...")
                for db_name in invalid_databases:
                    try:
                        client.drop_database(db_name)
                        logger.info(f"已删除数据库: {db_name}")
                    except Exception as e:
                        logger.error(f"删除数据库 {db_name} 失败: {e}")
            else:
                logger.info("跳过清理，保留现有数据库")
        else:
            logger.info("未发现包含连字符的无效数据库")
            
        # 检查配置文件中的引用
        config_files = [
            'rag_configurations.json',
            'user_active_configs.json'
        ]
        
        for config_file in config_files:
            if os.path.exists(config_file):
                logger.info(f"检查配置文件: {config_file}")
                try:
                    with open(config_file, 'r', encoding='utf-8') as f:
                        config_data = json.load(f)
                    
                    logger.info(f"{config_file} 内容: {json.dumps(config_data, indent=2, ensure_ascii=False)}")
                    
                    # 检查是否有包含连字符的配置ID
                    invalid_configs = []
                    if isinstance(config_data, dict):
                        for key, value in config_data.items():
                            if isinstance(key, str) and '-' in key:
                                invalid_configs.append(key)
                            if isinstance(value, str) and '-' in value:
                                invalid_configs.append(value)
                    
                    if invalid_configs:
                        logger.warning(f"在 {config_file} 中发现包含连字符的配置: {invalid_configs}")
                    
                except Exception as e:
                    logger.error(f"读取配置文件 {config_file} 失败: {e}")
            
        logger.info("清理检查完成")
        
    except Exception as e:
        logger.error(f"清理过程中出错: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    cleanup_invalid_collections()