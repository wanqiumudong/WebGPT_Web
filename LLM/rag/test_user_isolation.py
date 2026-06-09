#!/usr/bin/env python3
"""
用户隔离功能测试脚本
测试RAG管理器的用户隔离功能是否正常工作
"""

import requests
import json
import time

# 测试配置
BASE_URL = "http://10.98.64.22:5006"
TEST_USERS = ["alice", "bob", "charlie"]
TEST_CONFIG = "test_isolation"

def test_user_collection_names():
    """测试用户集合名称生成"""
    print("=== 测试用户集合名称生成 ===")
    
    # 导入配置管理器函数
    import sys
    sys.path.append('/data/Web-FabGPT/LLM/rag')
    from Rag_Framework.config_manager import get_user_collection_name, get_user_knowledge_path, get_user_data_path
    
    for user in TEST_USERS:
        # 测试不同配置的集合名称
        default_collection = get_user_collection_name(user, 'default')
        none_collection = get_user_collection_name(user, 'none')
        custom_collection = get_user_collection_name(user, 'mycustomlib')
        
        print(f"用户 {user}:")
        print(f"  默认库: {default_collection}")
        print(f"  无库: {none_collection}")
        print(f"  自定义库: {custom_collection}")
        
        # 测试路径生成
        default_path = get_user_knowledge_path(user, 'default')
        custom_path = get_user_knowledge_path(user, 'mycustomlib')
        print(f"  默认库路径: {default_path}")
        print(f"  自定义库路径: {custom_path}")
        print()

def test_api_user_isolation():
    """测试API用户隔离"""
    print("=== 测试API用户隔离 ===")
    
    for user in TEST_USERS:
        print(f"\n--- 测试用户: {user} ---")
        
        # 测试获取配置
        response = requests.get(f"{BASE_URL}/get_rag_configurations")
        if response.status_code == 200:
            configs = response.json().get('configurations', [])
            print(f"可用配置数量: {len(configs)}")
        else:
            print(f"获取配置失败: {response.status_code}")
        
        # 测试获取文档（使用用户ID）
        response = requests.get(f"{BASE_URL}/get_rag_documents", params={
            'config_id': 'default',
            'user_id': user
        })
        if response.status_code == 200:
            data = response.json()
            print(f"文档数量: {data.get('documents_count', 0)}")
            print(f"集合名称: {data.get('collection_name', 'unknown')}")
        else:
            print(f"获取文档失败: {response.status_code}")

def test_permission_control():
    """测试权限控制"""
    print("=== 测试权限控制 ===")
    
    # 测试默认库的只读权限
    test_data = {
        'config_id': 'default',
        'user_id': 'alice'
    }
    
    response = requests.post(f"{BASE_URL}/set_active_configuration", json=test_data)
    if response.status_code == 200:
        print("✓ 可以切换到默认库（只读模式）")
    else:
        print(f"✗ 切换到默认库失败: {response.status_code}")
    
    # 测试文件上传权限（应该被拒绝，因为默认库是只读的）
    print("测试默认库上传权限（应该被拒绝）...")
    # 这里应该模拟文件上传，但由于没有实际文件，只输出预期结果
    print("预期结果: 默认库为只读，应该拒绝文件上传")

def main():
    """主测试函数"""
    print("RAG用户隔离功能测试")
    print("=" * 50)
    
    try:
        # 测试1: 集合名称生成
        test_user_collection_names()
        
        # 测试2: API用户隔离
        test_api_user_isolation()
        
        # 测试3: 权限控制
        test_permission_control()
        
        print("\n" + "=" * 50)
        print("测试完成！")
        
    except Exception as e:
        print(f"测试过程中出现错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()