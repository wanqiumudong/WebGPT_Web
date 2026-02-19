#!/usr/bin/env python3
"""
Milvus数据库清理脚本
清理RAG系统中混乱的数据库和集合命名
"""

import os
import sys
import logging
from pathlib import Path

# 添加路径以便导入相关模块
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def clean_milvus_databases():
    """清理Milvus中的混乱数据库"""
    try:
        from pymilvus import connections, utility, Collection
        
        # 连接Milvus
        connections.connect("default", host="localhost", port="19530")
        logger.info("已连接到Milvus服务")
        
        # 获取所有数据库
        databases = utility.list_database()
        logger.info(f"当前数据库列表: {databases}")
        
        # 需要清理的数据库
        databases_to_clean = []
        valid_databases = ['default', 'rag_default', 'rag_none']
        
        for db in databases:
            if db.startswith('rag_') and db not in valid_databases:
                databases_to_clean.append(db)
        
        logger.info(f"需要清理的数据库: {databases_to_clean}")
        
        # 清理无效数据库
        for db_name in databases_to_clean:
            try:
                logger.warning(f"准备删除数据库: {db_name}")
                
                # 切换到要删除的数据库
                utility.using_database(db_name)
                
                # 获取该数据库中的所有集合
                collections = utility.list_collections()
                logger.info(f"数据库 {db_name} 中的集合: {collections}")
                
                # 删除所有集合
                for collection_name in collections:
                    try:
                        utility.drop_collection(collection_name)
                        logger.info(f"已删除集合: {collection_name}")
                    except Exception as e:
                        logger.error(f"删除集合 {collection_name} 失败: {str(e)}")
                
                # 切换回默认数据库
                utility.using_database("default")
                
                # 删除数据库
                utility.drop_database(db_name)
                logger.info(f"✅ 已删除数据库: {db_name}")
                
            except Exception as e:
                logger.error(f"删除数据库 {db_name} 失败: {str(e)}")
        
        # 验证清理结果
        final_databases = utility.list_database()
        logger.info(f"清理后的数据库列表: {final_databases}")
        
        # 检查每个合法数据库的集合
        for db_name in ['rag_default', 'rag_none']:
            if db_name in final_databases:
                utility.using_database(db_name)
                collections = utility.list_collections()
                logger.info(f"数据库 {db_name} 中的集合: {collections}")
        
        # 切换回默认数据库
        utility.using_database("default")
        
        connections.disconnect("default")
        logger.info("✅ Milvus数据库清理完成")
        
        return True
        
    except ImportError:
        logger.error("❌ pymilvus模块未安装，跳过Milvus清理")
        return False
    except Exception as e:
        logger.error(f"❌ Milvus清理失败: {str(e)}")
        return False

def main():
    """主函数"""
    
    print("=" * 60)
    print("Milvus数据库清理脚本")
    print("=" * 60)
    
    # 确认操作
    print("\n⚠️  警告：此操作将删除以下数据库：")
    print("   - rag_global_default")
    print("   - 任何不在合法列表中的rag_*数据库")
    print("\n✅ 保留的数据库：")
    print("   - default (Milvus默认)")
    print("   - rag_default (对应default配置)")
    print("   - rag_none (对应none配置)")
    
    confirm = input("\n确认执行清理操作? (yes/NO): ").strip().lower()
    if confirm not in ['yes', 'y']:
        print("❌ 用户取消操作")
        return
    
    # 执行清理
    print("\n开始执行Milvus数据库清理...")
    success = clean_milvus_databases()
    
    if success:
        print("\n" + "=" * 60)
        print("✅ 清理成功！")
        print("现在可以重启RAG Manager服务进行验证")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("❌ 清理失败，请检查错误信息")
        print("=" * 60)

if __name__ == "__main__":
    main()