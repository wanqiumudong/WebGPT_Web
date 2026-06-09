from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


REGISTRY_VERSION = "tcad-tool-registry-v1"
MANIFEST_VERSION = "tcad-run-manifest-v1"
ERROR_ENVELOPE_VERSION = "tcad-error-envelope-v1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_type_from_annotation(annotation: Any) -> str:
    if annotation in {int}:
        return "integer"
    if annotation in {float}:
        return "number"
    if annotation in {bool}:
        return "boolean"
    if annotation in {list, tuple, set}:
        return "array"
    if annotation in {dict, Mapping}:
        return "object"
    return "string"


def _jsonable_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, dict):
        return value
    return str(value)


def _build_input_schema(func: Callable[..., Any]) -> dict[str, Any]:
    signature = inspect.signature(func)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, parameter in signature.parameters.items():
        if name == "self":
            continue
        if parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
            continue
        schema: dict[str, Any] = {"type": _json_type_from_annotation(parameter.annotation)}
        if parameter.default is not inspect.Parameter.empty:
            schema["default"] = _jsonable_default(parameter.default)
        else:
            required.append(name)
        properties[name] = schema
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _default_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "stage": {"type": "string"},
            "success": {"type": "boolean"},
            "artifacts": {"type": "object"},
            "details": {"type": "object"},
        },
        "additionalProperties": True,
    }


def _guess_artifact_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".cmd", ".tcl", ".py", ".json", ".txt", ".log", ".csv"}:
        return "text"
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return "image"
    if suffix in {".tdr", ".plt", ".dat", ".iv", ".plx"}:
        return "simulation"
    if suffix in {".pdf"}:
        return "document"
    if suffix in {".zip", ".tar", ".gz"}:
        return "archive"
    return "file"


def _looks_like_artifact_path(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    if text.startswith(("/", "./", "../", "~")):
        return True
    path = Path(text)
    if path.suffix:
        return True
    return path.exists()


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    enabled: bool
    category: str
    summary: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    danger_level: str = "normal"
    async_capable: bool = True
    artifact_effects: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "category": self.category,
            "summary": self.summary,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "danger_level": self.danger_level,
            "async_capable": self.async_capable,
            "artifact_effects": list(self.artifact_effects),
        }


@dataclass(frozen=True)
class ToolRegistry:
    version: str
    tools: tuple[ToolDescriptor, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "tool_count": len(self.tools),
            "tools": [item.to_dict() for item in self.tools],
        }


@dataclass(frozen=True)
class TaskContract:
    summary: str = ""
    done_criteria: tuple[str, ...] = ()
    todos: tuple[dict[str, str], ...] = ()
    plan_steps: tuple[dict[str, Any], ...] = ()
    current_step: str = ""
    blocker: str = ""
    next_step_hint: str = ""
    plan_id: str = ""
    plan_attempt: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "done_criteria": list(self.done_criteria),
            "todos": [dict(item) for item in self.todos],
            "plan_steps": [dict(item) for item in self.plan_steps],
            "current_step": self.current_step,
            "blocker": self.blocker,
            "next_step_hint": self.next_step_hint,
            "plan_id": self.plan_id,
            "plan_attempt": self.plan_attempt,
        }


@dataclass(frozen=True)
class ArtifactRecord:
    role: str
    path: str
    relative_path: str | None
    exists: bool
    kind: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "path": self.path,
            "relative_path": self.relative_path,
            "exists": self.exists,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    stage: str
    session_dir: str
    device_type: str
    simulation_type: str
    target_artifact: str
    requirement_preview: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "stage": self.stage,
            "session_dir": self.session_dir,
            "device_type": self.device_type,
            "simulation_type": self.simulation_type,
            "target_artifact": self.target_artifact,
            "requirement_preview": self.requirement_preview,
        }


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    instance_id: str | None
    method: str
    created_at: str
    status: str
    done: bool
    response_ok: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "job_id": self.job_id,
            "instance_id": self.instance_id,
            "method": self.method,
            "created_at": self.created_at,
            "status": self.status,
            "done": self.done,
        }
        if self.response_ok is not None:
            data["response_ok"] = self.response_ok
        return data


@dataclass(frozen=True)
class RunManifest:
    version: str
    generated_at: str
    runtime_root: str
    session: SessionRecord
    task: TaskContract
    artifacts: tuple[ArtifactRecord, ...]
    metrics: dict[str, Any]
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "runtime_root": self.runtime_root,
            "session": self.session.to_dict(),
            "task": self.task.to_dict(),
            "artifacts": [item.to_dict() for item in self.artifacts],
            "metrics": self.metrics,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class ErrorEnvelope:
    schema_version: str
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    retryable: bool | None = None
    failure_class: str | None = None
    generated_at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "schema_version": self.schema_version,
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "generated_at": self.generated_at,
        }
        if self.retryable is not None:
            data["retryable"] = self.retryable
        if self.failure_class is not None:
            data["failure_class"] = self.failure_class
        return data


