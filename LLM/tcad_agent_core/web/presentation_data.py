from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any


DEFAULT_EXTERNAL_DELIVERABLES_ROOT = Path("/data/yphu/TCAD_Agent/code/deliverables")
TEXT_PREVIEW_SUFFIXES = {
    ".cmd",
    ".scm",
    ".txt",
    ".log",
    ".json",
    ".jsonl",
    ".csv",
    ".plt",
    ".md",
    ".tcl",
    ".va",
}
IMAGE_PREVIEW_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
}
STAGE_LABELS = {
    "created": "已创建",
    "structure_generated": "前序结构已生成",
    "structure_checked": "前序结构已检查",
    "sde_generated": "SDE 已生成",
    "sde_checked": "SDE 已检查",
    "sde_done": "SDE 已运行",
    "sde_generation_failed": "SDE 生成失败",
    "sde_check_failed": "SDE 语法检查失败",
    "sde_failed": "SDE 执行失败",
    "svisual_sde_done": "结构图已导出",
    "tdr_inspected": "TDR 已检查",
    "sdevice_generated": "SDevice 已生成",
    "sdevice_checked": "SDevice 已检查",
    "sdevice_done": "SDevice 已运行",
    "svisual_done": "仿真图已导出",
    "validated": "验证通过",
    "validation_failed": "验证失败",
    "greeting": "问候回复",
}
DEVICE_LABELS = {
    "nmos": "NMOS",
    "mosfet": "MOSFET",
    "moscap": "MOSCAP",
    "diode": "Diode",
    "finfet": "FinFET",
    "gaa": "GAA",
    "ldmos": "LDMOS",
    "bjt": "BJT",
    "igbt": "IGBT",
    "unspecified": "TCAD",
}
WORKSPACE_GROUP_LABELS = {
    "inputs": "输入文件",
    "outputs": "核心产物",
    "reports": "预览与报告",
    "logs": "日志",
}
WORKSPACE_ROLE_LABELS = {
    "input": "输入文件",
    "plot": "结果曲线",
    "mesh": "网格输入",
    "tdr": "TDR 输入",
    "process_cmd": "前序结构脚本",
    "sde_cmd": "SDE 输入脚本",
    "sdevice_cmd": "SDevice 输入脚本",
}
WORKSPACE_ARTIFACT_LABELS = {
    "process_cmd": "工艺脚本",
    "sde_cmd": "SDE 脚本",
    "sdevice_cmd": "SDevice 脚本",
    "mesh": "网格文件",
    "bnd": "边界文件",
    "plot": "仿真结果",
    "tdr": "TDR 数据",
    "tdr_info_report": "TDR 信息报告",
    "validation_report": "验证报告",
    "svisual_png": "结构图片",
    "svisual_sde_png": "结构图片",
    "svisual_doping_png": "掺杂分布图",
    "svisual_curve_txt": "曲线数据",
    "plot_transfer": "Id-Vg 曲线",
    "plot_output": "Id-Vd 曲线",
    "plot_breakdown": "BV 曲线",
    "plot_cv": "C-V 曲线",
    "plot_shift": "退化曲线",
    "compact_model_plot": "拟合对比图",
    "compact_model_card": "参数卡",
    "compact_model_report": "参数提取摘要",
    "verilog_a_model": "Verilog-A 模型",
}
WORKSPACE_ARTIFACT_GROUPS = {
    "process_cmd": "outputs",
    "sde_cmd": "outputs",
    "sdevice_cmd": "outputs",
    "mesh": "outputs",
    "bnd": "outputs",
    "plot": "outputs",
    "tdr": "outputs",
    "tdr_info_report": "reports",
    "validation_report": "reports",
    "svisual_png": "reports",
    "svisual_sde_png": "reports",
    "svisual_doping_png": "reports",
    "svisual_curve_txt": "reports",
    "plot_transfer": "reports",
    "plot_output": "reports",
    "plot_breakdown": "reports",
    "plot_cv": "reports",
    "plot_shift": "reports",
    "compact_model_plot": "reports",
    "compact_model_card": "reports",
    "compact_model_report": "reports",
    "verilog_a_model": "outputs",
}
VALIDATION_STATUS_LABELS = {
    "passed": "通过",
    "failed": "失败",
    "unknown": "未完成",
}
INTERNAL_REPORT_NAMES = {
    "reference_candidates.json",
    "reference_brief.json",
    "generate_sde_reference_context.txt",
    "generate_sdevice_reference_context.txt",
    "sde_coverage_audit.json",
    "sdevice_coverage_audit.json",
}
INTERNAL_WORKSPACE_NAMES = INTERNAL_REPORT_NAMES | {
    "debug_trace.jsonl",
    "main_agent_sde_brief.txt",
    "main_agent_sdevice_brief.txt",
}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _clip(text: str, limit: int = 120) -> str:
    normalized = " ".join((text or "").strip().split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1]}…"


