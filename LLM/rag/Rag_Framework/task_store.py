from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional


logger = logging.getLogger("Text-RAG-Manager")


class TaskStore:
    def __init__(self, storage_path: str) -> None:
        self.storage_path = Path(storage_path)
        self.processing_tasks: Dict[str, Dict] = {}
        self.user_tasks: Dict[str, List[str]] = {}
        self.task_users: Dict[str, str] = {}
        self._lock = threading.RLock()

    def initialize(self) -> None:
        with self._lock:
            self.storage_path.mkdir(parents=True, exist_ok=True)
            (self.storage_path / "tasks").mkdir(parents=True, exist_ok=True)

    def load(self) -> None:
        with self._lock:
            self.initialize()
            existing_user_tasks = self._read_json(self.storage_path / "user_tasks.json", default={})
            existing_task_users = self._read_json(self.storage_path / "task_users.json", default={})

            task_dir = self.storage_path / "tasks"
            self.processing_tasks = {}
            for task_file in task_dir.glob("*.json"):
                try:
                    self.processing_tasks[task_file.stem] = json.loads(
                        task_file.read_text(encoding="utf-8")
                    )
                except json.JSONDecodeError:
                    logger.warning("跳过损坏的任务文件: %s", task_file)
            self.user_tasks = {}
            self.task_users = {}
            for task_id, task in self.processing_tasks.items():
                user_key = str(
                    task.get("user_id")
                    or existing_task_users.get(task_id)
                    or ""
                ).strip()
                if not user_key:
                    continue
                self.task_users[task_id] = user_key
                self.user_tasks.setdefault(user_key, [])
                if task_id not in self.user_tasks[user_key]:
                    self.user_tasks[user_key].append(task_id)

            for user_key, task_ids in existing_user_tasks.items():
                normalized_user = str(user_key)
                self.user_tasks.setdefault(normalized_user, [])
                for task_id in task_ids:
                    if task_id not in self.processing_tasks:
                        continue
                    self.task_users.setdefault(task_id, normalized_user)
                    if task_id not in self.user_tasks[normalized_user]:
                        self.user_tasks[normalized_user].append(task_id)

            self._save_task_mappings()

    def recover_incomplete_tasks(self, *, failure_message: str) -> None:
        with self._lock:
            now = time.time()
            for task_id, task in list(self.processing_tasks.items()):
                status = str(task.get("status", ""))
                if status not in {"processing", "queued"}:
                    continue
                task["status"] = "failed"
                task["progress"] = int(task.get("progress", 0) or 0)
                task["error"] = failure_message
                task["current_step"] = "recovery_required"
                task["keep_until"] = now + 300
                self._task_path(task_id).write_text(
                    json.dumps(task, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

    def associate(self, task_id: str, user_id: str) -> None:
        with self._lock:
            user_key = str(user_id)
            self.user_tasks.setdefault(user_key, [])
            if task_id not in self.user_tasks[user_key]:
                self.user_tasks[user_key].append(task_id)
            self.task_users[task_id] = user_key
            self._save_task_mappings()

    def upsert_task(self, task_id: str, payload: Dict) -> None:
        with self._lock:
            self.processing_tasks[task_id] = payload
            self._task_path(task_id).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def get_task(self, task_id: str) -> Optional[Dict]:
        with self._lock:
            task = self.processing_tasks.get(task_id)
            return dict(task) if task else None

    def list_user_tasks(self, user_id: str) -> List[Dict]:
        with self._lock:
            task_ids = list(self.user_tasks.get(str(user_id), []))
            tasks: List[Dict] = []
            expired_ids: List[str] = []
            for task_id in task_ids:
                task = self.processing_tasks.get(task_id)
                if not task:
                    continue
                keep_until = task.get("keep_until")
                if keep_until and time.time() > keep_until:
                    expired_ids.append(task_id)
                    continue
                task_copy = dict(task)
                task_copy.setdefault("task_id", task_id)
                tasks.append(task_copy)
            for expired_id in expired_ids:
                self.delete_task(expired_id)
            tasks.sort(key=lambda item: item.get("start_time", 0), reverse=True)
            return tasks

    def delete_task(self, task_id: str) -> None:
        with self._lock:
            self.processing_tasks.pop(task_id, None)
            task_path = self._task_path(task_id)
            if task_path.exists():
                task_path.unlink()
            user_id = self.task_users.pop(task_id, None)
            if user_id and user_id in self.user_tasks:
                self.user_tasks[user_id] = [
                    existing for existing in self.user_tasks[user_id] if existing != task_id
                ]
            self._save_task_mappings()

    def clear(self) -> None:
        with self._lock:
            for task_file in (self.storage_path / "tasks").glob("*.json"):
                task_file.unlink()
            self.processing_tasks = {}
            self.user_tasks = {}
            self.task_users = {}
            self._save_task_mappings()

    def find_task_by_filename(self, *, user_id: str, filename: str) -> Optional[Dict]:
        with self._lock:
            task_ids = self.user_tasks.get(str(user_id), [])
            candidates = []
            for task_id in task_ids:
                task = self.processing_tasks.get(task_id)
                if not task:
                    continue
                if task.get("file_name") == filename or task.get("original_name") == filename:
                    task_copy = dict(task)
                    task_copy.setdefault("task_id", task_id)
                    candidates.append(task_copy)
            if not candidates:
                return None
            candidates.sort(key=lambda item: item.get("start_time", 0), reverse=True)
            return candidates[0]

    def _save_task_mappings(self) -> None:
        (self.storage_path / "user_tasks.json").write_text(
            json.dumps(self.user_tasks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.storage_path / "task_users.json").write_text(
            json.dumps(self.task_users, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _task_path(self, task_id: str) -> Path:
        return self.storage_path / "tasks" / f"{task_id}.json"

    @staticmethod
    def _read_json(path: Path, *, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("读取 JSON 失败，回退默认值: %s", path)
            return default
