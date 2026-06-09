#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MongoDB 对话分支消息清理工具

此脚本用于清理 MongoDB 中存储的聊天机器人对话、分支和消息数据。
可以清理所有数据、指定天数前的数据，以及修复不一致的数据关系。

使用方法:
python mongo_cleanup.py [--all] [--days 7] [--orphaned] [--dryrun] [--uri "mongodb://localhost:27017/"]

参数:
  --all          清理所有数据（危险操作）
  --days N       清理N天前的数据
  --orphaned     只清理孤立的数据（没有父会话的分支、没有对应会话的消息等）
  --dryrun       模拟运行，不实际删除数据
  --uri URI      MongoDB连接URI，默认为"mongodb://localhost:27017/"
"""

import argparse
import sys
import time
from datetime import datetime, timedelta
import pymongo
from pymongo.errors import PyMongoError

# 格式化输出颜色
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def print_header(text):
    print(f"\n{Colors.HEADER}{Colors.BOLD}======== {text} ========{Colors.ENDC}\n")

def print_success(text):
    print(f"{Colors.GREEN}✓ {text}{Colors.ENDC}")

def print_info(text):
    print(f"{Colors.BLUE}ℹ {text}{Colors.ENDC}")

def print_warning(text):
    print(f"{Colors.WARNING}⚠ {text}{Colors.ENDC}")

def print_error(text):
    print(f"{Colors.FAIL}✗ {text}{Colors.ENDC}")

def confirm_action(message):
    response = input(f"{Colors.WARNING}{message} (y/N): {Colors.ENDC}")
    return response.lower() == 'y'

def connect_to_mongodb(uri):
    """连接到MongoDB数据库"""
    try:
        client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')  # 验证连接
        return client
    except PyMongoError as e:
        print_error(f"MongoDB连接失败: {e}")
        sys.exit(1)

def get_db_stats(db):
    """获取数据库统计信息"""
    sessions_count = db.sessions.count_documents({})
    branches_count = db.branches.count_documents({})
    messages_count = db.messages.count_documents({})
    
    return {
        'sessions': sessions_count,
        'branches': branches_count,
        'messages': messages_count
    }

def cleanup_all_data(db, dry_run=False):
    """清理所有对话、分支和消息数据"""
    print_header("全部数据清理")
    
    if dry_run:
        print_info("模拟运行模式，以下操作不会实际执行")
    
    stats_before = get_db_stats(db)
    print_info(f"当前数据统计: {stats_before['sessions']}个会话, {stats_before['branches']}个分支关系, {stats_before['messages']}条消息")
    
    if not dry_run:
        if not confirm_action("⚠️ 警告：此操作将删除所有对话、分支和消息数据，且不可恢复。确定要继续吗?"):
            print_warning("操作已取消")
            return
        
        db.messages.delete_many({})
        db.branches.delete_many({})
        db.sessions.delete_many({})
        
        print_success("已清理所有数据")
    else:
        print_info("如果执行，将删除所有数据")
    
    if not dry_run:
        stats_after = get_db_stats(db)
        print_info(f"清理后数据统计: {stats_after['sessions']}个会话, {stats_after['branches']}个分支关系, {stats_after['messages']}条消息")

def cleanup_old_data(db, days, dry_run=False):
    """清理指定天数前的数据"""
    print_header(f"清理{days}天前的旧数据")
    
    cutoff_date = datetime.now() - timedelta(days=days)
    cutoff_date_str = cutoff_date.isoformat()
    print_info(f"清理截止日期: {cutoff_date_str}")
    
    if dry_run:
        print_info("模拟运行模式，以下操作不会实际执行")
    
    # 查找旧会话
    old_sessions = list(db.sessions.find(
        {"lastActive": {"$lt": cutoff_date_str}},
        {"sessionId": 1, "header": 1, "lastActive": 1}
    ))
    
    if not old_sessions:
        print_info("没有找到需要清理的旧会话")
        return
    
    old_session_ids = [s["sessionId"] for s in old_sessions]
    print_info(f"找到{len(old_session_ids)}个旧会话，最旧的几个会话:")
    
    # 显示一些旧会话的信息
    for i, session in enumerate(sorted(old_sessions, key=lambda x: x.get("lastActive", ""))[:5]):
        last_active = session.get("lastActive", "未知")
        header = session.get("header", "无标题")
        print_info(f"  {i+1}. ID: {session['sessionId']}, 标题: {header}, 最后活跃: {last_active}")
    
    if len(old_sessions) > 5:
        print_info(f"  ... 以及 {len(old_sessions) - 5} 个更多会话")
    
    if not dry_run:
        if not confirm_action(f"确定要删除这{len(old_session_ids)}个旧会话及其相关的分支和消息吗?"):
            print_warning("操作已取消")
            return
        
        # 删除旧会话相关的消息
        messages_result = db.messages.delete_many(
            {"sessionId": {"$in": old_session_ids}}
        )
        
        # 删除旧会话
        sessions_result = db.sessions.delete_many(
            {"sessionId": {"$in": old_session_ids}}
        )
        
        # 删除相关的分支关系
        branches_parent_result = db.branches.delete_many(
            {"parentId": {"$in": old_session_ids}}
        )
        
        branches_child_result = db.branches.delete_many(
            {"childId": {"$in": old_session_ids}}
        )
        
        print_success(f"已删除 {sessions_result.deleted_count} 个旧会话")
        print_success(f"已删除 {messages_result.deleted_count} 条相关消息")
        print_success(f"已删除 {branches_parent_result.deleted_count + branches_child_result.deleted_count} 个相关分支关系")
    else:
        print_info(f"如果执行，将删除 {len(old_session_ids)} 个旧会话及其相关数据")

def cleanup_orphaned_data(db, dry_run=False):
    """清理孤立的数据（没有关联的分支、会话和消息）"""
    print_header("清理孤立数据")
    
    if dry_run:
        print_info("模拟运行模式，以下操作不会实际执行")
    
    # 1. 获取所有会话ID
    all_session_ids = set(s["sessionId"] for s in db.sessions.find({}, {"sessionId": 1}))
    print_info(f"数据库中共有 {len(all_session_ids)} 个会话")
    
    # 2. 查找引用了不存在会话的分支关系
    orphaned_relations = []
    all_branches = list(db.branches.find())
    
    for relation in all_branches:
        child_exists = relation["childId"] in all_session_ids
        parent_exists = relation["parentId"] in all_session_ids
        
        if not child_exists or not parent_exists:
            orphaned_relations.append(relation)
    
    print_info(f"发现 {len(orphaned_relations)} 个孤立的分支关系")
    
    # 3. 查找没有对应会话的消息
    orphaned_messages_count = db.messages.count_documents(
        {"sessionId": {"$nin": list(all_session_ids)}}
    )
    
    print_info(f"发现 {orphaned_messages_count} 条孤立的消息")
    
    # 4. 查找重复的分支关系
    duplicate_relations = []
    relation_map = {}
    
    for relation in all_branches:
        key = f"{relation['parentId']}_{relation['childId']}"
        if key in relation_map:
            duplicate_relations.append(relation)
        else:
            relation_map[key] = relation
    
    print_info(f"发现 {len(duplicate_relations)} 个重复的分支关系")
    
    if not dry_run:
        if orphaned_relations or orphaned_messages_count or duplicate_relations:
            if not confirm_action("确定要清理这些孤立和重复的数据吗?"):
                print_warning("操作已取消")
                return
            
            # 删除孤立的分支关系
            if orphaned_relations:
                orphaned_ids = [r["_id"] for r in orphaned_relations]
                result = db.branches.delete_many({"_id": {"$in": orphaned_ids}})
                print_success(f"已删除 {result.deleted_count} 个孤立的分支关系")
            
            # 删除孤立的消息
            if orphaned_messages_count:
                result = db.messages.delete_many({"sessionId": {"$nin": list(all_session_ids)}})
                print_success(f"已删除 {result.deleted_count} 条孤立的消息")
            
            # 删除重复的分支关系
            if duplicate_relations:
                duplicate_ids = [r["_id"] for r in duplicate_relations]
                result = db.branches.delete_many({"_id": {"$in": duplicate_ids}})
                print_success(f"已删除 {result.deleted_count} 个重复的分支关系")
        else:
            print_info("没有发现需要清理的孤立或重复数据")
    else:
        if orphaned_relations or orphaned_messages_count or duplicate_relations:
            print_info(f"如果执行，将删除 {len(orphaned_relations)} 个孤立的分支关系，{orphaned_messages_count} 条孤立的消息，{len(duplicate_relations)} 个重复的分支关系")
        else:
            print_info("没有发现需要清理的孤立或重复数据")

def find_missing_relations(db, dry_run=False):
    """查找会话表中有parentId但在分支关系表中没有记录的情况，并修复"""
    print_header("修复缺失的分支关系")
    
    if dry_run:
        print_info("模拟运行模式，以下操作不会实际执行")
    
    # 查找有parentId的会话
    sessions_with_parent = list(db.sessions.find(
        {"parentId": {"$exists": True, "$ne": None}},
        {"sessionId": 1, "parentId": 1, "header": 1}
    ))
    
    print_info(f"发现 {len(sessions_with_parent)} 个设置了父会话ID的会话")
    
    missing_relations = []
    
    for session in sessions_with_parent:
        # 检查是否存在对应的分支关系
        relation = db.branches.find_one({
            "childId": session["sessionId"],
            "parentId": session["parentId"]
        })
        
        if not relation:
            missing_relations.append(session)
    
    print_info(f"其中 {len(missing_relations)} 个会话缺少对应的分支关系记录")
    
    if missing_relations:
        for i, session in enumerate(missing_relations[:5]):
            print_info(f"  {i+1}. 子会话: {session['sessionId']} ({session.get('header', '无标题')}), 父会话: {session['parentId']}")
        
        if len(missing_relations) > 5:
            print_info(f"  ... 以及 {len(missing_relations) - 5} 个更多缺失关系")
    
    if not dry_run and missing_relations:
        if not confirm_action(f"确定要为这 {len(missing_relations)} 个会话创建缺失的分支关系吗?"):
            print_warning("操作已取消")
            return
        
        created_count = 0
        for session in missing_relations:
            # 创建缺失的分支关系
            new_relation = {
                "childId": session["sessionId"],
                "parentId": session["parentId"],
                "createTime": datetime.now().isoformat(),
                "order": 0,
                "isActive": True
            }
            
            result = db.branches.insert_one(new_relation)
            if result.inserted_id:
                created_count += 1
        
        print_success(f"已创建 {created_count} 个缺失的分支关系")
    elif missing_relations:
        print_info(f"如果执行，将为 {len(missing_relations)} 个会话创建缺失的分支关系")
    else:
        print_info("没有发现缺失的分支关系")

def find_sessions_without_parentid(db, dry_run=False):
    """查找分支关系表中有记录但会话表中没有parentId的情况，并修复"""
    print_header("修复会话的父会话引用")
    
    if dry_run:
        print_info("模拟运行模式，以下操作不会实际执行")
    
    # 获取所有分支关系
    all_relations = list(db.branches.find())
    print_info(f"数据库中共有 {len(all_relations)} 个分支关系记录")
    
    inconsistent_sessions = []
    
    for relation in all_relations:
        # 检查子会话是否正确设置了parentId
        child_session = db.sessions.find_one({"sessionId": relation["childId"]})
        
        if child_session and (not child_session.get("parentId") or child_session.get("parentId") != relation["parentId"]):
            inconsistent_sessions.append({
                "session": child_session,
                "relation": relation
            })
    
    print_info(f"发现 {len(inconsistent_sessions)} 个会话的parentId与分支关系不一致")
    
    if inconsistent_sessions:
        for i, item in enumerate(inconsistent_sessions[:5]):
            session = item["session"]
            relation = item["relation"]
            current_parent = session.get("parentId") or "未设置"
            print_info(f"  {i+1}. 会话: {session['sessionId']} ({session.get('header', '无标题')}), 当前父ID: {current_parent}, 关系中的父ID: {relation['parentId']}")
        
        if len(inconsistent_sessions) > 5:
            print_info(f"  ... 以及 {len(inconsistent_sessions) - 5} 个更多不一致会话")
    
    if not dry_run and inconsistent_sessions:
        if not confirm_action(f"确定要修复这 {len(inconsistent_sessions)} 个会话的parentId吗?"):
            print_warning("操作已取消")
            return
        
        updated_count = 0
        for item in inconsistent_sessions:
            session = item["session"]
            relation = item["relation"]
            
            # 更新会话的parentId
            result = db.sessions.update_one(
                {"sessionId": session["sessionId"]},
                {"$set": {"parentId": relation["parentId"]}}
            )
            
            if result.modified_count:
                updated_count += 1
        
        print_success(f"已更新 {updated_count} 个会话的parentId")
    elif inconsistent_sessions:
        print_info(f"如果执行，将更新 {len(inconsistent_sessions)} 个会话的parentId")
    else:
        print_info("没有发现需要修复的会话parentId")

def find_duplicate_sessions(db, dry_run=False):
    """查找并合并重复的会话记录"""
    print_header("查找重复的会话记录")
    
    if dry_run:
        print_info("模拟运行模式，以下操作不会实际执行")
    
    # 获取所有会话ID
    session_ids = {}
    duplicates = []
    
    for session in db.sessions.find({}, {"sessionId": 1, "_id": 1, "header": 1, "lastActive": 1}):
        session_id = session["sessionId"]
        
        if session_id in session_ids:
            duplicates.append({
                "sessionId": session_id,
                "original": session_ids[session_id],
                "duplicate": session
            })
        else:
            session_ids[session_id] = session
    
    print_info(f"发现 {len(duplicates)} 个重复的会话记录")
    
    if duplicates:
        for i, dup in enumerate(duplicates[:5]):
            print_info(f"  {i+1}. 会话ID: {dup['sessionId']}, 标题: {dup['duplicate'].get('header', '无标题')}")
        
        if len(duplicates) > 5:
            print_info(f"  ... 以及 {len(duplicates) - 5} 个更多重复会话")
    
    if not dry_run and duplicates:
        if not confirm_action(f"确定要删除这 {len(duplicates)} 个重复的会话记录吗?"):
            print_warning("操作已取消")
            return
        
        deleted_count = 0
        for dup in duplicates:
            # 删除重复的会话记录
            result = db.sessions.delete_one({"_id": dup["duplicate"]["_id"]})
            
            if result.deleted_count:
                deleted_count += 1
        
        print_success(f"已删除 {deleted_count} 个重复的会话记录")
    elif duplicates:
        print_info(f"如果执行，将删除 {len(duplicates)} 个重复的会话记录")
    else:
        print_info("没有发现重复的会话记录")

def main():
    parser = argparse.ArgumentParser(description="MongoDB 对话分支消息清理工具")
    parser.add_argument("--all", action="store_true", help="清理所有数据（危险操作）")
    parser.add_argument("--days", type=int, default=0, help="清理指定天数前的数据")
    parser.add_argument("--orphaned", action="store_true", help="只清理孤立的数据")
    parser.add_argument("--dryrun", action="store_true", help="模拟运行，不实际删除数据")
    parser.add_argument("--uri", type=str, default="mongodb://localhost:27017/", help="MongoDB连接URI")
    parser.add_argument("--db", type=str, default="chatbot_db", help="MongoDB数据库名称")
    
    args = parser.parse_args()
    
    # 连接MongoDB
    print_header("连接到MongoDB")
    client = connect_to_mongodb(args.uri)
    db = client[args.db]
    
    stats = get_db_stats(db)
    print_info(f"已连接到数据库: {args.db}")
    print_info(f"当前数据统计: {stats['sessions']}个会话, {stats['branches']}个分支关系, {stats['messages']}条消息")
    
    try:
        # 根据参数执行不同的清理操作
        if args.all:
            cleanup_all_data(db, args.dryrun)
        elif args.days > 0:
            cleanup_old_data(db, args.days, args.dryrun)
        elif args.orphaned:
            cleanup_orphaned_data(db, args.dryrun)
            find_missing_relations(db, args.dryrun)
            find_sessions_without_parentid(db, args.dryrun)
            find_duplicate_sessions(db, args.dryrun)
        else:
            # 如果没有指定具体操作，显示菜单
            while True:
                print_header("MongoDB 对话分支消息清理工具")
                print("1. 清理所有数据（危险操作）")
                print("2. 清理指定天数前的数据")
                print("3. 清理孤立的分支关系和消息")
                print("4. 修复缺失的分支关系")
                print("5. 修复会话的父会话引用")
                print("6. 查找并合并重复的会话记录")
                print("7. 显示数据库统计信息")
                print("0. 退出")
                
                choice = input("\n请选择操作 [0-7]: ")
                
                if choice == "1":
                    cleanup_all_data(db, args.dryrun)
                elif choice == "2":
                    days = input("请输入要清理的天数: ")
                    try:
                        days = int(days)
                        if days > 0:
                            cleanup_old_data(db, days, args.dryrun)
                        else:
                            print_warning("天数必须大于0")
                    except ValueError:
                        print_warning("无效输入，请输入一个整数")
                elif choice == "3":
                    cleanup_orphaned_data(db, args.dryrun)
                elif choice == "4":
                    find_missing_relations(db, args.dryrun)
                elif choice == "5":
                    find_sessions_without_parentid(db, args.dryrun)
                elif choice == "6":
                    find_duplicate_sessions(db, args.dryrun)
                elif choice == "7":
                    stats = get_db_stats(db)
                    print_info(f"当前数据统计: {stats['sessions']}个会话, {stats['branches']}个分支关系, {stats['messages']}条消息")
                elif choice == "0":
                    break
                else:
                    print_warning("无效选择，请重新输入")
                
                input("\n按Enter键继续...")
    
    finally:
        client.close()
        print_header("清理操作完成")

if __name__ == "__main__":
    main()