def _device_label(device_type: str) -> str:
    key = (device_type or "unspecified").strip().lower()
    return DEVICE_LABELS.get(key, key.upper() or "TCAD")


def resolve_deliverables_root(workspace: Path) -> Path | None:
    env_path = os.environ.get("TCAD_PRESENTATION_DELIVERABLES_ROOT", "").strip()
    candidates = []
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.extend(
        [
            workspace / "deliverables",
            DEFAULT_EXTERNAL_DELIVERABLES_ROOT,
        ]
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists() and resolved.is_dir():
            return resolved
    return None


def load_demo_cases(workspace: Path, *, limit: int = 8) -> dict[str, Any]:
    root = resolve_deliverables_root(workspace)
    if root is None:
        return {"source": None, "cases": []}

    manifest = _read_json(root / "catalogs" / "active_case_manifest_v1.json") or {}
    prompt_pack = _read_json(root / "catalogs" / "active_case_prompt_pack_v1.json") or {}
    pack_cases = {
        str(item.get("case_id", "")): item
        for item in prompt_pack.get("cases", [])
        if isinstance(item, dict)
    }

    ordered_cases: list[dict[str, Any]] = []
    raw_cases = [item for item in manifest.get("cases", []) if isinstance(item, dict)]
    raw_cases.sort(
        key=lambda item: (
            0 if str(item.get("source_group", "")) == "current_main_case" else 1,
            str(item.get("case_id", "")),
        )
    )
    for item in raw_cases[: max(1, limit)]:
        case_id = str(item.get("case_id", "")).strip()
        pack_item = pack_cases.get(case_id, {})
        device_type = str(pack_item.get("device_type") or item.get("device_type") or "unspecified")
        simulation_type = str(pack_item.get("simulation_type") or item.get("simulation_type") or "structure")
        profile = str(pack_item.get("profile") or "").strip()
        prompt = str(pack_item.get("full_prompt") or item.get("prompt") or "").strip()
        label_prefix = "主案例" if str(item.get("source_group", "")) == "current_main_case" else "示例任务"
        title = f"{label_prefix} · {_device_label(device_type)}"
        if simulation_type:
            title = f"{title} · {simulation_type}"
        ordered_cases.append(
            {
                "case_id": case_id,
                "title": title,
                "summary": _clip(prompt, 160),
                "prompt": prompt,
                "device_type": device_type,
                "simulation_type": simulation_type,
                "profile": profile,
                "stage": str(item.get("stage") or ""),
                "artifact_files": list(item.get("artifact_files") or [])[:6],
                "reference_basis": list(pack_item.get("reference_basis") or [])[:3],
                "is_featured": str(item.get("source_group", "")) == "current_main_case",
            }
        )
    return {"source": str(root), "cases": ordered_cases}


def load_state_payload(workdir: Path) -> dict[str, Any]:
    state_file = workdir / "state.json"
    return _read_json(state_file) or {}


def _load_web_session_meta(workdir: Path) -> dict[str, Any]:
    return _read_json(workdir / "web_session_meta.json") or {}


def _load_reference_candidates(workdir: Path, state: dict[str, Any]) -> dict[str, Any]:
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    raw_path = str(artifacts.get("reference_candidates") or "").strip()
    candidate_path = None
    if raw_path:
        candidate_path = _ensure_within_workdir(workdir, raw_path)
    if candidate_path is None:
        fallback = workdir / "reports" / "reference_candidates.json"
        candidate_path = fallback if fallback.exists() else None
    if candidate_path is None:
        return {}
    return _read_json(candidate_path) or {}


def build_session_summary(
    *,
    record: Any,
    state: dict[str, Any],
    public_artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    spec = state.get("spec") if isinstance(state.get("spec"), dict) else {}
    requirement = str(spec.get("requirement") or "").strip()
    notes = [str(item) for item in (state.get("notes") or []) if str(item).strip()]
    uploads = []
    for asset in getattr(record, "uploads", {}).values():
        uploads.append(
            {
                "file_name": asset.file_name,
                "role": asset.role,
                "bound": asset.bound,
            }
        )

    stage = str(state.get("stage") or "created")
    metrics = state.get("metrics") if isinstance(state.get("metrics"), dict) else {}
    reference_candidates = _load_reference_candidates(getattr(record, "workdir"), state)
    meta = _load_web_session_meta(getattr(record, "workdir"))
    validation = build_validation_summary(workdir=getattr(record, "workdir"), state=state, artifacts=state.get("artifacts") or {})
    return {
        "conversation_id": getattr(record, "conversation_id", ""),
        "user_id": getattr(record, "user_id", ""),
        "stage": stage,
        "stage_label": STAGE_LABELS.get(stage, stage),
        "requirement": requirement,
        "requirement_short": _clip(requirement, 180),
        "latest_note": notes[-1] if notes else "",
        "notes_tail": notes[-4:],
        "artifact_count": len(public_artifacts),
        "artifacts": public_artifacts,
        "uploads": uploads,
        "metrics": metrics,
        "has_runtime": bool(state),
        "selected_sde_references": list(reference_candidates.get("selected_sde_references") or []),
        "selected_sdevice_references": list(reference_candidates.get("selected_sdevice_references") or []),
        "selected_function_references": list(reference_candidates.get("selected_function_references") or []),
        "reference_summary_note": str(reference_candidates.get("summary_note") or "").strip(),
        "task_summary": str(state.get("task_summary") or "").strip(),
        "done_criteria": [str(item) for item in (state.get("done_criteria") or []) if str(item).strip()],
        "todos": list(state.get("todos") or []),
        "plan_steps": list(state.get("plan_steps") or []),
        "current_step": str(state.get("current_step") or "").strip(),
        "blocker": str(state.get("blocker") or "").strip(),
        "next_step_hint": str(state.get("next_step_hint") or "").strip(),
        "plan_id": str(state.get("plan_id") or "").strip(),
        "plan_attempt": int(state.get("plan_attempt") or 0),
        "reference_stats": {
            "sde": len(reference_candidates.get("selected_sde_references") or []),
            "sdevice": len(reference_candidates.get("selected_sdevice_references") or []),
            "function": len(reference_candidates.get("selected_function_references") or []),
        },
        "demo_case": state.get("demo_case") if isinstance(state.get("demo_case"), dict) else {},
        "demo_panels": list(state.get("demo_panels") or []),
        "artifact_counts": {
            "total": len(public_artifacts),
            "images": sum(1 for item in public_artifacts if item.get("is_image")),
        },
        "validation_status": validation.get("status", "unknown"),
        "validation_status_label": VALIDATION_STATUS_LABELS.get(validation.get("status", "unknown"), validation.get("status", "unknown")),
        "tool_sequence": _load_tool_sequence(getattr(record, "workdir")),
        "last_user_message": str(meta.get("last_user_message") or "").strip(),
        "updated_at": str(meta.get("updated_at") or ""),
    }


def _load_tool_sequence(workdir: Path) -> list[str]:
    trace_file = workdir / "logs" / "debug_trace.jsonl"
    if not trace_file.exists():
        return []
    sequence: list[str] = []
    seen: set[tuple[str, str]] = set()
    try:
        lines = trace_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(payload.get("action") or "") != "mcp_tool_done":
            continue
        tool = str((payload.get("payload") or {}).get("tool") or "").strip()
        if not tool:
            continue
        stage = str((payload.get("payload") or {}).get("stage") or "")
        marker = (tool, stage)
        if marker in seen:
            continue
        seen.add(marker)
        sequence.append(tool)
    return sequence


def _ensure_within_workdir(workdir: Path, artifact_path: str) -> Path | None:
    try:
        resolved = Path(str(artifact_path)).expanduser().resolve()
    except OSError:
        return None
    try:
        resolved.relative_to(workdir.resolve())
    except ValueError:
        return None
    return resolved if resolved.exists() else None


def _workspace_item(
    *,
    workdir: Path,
    path: Path,
    group: str,
    label: str,
    kind: str,
) -> dict[str, Any] | None:
    resolved = _ensure_within_workdir(workdir, str(path))
    if resolved is None:
        return None
    suffix = resolved.suffix.lower()
    relative_path = resolved.relative_to(workdir).as_posix()
    return {
        "key": relative_path,
        "label": label,
        "file_name": resolved.name,
        "relative_path": relative_path,
        "group": group,
        "kind": kind,
        "is_image": suffix in IMAGE_PREVIEW_SUFFIXES,
        "previewable": suffix in TEXT_PREVIEW_SUFFIXES,
        "file_type": suffix.lstrip(".") or "file",
    }


def _asset_value(asset: Any, field: str) -> Any:
    if isinstance(asset, dict):
        return asset.get(field)
    return getattr(asset, field, None)


def _should_hide_workspace_file(path: Path) -> bool:
    file_name = path.name
    if file_name in INTERNAL_WORKSPACE_NAMES:
        return True
    if file_name.endswith("_msh.cmd"):
        return True
    return False


def build_workspace_manifest(
    *,
    workdir: Path,
    state: dict[str, Any],
    artifacts: dict[str, Any],
    uploads: list[Any],
) -> dict[str, Any]:
    grouped_items: dict[str, list[dict[str, Any]]] = {key: [] for key in WORKSPACE_GROUP_LABELS}
    seen_relative_paths: set[str] = set()

    def _add_item(item: dict[str, Any] | None) -> None:
        if item is None:
            return
        relative_path = str(item.get("relative_path") or "")
        if not relative_path or relative_path in seen_relative_paths:
            return
        seen_relative_paths.add(relative_path)
        grouped_items[item["group"]].append(item)

    for asset in uploads:
        stored_path = _asset_value(asset, "stored_path")
        role = str(_asset_value(asset, "role") or "input")
        label = WORKSPACE_ROLE_LABELS.get(role, role or "输入文件")
        _add_item(
            _workspace_item(
                workdir=workdir,
                path=Path(str(stored_path)),
                group="inputs",
                label=label,
                kind=role,
            )
        )

    ordered_artifact_keys = [
        "sde_cmd",
        "sdevice_cmd",
        "mesh",
        "bnd",
        "plot",
        "plot_transfer",
        "plot_output",
        "plot_breakdown",
        "plot_cv",
        "tdr",
        "tdr_info_report",
        "validation_report",
        "svisual_png",
        "svisual_sde_png",
        "svisual_doping_png",
        "svisual_curve_txt",
        "compact_model_plot",
        "compact_model_card",
        "compact_model_report",
        "verilog_a_model",
    ]
    for artifact_key in ordered_artifact_keys:
        raw_path = str(artifacts.get(artifact_key) or "").strip()
        if not raw_path:
            continue
        group = WORKSPACE_ARTIFACT_GROUPS.get(artifact_key, "reports")
        label = WORKSPACE_ARTIFACT_LABELS.get(artifact_key, artifact_key)
        _add_item(
            _workspace_item(
                workdir=workdir,
                path=Path(raw_path),
                group=group,
                label=label,
                kind=artifact_key,
            )
        )

    for report_file in sorted((workdir / "reports").glob("*")):
        if not report_file.is_file():
            continue
        if _should_hide_workspace_file(report_file):
            continue
        target_group = "logs" if report_file.name in INTERNAL_REPORT_NAMES else "reports"
        _add_item(
            _workspace_item(
                workdir=workdir,
                path=report_file,
                group=target_group,
                label=report_file.name,
                kind="log" if target_group == "logs" else "report",
            )
        )

    for run_file in sorted((workdir / "run").glob("*")):
        if not run_file.is_file():
            continue
        if _should_hide_workspace_file(run_file):
            continue
        is_internal_sidecar = run_file.name.endswith("_msh.cmd")
        group = "logs" if run_file.suffix.lower() in {".log", ".tcl"} or is_internal_sidecar else "outputs"
        kind = "log" if group == "logs" else "run_output"
        _add_item(
            _workspace_item(
                workdir=workdir,
                path=run_file,
                group=group,
                label=run_file.name,
                kind=kind,
            )
        )

    for log_file in sorted((workdir / "logs").glob("*")):
        if not log_file.is_file():
            continue
        if _should_hide_workspace_file(log_file):
            continue
        _add_item(
            _workspace_item(
                workdir=workdir,
                path=log_file,
                group="logs",
                label=log_file.name,
                kind="log",
            )
        )

    stage = str(state.get("stage") or "created")
    notes = [str(item) for item in (state.get("notes") or []) if str(item).strip()]
    groups = []
    total_files = 0
    primary_file_count = 0
    log_file_count = 0
    for group_key, title in WORKSPACE_GROUP_LABELS.items():
        items = grouped_items[group_key]
        total_files += len(items)
        if group_key == "logs":
            log_file_count += len(items)
        else:
            primary_file_count += len(items)
        groups.append(
            {
                "key": group_key,
                "label": title,
                "count": len(items),
                "items": items,
            }
        )

    return {
        "stage": stage,
        "stage_label": STAGE_LABELS.get(stage, stage),
        "latest_note": notes[-1] if notes else "",
        "groups": groups,
        "total_files": total_files,
        "primary_file_count": primary_file_count,
        "log_file_count": log_file_count,
    }


def build_artifact_preview(
    *,
    workdir: Path,
    artifact_key: str,
    artifact_path: str,
    max_lines: int = 80,
) -> dict[str, Any] | None:
    resolved = _ensure_within_workdir(workdir, artifact_path)
    if resolved is None:
        return None
    suffix = resolved.suffix.lower()
    if suffix not in TEXT_PREVIEW_SUFFIXES:
        return {
            "artifact_key": artifact_key,
            "file_name": resolved.name,
            "previewable": False,
            "file_type": suffix.lstrip(".") or "file",
        }

    try:
        if suffix == ".json":
            payload = json.loads(resolved.read_text(encoding="utf-8"))
            content = json.dumps(payload, ensure_ascii=False, indent=2)
        else:
            content = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    lines = content.splitlines()
    preview_lines = lines[: max(1, max_lines)]
    return {
        "artifact_key": artifact_key,
        "file_name": resolved.name,
        "previewable": True,
        "content": "\n".join(preview_lines),
        "truncated": len(lines) > len(preview_lines),
        "line_count": len(lines),
        "file_type": suffix.lstrip(".") or "file",
    }


def _extract_highlights(text: str, *, limit: int = 6) -> list[str]:
    highlights: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("-") or line[:2].isdigit() or line.startswith("•"):
            cleaned = line.lstrip("-• ").strip()
            if cleaned:
                highlights.append(cleaned)
        if len(highlights) >= limit:
            break
    return highlights


def build_brief_summary(
    *,
    workdir: Path,
    artifacts: dict[str, Any],
) -> dict[str, Any]:
    brief_items = [
        ("sde", "SDE 简报", str(artifacts.get("main_agent_sde_brief") or "")),
        ("sdevice", "SDevice 简报", str(artifacts.get("main_agent_sdevice_brief") or "")),
    ]
    briefs: list[dict[str, Any]] = []
    for kind, label, path in brief_items:
        if not path:
            continue
        resolved = _ensure_within_workdir(workdir, path)
        if resolved is None:
            continue
        try:
            text = resolved.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        briefs.append(
            {
                "kind": kind,
                "label": label,
                "file_name": resolved.name,
                "summary": _clip(text, 220),
                "highlights": _extract_highlights(text),
                "content": text,
            }
        )
    return {"briefs": briefs}


def build_validation_summary(
    *,
    workdir: Path,
    state: dict[str, Any],
    artifacts: dict[str, Any],
) -> dict[str, Any]:
    metrics = state.get("metrics") if isinstance(state.get("metrics"), dict) else {}
    raw_path = str(artifacts.get("validation_report") or "")
    report_payload: dict[str, Any] | None = None
    if raw_path:
        resolved = _ensure_within_workdir(workdir, raw_path)
        if resolved is not None:
            report_payload = _read_json(resolved)
    status = "unknown"
    if str(state.get("stage") or "") == "validated":
        status = "passed"
    elif str(state.get("stage") or "").endswith("failed"):
        status = "failed"
    elif report_payload:
        if report_payload.get("ok") is True:
            status = "passed"
        elif report_payload.get("ok") is False:
            status = "failed"
    return {
        "available": bool(report_payload or metrics),
        "status": status,
        "metrics": metrics,
        "report": report_payload or {},
    }


def build_reference_preview(
    *,
    workdir: Path,
    state: dict[str, Any],
    ref_id: str,
) -> dict[str, Any] | None:
    candidates = _load_reference_candidates(workdir, state)
    pools = [
        candidates.get("selected_sde_references") or [],
        candidates.get("selected_sdevice_references") or [],
        candidates.get("selected_function_references") or [],
    ]
    for pool in pools:
        if not isinstance(pool, list):
            continue
        for item in pool:
            if not isinstance(item, dict):
                continue
            if str(item.get("ref_id") or "") != ref_id:
                continue
            return {
                "ref_id": ref_id,
                "title": str(item.get("title") or ""),
                "source_kind": str(item.get("source_kind") or ""),
                "source_label": str(item.get("source_label") or ""),
                "family": str(item.get("family") or ""),
                "summary": str(item.get("summary") or ""),
                "why_matched": str(item.get("why_matched") or ""),
                "prompt_excerpt": str(item.get("prompt_excerpt") or ""),
                "code_excerpt": str(item.get("code_excerpt") or ""),
                "score": item.get("score"),
            }
    return None


def build_session_export(
    *,
    record: Any,
    state: dict[str, Any],
    public_artifacts: list[dict[str, Any]],
    export_format: str = "json",
) -> dict[str, Any]:
    summary = build_session_summary(record=record, state=state, public_artifacts=public_artifacts)
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    brief = build_brief_summary(workdir=getattr(record, "workdir"), artifacts=artifacts)
    validation = build_validation_summary(workdir=getattr(record, "workdir"), state=state, artifacts=artifacts)
    workspace = build_workspace_manifest(
        workdir=getattr(record, "workdir"),
        state=state,
        artifacts=artifacts,
        uploads=list(getattr(record, "uploads", {}).values()),
    )
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "brief_summary": brief,
        "validation_summary": validation,
        "workspace_manifest": workspace,
    }
    if export_format == "markdown":
        return {
            "content_type": "text/markdown; charset=utf-8",
            "file_name": f"tcad-session-{summary['conversation_id']}.md",
            "body": _build_session_export_markdown(payload),
        }
    return {
        "content_type": "application/json; charset=utf-8",
        "file_name": f"tcad-session-{summary['conversation_id']}.json",
        "body": json.dumps(payload, ensure_ascii=False, indent=2),
    }


def _build_session_export_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), dict) else {}
    validation = payload.get("validation_summary", {}) if isinstance(payload.get("validation_summary"), dict) else {}
    workspace = payload.get("workspace_manifest", {}) if isinstance(payload.get("workspace_manifest"), dict) else {}
    reference_stats = summary.get("reference_stats", {}) if isinstance(summary.get("reference_stats"), dict) else {}
    artifact_counts = summary.get("artifact_counts", {}) if isinstance(summary.get("artifact_counts"), dict) else {}
    tool_sequence = summary.get("tool_sequence", []) if isinstance(summary.get("tool_sequence"), list) else []
    demo_case = summary.get("demo_case", {}) if isinstance(summary.get("demo_case"), dict) else {}
    demo_panels = summary.get("demo_panels", []) if isinstance(summary.get("demo_panels"), list) else []
    lines = [
        "# TCAD 会话摘要",
        "",
        f"- 会话ID：`{summary.get('conversation_id', '')}`",
        f"- 当前阶段：{summary.get('stage_label', summary.get('stage', ''))}",
        f"- 验证状态：{summary.get('validation_status_label', validation.get('status', summary.get('validation_status', 'unknown')))}",
        "",
    ]
    if demo_case:
        lines.extend(
            [
                "## 任务概览",
                "",
                f"- 标题：{demo_case.get('title', '')}",
                f"- 能力：{'、'.join(demo_case.get('capabilities', []) or []) or '—'}",
                "",
            ]
        )
    lines.extend(
        [
            "## 用户需求",
            "",
            summary.get("requirement", "") or summary.get("last_user_message", ""),
            "",
            "## 本轮摘要",
            "",
            f"- 参考增强：SDE {reference_stats.get('sde', 0)} 个，SDevice {reference_stats.get('sdevice', 0)} 个，函数知识 {reference_stats.get('function', 0)} 条",
            f"- 主要产物：{artifact_counts.get('total', 0)} 个，其中图片 {artifact_counts.get('images', 0)} 个",
            f"- 当前工作区：主要文件 {workspace.get('primary_file_count', 0)}，日志 {workspace.get('log_file_count', 0)}",
            "",
            "## 工具调用序列",
            "",
        ]
    )
    if tool_sequence:
        lines.extend(f"- `{tool}`" for tool in tool_sequence)
    else:
        lines.append("- 暂无记录")
    if demo_panels:
        lines.extend(["", "## 任务说明", ""])
        for panel in demo_panels:
            title = str(panel.get("title") or "").strip()
            if title:
                lines.append(f"### {title}")
            for item in panel.get("items", []) if isinstance(panel.get("items"), list) else []:
                lines.append(f"- {item}")
            lines.append("")
    lines.extend(["", "## 最近进展", ""])
    notes_tail = summary.get("notes_tail", []) if isinstance(summary.get("notes_tail"), list) else []
    if notes_tail:
        lines.extend(f"- {note}" for note in notes_tail)
    else:
        lines.append("- 暂无记录")
    lines.extend(["", "## 主要产物", ""])
    for item in summary.get("artifacts", []) if isinstance(summary.get("artifacts"), list) else []:
        lines.append(f"- {item.get('label', '文件')}：`{item.get('file_name', '')}`")
    return "\n".join(lines).strip() + "\n"
