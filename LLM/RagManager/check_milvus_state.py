#!/usr/bin/env python3

import os
import sys
import hashlib

# 添加项目路径到Python路径
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

from pymilvus import connections, utility, Collection

def check_milvus_state():
    """检查Milvus数据库状态"""
    try:
        # 连接到Milvus
        connections.connect("default", host="localhost", port="19530")
        
        # 检查所有数据库
        databases = utility.list_database()
        print(f"所有数据库: {databases}")
        
        # 检查rag_default数据库
        if "rag_default" in databases:
            utility.using_database("rag_default")
            collections = utility.list_collections()
            print(f"rag_default数据库中的集合: {collections}")
            
            if "default" in collections:
                collection = Collection("default")
                collection.load()
                
                # 检查集合统计信息
                stats = collection.get_stats()
                print(f"default集合统计信息: {stats}")
                
                # 查询所有记录
                try:
                    results = collection.query(
                        expr="seq_id >= 0",
                        output_fields=["doc_id", "doc", "seq_id"],
                        limit=100
                    )
                    print(f"default集合中有 {len(results)} 条记录")
                    
                    # 显示前几条记录
                    for i, result in enumerate(results[:5]):
                        print(f"记录 {i+1}: doc_id={result['doc_id']}, doc={result['doc']}, seq_id={result['seq_id']}")
                        
                except Exception as query_err:
                    print(f"查询记录失败: {query_err}")
            else:
                print("default集合不存在")
        else:
            print("rag_default数据库不存在")
    
    except Exception as e:
        print(f"检查Milvus状态失败: {e}")
    finally:
        try:
            connections.disconnect("default")
        except:
            pass

def check_file_ids():
    """检查文件ID计算"""
    knowledge_dir = "/data/Web-FabGPT/LLM/RagManager/knowledge_base/global/default"
    print(f"\n检查目录: {knowledge_dir}")
    
    if os.path.exists(knowledge_dir):
        for filename in os.listdir(knowledge_dir):
            if filename.endswith('.pdf'):
                file_path = os.path.abspath(os.path.join(knowledge_dir, filename))
                file_id = int(hashlib.md5(file_path.encode()).hexdigest()[:8], 16)
                print(f"文件: {filename}")
                print(f"路径: {file_path}")
                print(f"ID: {file_id}")
                print("---")
    else:
        print("目录不存在")

if __name__ == "__main__":
    print("=== 检查Milvus状态 ===")
    check_milvus_state()
    
    print("\n=== 检查文件ID ===")
    check_file_ids()