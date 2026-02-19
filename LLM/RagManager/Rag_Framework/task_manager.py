"""
任务管理模块 - 统一管理用户任务状态，提供线程安全的任务追踪
"""
import os
import json
import time
import threading
import logging
import hashlib
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict

logger = logging.getLogger("ColPali-RAG-Manager")

@dataclass
class TaskStatus:
    """任务状态数据类"""
    task_id: str
    user_id: str
    task_type: str
    status: str  # pending, processing, completed, failed
    progress: float = 0.0
    message: str = ""
    created_time: float = 0.0
    updated_time: float = 0.0
    result_data: Dict[str, Any] = None
    error_info: str = ""

    def __post_init__(self):
        if self.created_time == 0.0:
            self.created_time = time.time()
        if self.result_data is None:
            self.result_data = {}

class TaskManager:
    """线程安全的任务管理器"""
    
    def __init__(self, storage_path: str):
        self.storage_path = storage_path
        self._lock = threading.RLock()
        self._tasks: Dict[str, TaskStatus] = {}
        self._user_tasks: Dict[str, List[str]] = {}  # user_id -> [task_ids]
        
        # 确保存储目录存在
        os.makedirs(self.storage_path, exist_ok=True)
        os.makedirs(os.path.join(self.storage_path, "tasks"), exist_ok=True)
        
        # 加载持久化任务
        self._load_tasks()
    
    def _load_tasks(self):
        """加载持久化的任务"""
        with self._lock:
            try:
                # 加载任务文件
                tasks_dir = os.path.join(self.storage_path, "tasks")
                if os.path.exists(tasks_dir):
                    for task_file in os.listdir(tasks_dir):
                        if task_file.endswith(".json"):
                            task_id = task_file[:-5]
                            try:
                                with open(os.path.join(tasks_dir, task_file), 'r', encoding='utf-8') as f:
                                    task_data = json.load(f)
                                
                                # 创建TaskStatus对象
                                task_status = TaskStatus(**task_data)
                                self._tasks[task_id] = task_status
                                
                                # 更新用户任务映射
                                user_id = task_status.user_id
                                if user_id not in self._user_tasks:
                                    self._user_tasks[user_id] = []
                                if task_id not in self._user_tasks[user_id]:
                                    self._user_tasks[user_id].append(task_id)
                                    
                            except Exception as e:
                                logger.error(f"加载任务 {task_id} 失败: {str(e)}")
                
                logger.info(f"加载了 {len(self._tasks)} 个任务")
                
            except Exception as e:
                logger.error(f"加载任务失败: {str(e)}")
    
    def _save_task(self, task_id: str, task_status: TaskStatus):
        """保存单个任务到文件"""
        try:
            task_file = os.path.join(self.storage_path, "tasks", f"{task_id}.json")
            with open(task_file, 'w', encoding='utf-8') as f:
                json.dump(asdict(task_status), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存任务 {task_id} 失败: {str(e)}")
    
    def generate_task_id(self, user_id: str, task_type: str) -> str:
        """生成唯一的任务ID"""
        timestamp = int(time.time())
        hash_input = f"{user_id}_{task_type}_{timestamp}"
        task_hash = hashlib.md5(hash_input.encode()).hexdigest()[:8]
        return f"task_{timestamp}_{task_hash}"
    
    def create_task(self, user_id: str, task_type: str, message: str = "") -> str:
        """创建新任务"""
        with self._lock:
            task_id = self.generate_task_id(user_id, task_type)
            task_status = TaskStatus(
                task_id=task_id,
                user_id=user_id,
                task_type=task_type,
                status="pending",
                message=message,
                created_time=time.time(),
                updated_time=time.time()
            )
            
            self._tasks[task_id] = task_status
            
            # 更新用户任务映射
            if user_id not in self._user_tasks:
                self._user_tasks[user_id] = []
            self._user_tasks[user_id].append(task_id)
            
            # 持久化
            self._save_task(task_id, task_status)
            
            logger.info(f"创建任务 {task_id} for 用户 {user_id}: {task_type}")
            return task_id
    
    def update_task(self, task_id: str, **updates) -> bool:
        """更新任务状态"""
        with self._lock:
            if task_id not in self._tasks:
                logger.error(f"任务 {task_id} 不存在")
                return False
            
            task_status = self._tasks[task_id]
            
            # 更新字段
            for key, value in updates.items():
                if hasattr(task_status, key):
                    setattr(task_status, key, value)
            
            task_status.updated_time = time.time()
            
            # 持久化
            self._save_task(task_id, task_status)
            
            logger.debug(f"更新任务 {task_id}: {list(updates.keys())}")
            return True
    
    def get_task(self, task_id: str) -> Optional[TaskStatus]:
        """获取任务状态"""
        with self._lock:
            return self._tasks.get(task_id)
    
    def get_user_tasks(self, user_id: str, limit: int = 50) -> List[TaskStatus]:
        """获取用户的任务列表"""
        with self._lock:
            user_task_ids = self._user_tasks.get(user_id, [])
            
            # 按创建时间排序，最新的在前
            user_tasks = []
            for task_id in user_task_ids:
                if task_id in self._tasks:
                    user_tasks.append(self._tasks[task_id])
            
            user_tasks.sort(key=lambda x: x.created_time, reverse=True)
            return user_tasks[:limit]
    
    def delete_task(self, task_id: str) -> bool:
        """删除任务"""
        with self._lock:
            if task_id not in self._tasks:
                return False
            
            task_status = self._tasks[task_id]
            user_id = task_status.user_id
            
            # 从内存中删除
            del self._tasks[task_id]
            
            # 从用户任务映射中删除
            if user_id in self._user_tasks:
                if task_id in self._user_tasks[user_id]:
                    self._user_tasks[user_id].remove(task_id)
            
            # 删除文件
            try:
                task_file = os.path.join(self.storage_path, "tasks", f"{task_id}.json")
                if os.path.exists(task_file):
                    os.remove(task_file)
            except Exception as e:
                logger.error(f"删除任务文件失败 {task_id}: {str(e)}")
            
            logger.info(f"删除任务 {task_id}")
            return True
    
    def cleanup_old_tasks(self, max_age_hours: int = 24):
        """清理旧任务"""
        with self._lock:
            current_time = time.time()
            cutoff_time = current_time - (max_age_hours * 3600)
            
            tasks_to_delete = []
            for task_id, task_status in self._tasks.items():
                if task_status.created_time < cutoff_time:
                    # 只清理已完成或失败的任务
                    if task_status.status in ['completed', 'failed']:
                        tasks_to_delete.append(task_id)
            
            for task_id in tasks_to_delete:
                self.delete_task(task_id)
            
            if tasks_to_delete:
                logger.info(f"清理了 {len(tasks_to_delete)} 个旧任务")
    
    def get_task_statistics(self) -> Dict[str, Any]:
        """获取任务统计信息"""
        with self._lock:
            stats = {
                'total_tasks': len(self._tasks),
                'by_status': {},
                'by_user': {},
                'by_type': {}
            }
            
            for task_status in self._tasks.values():
                # 按状态统计
                status = task_status.status
                stats['by_status'][status] = stats['by_status'].get(status, 0) + 1
                
                # 按用户统计
                user_id = task_status.user_id
                stats['by_user'][user_id] = stats['by_user'].get(user_id, 0) + 1
                
                # 按类型统计
                task_type = task_status.task_type
                stats['by_type'][task_type] = stats['by_type'].get(task_type, 0) + 1
            
            return stats

# 全局任务管理器实例
_global_task_manager = None

def get_task_manager() -> TaskManager:
    """获取全局任务管理器实例"""
    global _global_task_manager
    if _global_task_manager is None:
        storage_path = "/data/yphu/Web-FabGPT/LLM/RagManager/user_tasks"
        _global_task_manager = TaskManager(storage_path)
    return _global_task_manager