def build_tool_registry(
    *,
    metadata: Mapping[str, Mapping[str, Any]],
    dispatch: Mapping[str, Callable[..., Any]],
    denied_tools: set[str] | None = None,
) -> ToolRegistry:
    denied = denied_tools or set()
    descriptors: list[ToolDescriptor] = []
    for name in sorted(dispatch.keys()):
        meta = dict(metadata.get(name, {}))
        descriptors.append(
            ToolDescriptor(
                name=name,
                enabled=name not in denied,
                category=str(meta.get("category", "uncategorized")),
                summary=str(meta.get("summary", "")),
                input_schema=dict(meta.get("input_schema") or _build_input_schema(dispatch[name])),
                output_schema=dict(meta.get("output_schema") or _default_output_schema()),
                danger_level=str(meta.get("danger_level", "normal")),
                async_capable=bool(meta.get("async_capable", True)),
                artifact_effects=tuple(str(item) for item in meta.get("artifact_effects", ())),
            )
        )
    return ToolRegistry(version=REGISTRY_VERSION, tools=tuple(descriptors))


def build_error_envelope(
    *,
    code: str,
    message: str,
    details: Mapping[str, Any] | None = None,
    retryable: bool | None = None,
    failure_class: str | None = None,
) -> dict[str, Any]:
    envelope = ErrorEnvelope(
        schema_version=ERROR_ENVELOPE_VERSION,
        code=code,
        message=message,
        details=dict(details or {}),
        retryable=retryable,
        failure_class=failure_class,
    )
    return envelope.to_dict()


def build_job_record(
    *,
    job_id: str,
    instance_id: str | None,
    method: str,
    created_at: float,
    done: bool,
    running: bool = False,
    response: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    status = "completed" if done else "running" if running else "submitted"
    response_ok = None
    if response is not None and "ok" in response:
        response_ok = bool(response.get("ok"))
    record = JobRecord(
        job_id=job_id,
        instance_id=instance_id,
        method=method,
        created_at=datetime.fromtimestamp(created_at, tz=timezone.utc).isoformat(),
        status=status,
        done=done,
        response_ok=response_ok,
    )
    return record.to_dict()


def _build_task_contract_from_state(state: Any) -> TaskContract:
    raw_todos = getattr(state, "todos", []) or []
    todos: list[dict[str, str]] = []
    for item in raw_todos:
        if isinstance(item, dict):
            content = str(item.get("content", ""))
            status = str(item.get("status", "pending"))
        else:
            content = str(getattr(item, "content", ""))
            status = str(getattr(item, "status", "pending"))
        if content:
            todos.append({"content": content, "status": status})
    raw_plan_steps = getattr(state, "plan_steps", []) or []
    plan_steps: list[dict[str, Any]] = []
    for item in raw_plan_steps:
        if isinstance(item, dict):
            payload = dict(item)
        else:
            payload = {
                "step_id": str(getattr(item, "step_id", "") or ""),
                "title": str(getattr(item, "title", "") or ""),
                "tool_name": str(getattr(item, "tool_name", "") or ""),
                "status": str(getattr(item, "status", "pending") or "pending"),
                "attempt": int(getattr(item, "attempt", 1) or 1),
            }
        if str(payload.get("step_id", "")).strip():
            plan_steps.append(payload)
    return TaskContract(
        summary=str(getattr(state, "task_summary", "") or ""),
        done_criteria=tuple(str(x) for x in (getattr(state, "done_criteria", []) or [])),
        todos=tuple(todos),
        plan_steps=tuple(plan_steps),
        current_step=str(getattr(state, "current_step", "") or ""),
        blocker=str(getattr(state, "blocker", "") or ""),
        next_step_hint=str(getattr(state, "next_step_hint", "") or ""),
        plan_id=str(getattr(state, "plan_id", "") or ""),
        plan_attempt=int(getattr(state, "plan_attempt", 0) or 0),
    )


def _build_session_record(state: Any) -> SessionRecord:
    spec = getattr(state, "spec", None)
    requirement = str(getattr(spec, "requirement", "") or "")
    return SessionRecord(
        session_id=str(getattr(state, "session_id", "")),
        stage=str(getattr(state, "stage", "")),
        session_dir=str(getattr(state, "session_dir", "")),
        device_type=str(getattr(spec, "device_type", "unspecified") or "unspecified"),
        simulation_type=str(getattr(spec, "simulation_type", "unspecified") or "unspecified"),
        target_artifact=str(getattr(spec, "target_artifact", "unspecified") or "unspecified"),
        requirement_preview=requirement[:240],
    )


def _build_artifact_records(runtime_root: Path, artifacts: Mapping[str, Any]) -> tuple[ArtifactRecord, ...]:
    records: list[ArtifactRecord] = []
    for role in sorted(artifacts.keys()):
        value = artifacts.get(role)
        if not isinstance(value, str) or not _looks_like_artifact_path(value):
            continue
        path = Path(value).expanduser()
        resolved = path.resolve(strict=False)
        relative_path: str | None = None
        try:
            relative_path = str(resolved.relative_to(runtime_root.resolve()))
        except ValueError:
            relative_path = None
        records.append(
            ArtifactRecord(
                role=str(role),
                path=str(resolved),
                relative_path=relative_path,
                exists=resolved.exists(),
                kind=_guess_artifact_kind(resolved),
            )
        )
    return tuple(records)


def emit_run_manifest(runtime_root: Path, state: Any) -> Path:
    runtime_root = runtime_root.resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    manifest = RunManifest(
        version=MANIFEST_VERSION,
        generated_at=_utc_now_iso(),
        runtime_root=str(runtime_root),
        session=_build_session_record(state),
        task=_build_task_contract_from_state(state),
        artifacts=_build_artifact_records(runtime_root, getattr(state, "artifacts", {}) or {}),
        metrics=dict(getattr(state, "metrics", {}) or {}),
        notes=tuple(str(item) for item in (getattr(state, "notes", []) or [])),
    )
    manifest_path = runtime_root / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path
