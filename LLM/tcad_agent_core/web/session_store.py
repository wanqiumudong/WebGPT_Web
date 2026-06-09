from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
import shutil
import threading
from typing import Any
import uuid

from mcp.service import TcadGatewayMCPService


@dataclass
class UploadedAsset:
    file_name: str
    stored_path: Path
    role: str
    bound: bool = False


@dataclass
class WebSessionRecord:
    user_id: str
    conversation_id: str
    workdir: Path
    instance_id: str | None = None
    uploads: dict[str, UploadedAsset] = field(default_factory=dict)
    active_request_ids: set[str] = field(default_factory=set)
    aborted_request_ids: set[str] = field(default_factory=set)
    execution_lock: threading.Lock = field(default_factory=threading.Lock)


class WebSessionStore:
    @staticmethod
    def _looks_like_sdevice(file_name: str) -> bool:
        lower = file_name.lower()
        return any(
            marker in lower
            for marker in (
                "sdevice",
                "_des.cmd",
                "_des.scm",
                "des.cmd",
                "des.scm",
            )
        ) or lower.endswith(".des")

    def __init__(
        self,
        *,
        workspace: Path,
        service: TcadGatewayMCPService,
        session_root: Path | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.service = service
        self.session_root = (session_root or (self.workspace / "runtime" / "web_sessions")).resolve()
        self.session_root.mkdir(parents=True, exist_ok=True)
        self._records: dict[tuple[str, str], WebSessionRecord] = {}
        self._request_map: dict[str, tuple[str, str]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def normalize_user_id(user_id: str | None, *, fallback: str = "anonymous") -> str:
        text = str(user_id or "").strip()
        return text or fallback

    @staticmethod
    def normalize_conversation_id(conversation_id: str | None) -> str:
        text = str(conversation_id or "").strip()
        return text or f"web-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def detect_role(file_name: str) -> str:
        lower = file_name.lower()
        if lower.endswith(".plt"):
            return "plot"
        if lower.endswith(".tdr"):
            return "mesh" if ("msh" in lower or "mesh" in lower) else "tdr"
        if WebSessionStore._looks_like_sdevice(file_name):
            return "sdevice_cmd"
        if lower.endswith(".cmd") or lower.endswith(".scm"):
            return "sdevice_cmd" if WebSessionStore._looks_like_sdevice(file_name) else "sde_cmd"
        return "input"

    def _key(self, user_id: str, conversation_id: str) -> tuple[str, str]:
        return (self.normalize_user_id(user_id), self.normalize_conversation_id(conversation_id))

    def get_or_create_record(self, user_id: str, conversation_id: str) -> WebSessionRecord:
        key = self._key(user_id, conversation_id)
        with self._lock:
            record = self._records.get(key)
            if record is not None:
                return record
            workdir = self.session_root / key[0] / key[1]
            (workdir / "pending_uploads").mkdir(parents=True, exist_ok=True)
            record = WebSessionRecord(user_id=key[0], conversation_id=key[1], workdir=workdir)
            self._records[key] = record
            return record

    def get_record(self, user_id: str, conversation_id: str) -> WebSessionRecord | None:
        key = self._key(user_id, conversation_id)
        with self._lock:
            return self._records.get(key)

    @staticmethod
    def _meta_file(record: WebSessionRecord) -> Path:
        return record.workdir / "web_session_meta.json"

    def load_meta(self, record: WebSessionRecord) -> dict[str, Any]:
        meta_path = self._meta_file(record)
        if not meta_path.exists():
            return {}
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def save_meta(self, record: WebSessionRecord, **fields: Any) -> dict[str, Any]:
        with self._lock:
            payload = self.load_meta(record)
            payload.update(
                {
                    "user_id": record.user_id,
                    "conversation_id": record.conversation_id,
                    **fields,
                }
            )
            meta_path = self._meta_file(record)
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return payload

    def ensure_instance(self, record: WebSessionRecord) -> WebSessionRecord:
        with self._lock:
            if record.instance_id:
                return record
            started = self.service.start_tcad_server(str(record.workdir))
            record.instance_id = started["instance_id"]
            return record

    def save_upload(self, *, user_id: str, conversation_id: str, file_storage: Any) -> dict[str, Any]:
        record = self.get_or_create_record(user_id, conversation_id)
        file_name = Path(getattr(file_storage, "filename", "") or "upload.bin").name
        stored_path = record.workdir / "pending_uploads" / file_name
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        file_storage.save(stored_path)
        asset = UploadedAsset(file_name=file_name, stored_path=stored_path, role=self.detect_role(file_name))
        with self._lock:
            record.uploads[file_name] = asset
        if record.instance_id is not None:
            self.bind_uploaded_assets(record)
        return {
            "file_name": file_name,
            "stored_path": str(stored_path),
            "role": asset.role,
        }

    def bind_uploaded_assets(self, record: WebSessionRecord) -> list[dict[str, Any]]:
        self.ensure_instance(record)
        bound_assets: list[dict[str, Any]] = []
        for asset in list(record.uploads.values()):
            if asset.bound:
                continue
            response = self.service.call(
                method="api_tcad_register_asset",
                params={
                    "source_path": str(asset.stored_path),
                    "file_name": asset.file_name,
                    "role": asset.role,
                },
                instance_id=record.instance_id,
            )
            if not response.get("ok"):
                raise RuntimeError(response.get("error", {}).get("message", "register asset failed"))
            asset.bound = True
            bound_assets.append(response["data"]["asset"])
        return bound_assets

    def list_known_assets(self, record: WebSessionRecord) -> list[dict[str, Any]]:
        pending = [
            {
                "file_name": asset.file_name,
                "role": asset.role,
                "stored_path": str(asset.stored_path),
                "bound": asset.bound,
            }
            for asset in record.uploads.values()
            if not asset.bound
        ]
        if not record.instance_id:
            return pending
        response = self.service.call(method="api_tcad_list_assets", instance_id=record.instance_id)
        if not response.get("ok"):
            return pending
        active_assets = response["data"].get("assets", [])
        return [*active_assets, *pending]

    def compose_instruction(self, record: WebSessionRecord, user_message: str) -> str:
        assets = self.list_known_assets(record)
        if not assets:
            return user_message
        lines = ["当前会话已上传文件："]
        for item in assets:
            file_name = item.get("file_name", "")
            role = item.get("role", "input")
            stored_path = str(item.get("stored_path", "")).strip()
            if stored_path:
                lines.append(f"- {file_name} ({role}): {stored_path}")
            else:
                lines.append(f"- {file_name} ({role})")
        lines.extend(["", "请结合这些文件处理以下请求：", user_message])
        return "\n".join(lines)

    def delete_asset(self, *, user_id: str, conversation_id: str, file_name: str) -> dict[str, Any]:
        record = self.get_or_create_record(user_id, conversation_id)
        resolved_name = Path(file_name).name
        removed = False
        asset = record.uploads.pop(resolved_name, None)
        if asset is not None:
            asset.stored_path.unlink(missing_ok=True)
            removed = True
        if record.instance_id:
            response = self.service.call(
                method="api_tcad_delete_asset",
                params={"file_name": resolved_name},
                instance_id=record.instance_id,
            )
            if response.get("ok"):
                removed = bool(response["data"].get("deleted")) or removed
        return {"deleted": removed, "file_name": resolved_name}

    def clear_file_context(self, *, user_id: str, conversation_id: str, file_name: str = "") -> dict[str, Any]:
        if not file_name:
            return {"cleared": True, "file_name": ""}
        result = self.delete_asset(user_id=user_id, conversation_id=conversation_id, file_name=file_name)
        return {"cleared": result["deleted"], "file_name": file_name}

    def delete_session(self, *, user_id: str, conversation_id: str) -> dict[str, Any]:
        key = self._key(user_id, conversation_id)
        session_path = self.session_root / key[0] / key[1]
        with self._lock:
            record = self._records.pop(key, None)
            request_ids = set()
            if record is not None:
                request_ids.update(record.active_request_ids)
                request_ids.update(record.aborted_request_ids)
            for request_id in request_ids:
                self._request_map.pop(request_id, None)
        stopped_instance = False
        if record is not None and record.instance_id:
            stopped = self.service.stop_server(instance_id=record.instance_id)
            stopped_instance = bool(stopped.get("ok"))
        existed_before = session_path.exists()
        shutil.rmtree(session_path, ignore_errors=True)
        return {
            "deleted": existed_before or record is not None,
            "workdir_deleted": not session_path.exists(),
            "stopped_instance": stopped_instance,
            "conversation_id": key[1],
            "user_id": key[0],
        }

    def begin_request(self, record: WebSessionRecord, request_id: str) -> None:
        with self._lock:
            record.active_request_ids.add(request_id)
            record.aborted_request_ids.discard(request_id)
            self._request_map[request_id] = (record.user_id, record.conversation_id)

    def finish_request(self, request_id: str) -> None:
        with self._lock:
            key = self._request_map.pop(request_id, None)
            if key is None:
                return
            record = self._records.get(key)
            if record is None:
                return
            record.active_request_ids.discard(request_id)
            record.aborted_request_ids.discard(request_id)

    def abort_request(self, request_id: str) -> bool:
        with self._lock:
            key = self._request_map.get(request_id)
            if key is None:
                return False
            record = self._records.get(key)
            if record is None:
                return False
            if request_id not in record.active_request_ids:
                return False
            record.aborted_request_ids.add(request_id)
            return True

    def should_abort(self, request_id: str) -> bool:
        with self._lock:
            key = self._request_map.get(request_id)
            if key is None:
                return False
            record = self._records.get(key)
            return bool(record and request_id in record.aborted_request_ids)
