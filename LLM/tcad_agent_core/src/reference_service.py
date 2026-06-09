from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from .core import SessionState, preview_text


DEFAULT_DELIVERABLES_ROOT = Path("/data/yphu/TCAD_Agent/code/deliverables")
DEFAULT_DATASET_PROMPT_PAIRS = Path("/data/yphu/Dataset/unified_sde/current/evidence/prompt_pairs/prompt_pairs.json")
DEFAULT_DATASET_CMD_DIR = Path("/data/yphu/Dataset/unified_sde/current/evidence/generated_cmd")
DEFAULT_FUNCTION_KB = Path("/data/yphu/TCAD_RAG/knowledge/sde_function_knowledge_base.json")
DEFAULT_SEED_DIR = Path("/data/yphu/TCAD_RAG/knowledge/legacy_seed_pack")

TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{1,6}|[a-zA-Z][a-zA-Z0-9_./+-]*")
STOPWORDS = {
    "please",
    "sentaurus",
    "tcad",
    "scheme",
    "script",
    "code",
    "cmd",
    "sde",
    "sdevice",
    "the",
    "and",
    "with",
    "for",
    "that",
    "this",
    "输出",
    "直接",
    "生成",
    "代码",
    "脚本",
    "可执行",
    "要求",
    "参数",
    "结构",
    "器件",
    "需要",
    "请把",
}
PROMPT_EXCERPT_LIMIT = 420
IR_EXCERPT_LIMIT = 900
CODE_EXCERPT_LIMIT = 1600
SDEVICE_ARTIFACT_HINTS = ("sdevice_des.cmd", "validation.json", "result_", "idvg", "idvd", "curve")
FUNCTION_CUE_MAP = {
    "gaussian": ("sdedr:define-gaussian-profile", "sdedr:define-analytical-profile-placement"),
    "analytical": ("sdedr:define-analytical-profile-placement",),
    "mesh": (
        "sdedr:define-refeval-window",
        "sdedr:define-refinement-size",
        "sdedr:define-refinement-placement",
        "sdedr:define-refinement-function",
    ),
    "contact": ("sdegeo:define-contact-set", "sdegeo:set-contact", "find-edge-id"),
    "polygon": ("sdegeo:create-polygon", "sdegeo:create-rectangle"),
    "rectangle": ("sdegeo:create-rectangle",),
    "interface": ("sdedr:define-refinement-function",),
    "baseline": ("sdedr:define-refeval-window", "sdedr:define-gaussian-profile"),
}
DEVICE_HINTS = {
    "nmos": {"nmos", "soi", "mosfet", "bodytie", "source", "drain", "gate", "spacer"},
    "pmos": {"pmos", "soi", "mosfet", "source", "drain", "gate", "spacer"},
    "mosfet": {"mosfet", "nmos", "pmos", "gate", "drain", "source", "spacer"},
    "bjt": {"bjt", "hbt", "sige", "collector", "emitter", "base"},
    "hbt": {"hbt", "sige", "collector", "emitter", "base"},
    "diode": {"diode", "pn", "schottky", "anode", "cathode"},
    "moscap": {"moscap", "cv", "oxide", "gate", "capacitance"},
    "hemt": {"hemt", "hfet", "gan", "algan", "field", "plate"},
    "igbt": {"igbt", "collector", "emitter", "gate", "drift"},
}


@dataclass(frozen=True)
class ReferenceBundle:
    selected_sde_references: list[dict[str, Any]]
    selected_sdevice_references: list[dict[str, Any]]
    selected_function_references: list[dict[str, Any]]
    reference_context: str
    brief: dict[str, Any]
    catalog: dict[str, Any]
    summary_note: str


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _clip(text: str, limit: int) -> str:
    normalized = " ".join((text or "").split())
    return preview_text(normalized, limit)


def _tokenize(text: str) -> set[str]:
    tokens = {token.lower() for token in TOKEN_RE.findall(text or "")}
    return {token for token in tokens if len(token) > 1 and token not in STOPWORDS}


def _ir_summary(canonical_ir: dict[str, Any]) -> str:
    parts: list[str] = []
    parameter_setup = canonical_ir.get("parameter_setup", {})
    if isinstance(parameter_setup, dict):
        primary = parameter_setup.get("primary_parameters", [])
        if isinstance(primary, list) and primary:
            parts.append("参数: " + "；".join(str(item) for item in primary[:5]))
    geometry = canonical_ir.get("geometry_construction", {})
    if isinstance(geometry, dict):
        topology = str(geometry.get("topology") or "").strip()
        if topology:
            parts.append("拓扑: " + topology)
    contacts = canonical_ir.get("contact_definition", {})
    if isinstance(contacts, dict):
        names = contacts.get("contact_names", [])
        if isinstance(names, list) and names:
            parts.append("接触: " + ", ".join(str(item) for item in names[:6]))
    doping = canonical_ir.get("doping_definition", {})
    if isinstance(doping, dict):
        gaussian = doping.get("gaussian_profiles", [])
        if isinstance(gaussian, list) and gaussian:
            parts.append("掺杂: " + ", ".join(str(item) for item in gaussian[:4]))
    mesh = canonical_ir.get("mesh_definition", {})
    if isinstance(mesh, dict):
        windows = mesh.get("windows", [])
        if isinstance(windows, list) and windows:
            parts.append("网格: " + ", ".join(str(item) for item in windows[:4]))
    return _clip(" | ".join(parts), IR_EXCERPT_LIMIT)


@lru_cache(maxsize=1)
def _load_active_cases() -> list[dict[str, Any]]:
    pack = _read_json(DEFAULT_DELIVERABLES_ROOT / "catalogs" / "active_case_prompt_pack_v1.json") or {}
    cases: list[dict[str, Any]] = []
    for item in pack.get("cases", []):
        if not isinstance(item, dict):
            continue
        source_path = Path(str(item.get("source_path") or "")).expanduser() if item.get("source_path") else None
        code_excerpt = ""
        if source_path and source_path.exists():
            artifact_dir = source_path / "artifacts"
            for name in ("sde_dvs.cmd", "sdevice_des.cmd"):
                candidate = artifact_dir / name
                if candidate.exists():
                    code_excerpt = _clip(candidate.read_text(encoding="utf-8", errors="replace"), CODE_EXCERPT_LIMIT)
                    break
        cases.append(
            {
                "ref_id": f"deliverables:{item.get('case_id', '')}",
                "source_kind": "deliverables_case",
                "title": str(item.get("case_id") or "deliverable_case"),
                "device_type": str(item.get("device_type") or "unspecified"),
                "simulation_type": str(item.get("simulation_type") or "unspecified"),
                "summary": _clip(str(item.get("full_prompt") or item.get("prompt") or ""), PROMPT_EXCERPT_LIMIT),
                "prompt_excerpt": _clip(str(item.get("full_prompt") or item.get("prompt") or ""), PROMPT_EXCERPT_LIMIT),
                "code_excerpt": code_excerpt,
                "reference_basis": list(item.get("reference_basis") or [])[:4],
                "artifact_files": list(item.get("artifact_files") or [])[:8],
            }
        )
    return cases


@lru_cache(maxsize=1)
def _load_dataset_records() -> list[dict[str, Any]]:
    payload = _read_json(DEFAULT_DATASET_PROMPT_PAIRS) or {}
    records: list[dict[str, Any]] = []
    for record in payload.get("records", []):
        if not isinstance(record, dict):
            continue
        filename = str(record.get("filename") or "").strip()
        cmd_path = DEFAULT_DATASET_CMD_DIR / filename
        code_excerpt = ""
        if filename and cmd_path.exists():
            code_excerpt = _clip(cmd_path.read_text(encoding="utf-8", errors="replace"), CODE_EXCERPT_LIMIT)
        prompts = record.get("prompts", {}) if isinstance(record.get("prompts"), dict) else {}
        canonical_ir = record.get("canonical_ir", {}) if isinstance(record.get("canonical_ir"), dict) else {}
        device_profile = record.get("device_profile", {}) if isinstance(record.get("device_profile"), dict) else {}
        records.append(
            {
                "ref_id": f"dataset:{filename}",
                "source_kind": "dataset_prompt_pair",
                "title": filename or str(record.get("stem") or "dataset_case"),
                "device_type": str(device_profile.get("subfamily") or record.get("family") or "unspecified"),
                "simulation_type": "structure",
                "summary": _clip(str(prompts.get("complex") or ""), PROMPT_EXCERPT_LIMIT),
                "prompt_excerpt": _clip(str(prompts.get("complex") or ""), PROMPT_EXCERPT_LIMIT),
                "code_excerpt": code_excerpt,
                "ir_excerpt": _ir_summary(canonical_ir),
                "family": str(record.get("family") or ""),
                "source_anchor": str(record.get("source_anchor") or ""),
                "seed_reference": str(record.get("seed_reference") or ""),
            }
        )
    return records


@lru_cache(maxsize=1)
def _load_seed_references() -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for path in sorted(DEFAULT_SEED_DIR.glob("*.cmd")):
        references.append(
            {
                "ref_id": f"seed:{path.name}",
                "source_kind": "legacy_seed_cmd",
                "title": path.name,
                "device_type": "unspecified",
                "simulation_type": "structure",
                "summary": _clip(path.stem.replace("_", " "), 160),
                "prompt_excerpt": "",
                "code_excerpt": _clip(path.read_text(encoding="utf-8", errors="replace"), CODE_EXCERPT_LIMIT),
            }
        )
    return references


@lru_cache(maxsize=1)
def _load_function_docs() -> list[dict[str, Any]]:
    payload = _read_json(DEFAULT_FUNCTION_KB) or {}
    documents: list[dict[str, Any]] = []
    for name, body in payload.items():
        if not isinstance(body, dict):
            continue
        content_parts = [
            str(body.get("introduction") or ""),
            str(body.get("syntax") or ""),
            str(body.get("description") or ""),
            " ".join(str(item) for item in body.get("examples") or []),
        ]
        documents.append(
            {
                "ref_id": f"function:{name}",
                "source_kind": "function_doc",
                "title": name,
                "device_type": "unspecified",
                "simulation_type": "structure",
                "summary": _clip(" ".join(content_parts), 260),
                "prompt_excerpt": "",
                "code_excerpt": _clip(str(body.get("syntax") or ""), 320),
            }
        )
    return documents


def _shared_keywords(query_tokens: set[str], text: str, limit: int = 4) -> list[str]:
    shared = sorted(query_tokens & _tokenize(text))
    return shared[:limit]


def _device_bonus(query_low: str, candidate_text: str, device_type: str) -> float:
    candidate_low = candidate_text.lower()
    bonus = 0.0
    for hint in DEVICE_HINTS.get(device_type.lower(), set()):
        if hint in query_low and hint in candidate_low:
            bonus += 2.0
    for hints in DEVICE_HINTS.values():
        overlap = sum(1 for hint in hints if hint in query_low and hint in candidate_low)
        if overlap >= 2:
            bonus += 1.5
            break
    return bonus


def _score_candidate(
    candidate: dict[str, Any],
    *,
    query_text: str,
    query_tokens: set[str],
    device_type: str,
    simulation_type: str,
    phase: str,
) -> tuple[float, list[str]]:
    candidate_text = " ".join(
        [
            str(candidate.get("title") or ""),
            str(candidate.get("summary") or ""),
            str(candidate.get("prompt_excerpt") or ""),
            str(candidate.get("ir_excerpt") or ""),
            str(candidate.get("code_excerpt") or "")[:600],
            str(candidate.get("device_type") or ""),
            str(candidate.get("simulation_type") or ""),
            " ".join(str(item) for item in candidate.get("reference_basis") or []),
        ]
    )
    shared = _shared_keywords(query_tokens, candidate_text)
    score = float(len(shared))
    score += _device_bonus(query_text.lower(), candidate_text, device_type)
    if simulation_type and simulation_type != "unspecified" and simulation_type.lower() in candidate_text.lower():
        score += 2.0
    if phase == "generate_sdevice" and candidate.get("source_kind") == "deliverables_case":
        artifact_text = " ".join(candidate.get("artifact_files") or []).lower()
        if any(hint in artifact_text for hint in SDEVICE_ARTIFACT_HINTS):
            score += 5.0
    if phase == "generate_sde":
        if candidate.get("source_kind") == "dataset_prompt_pair":
            score += 2.5
        elif candidate.get("source_kind") == "deliverables_case":
            score += 1.5
    return score, shared


def _format_reference(candidate: dict[str, Any], *, score: float, shared: list[str], role: str) -> dict[str, Any]:
    why = "、".join(shared) if shared else "同器件家族/相近任务"
    source_kind = str(candidate.get("source_kind") or "")
    if source_kind == "deliverables_case":
        source_label = "deliverables"
    elif source_kind == "dataset_prompt_pair":
        source_label = "unified_sde"
    elif source_kind == "function_doc":
        source_label = "TCAD_RAG"
    else:
        source_label = "seed_pack"
    return {
        "ref_id": str(candidate.get("ref_id") or ""),
        "role": role,
        "source_kind": source_kind,
        "source_label": source_label,
        "title": str(candidate.get("title") or ""),
        "device_type": str(candidate.get("device_type") or "unspecified"),
        "simulation_type": str(candidate.get("simulation_type") or "unspecified"),
        "score": round(score, 3),
        "why_matched": why,
        "summary": str(candidate.get("summary") or ""),
        "prompt_excerpt": str(candidate.get("prompt_excerpt") or ""),
        "code_excerpt": str(candidate.get("code_excerpt") or ""),
        "ir_excerpt": str(candidate.get("ir_excerpt") or ""),
        "reference_basis": list(candidate.get("reference_basis") or [])[:4],
        "artifact_files": list(candidate.get("artifact_files") or [])[:8],
    }


class ReferenceService:
    def __init__(self, workspace: Path):
        self.workspace = workspace

    def prepare_reference_bundle(self, *, state: SessionState, phase: str) -> ReferenceBundle:
        task_spec_text = "\n".join(state.spec.task_spec.as_lines()) if state.spec.task_spec.has_content() else ""
        query_text = "\n".join(
            [
                state.spec.requirement,
                str(state.artifacts.get("last_user_instruction") or ""),
                state.spec.device_type,
                state.spec.simulation_type,
                state.spec.target_artifact,
                task_spec_text,
            ]
        )
        query_tokens = _tokenize(query_text)
        sde_references = self._select_sde_references(query_text, query_tokens, state, phase)
        sdevice_references = self._select_sdevice_references(query_text, query_tokens, state, phase)
        function_references = self._select_function_references(query_text, query_tokens)
        context = self._build_reference_context(sde_references, sdevice_references, function_references, phase)
        brief = {
            "phase": phase,
            "query_preview": _clip(query_text, 220),
            "selected_sde_references": sde_references,
            "selected_sdevice_references": sdevice_references,
            "selected_function_references": function_references,
        }
        catalog = dict(brief)
        return ReferenceBundle(
            selected_sde_references=sde_references,
            selected_sdevice_references=sdevice_references,
            selected_function_references=function_references,
            reference_context=context,
            brief=brief,
            catalog=catalog,
            summary_note=self._build_summary_note(sde_references, sdevice_references, function_references),
        )

    def persist_reference_bundle(self, *, session_dir: Path, bundle: ReferenceBundle) -> dict[str, str]:
        reports_dir = session_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        catalog_path = reports_dir / "reference_candidates.json"
        brief_path = reports_dir / "reference_brief.json"
        context_path = reports_dir / f"{bundle.brief.get('phase', 'reference')}_reference_context.txt"

        payload = _read_json(catalog_path) or {}
        payload.update(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "phase": bundle.brief.get("phase", "unknown"),
                "selected_sde_references": bundle.selected_sde_references,
                "selected_sdevice_references": bundle.selected_sdevice_references,
                "selected_function_references": bundle.selected_function_references,
                "summary_note": bundle.summary_note,
            }
        )
        catalog_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        brief_path.write_text(json.dumps(bundle.brief, ensure_ascii=False, indent=2), encoding="utf-8")
        context_path.write_text(bundle.reference_context, encoding="utf-8")
        return {
            "reference_candidates": str(catalog_path),
            "reference_brief": str(brief_path),
            f"{bundle.brief.get('phase', 'reference')}_reference_context": str(context_path),
        }

    def _select_sde_references(
        self,
        query_text: str,
        query_tokens: set[str],
        state: SessionState,
        phase: str,
    ) -> list[dict[str, Any]]:
        candidates = [*_load_active_cases(), *_load_dataset_records(), *_load_seed_references()]
        scored: list[tuple[float, dict[str, Any], list[str]]] = []
        for candidate in candidates:
            score, shared = _score_candidate(
                candidate,
                query_text=query_text,
                query_tokens=query_tokens,
                device_type=state.spec.device_type,
                simulation_type=state.spec.simulation_type,
                phase=phase,
            )
            if score <= 0:
                continue
            scored.append((score, candidate, shared))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [_format_reference(candidate, score=score, shared=shared, role="sde") for score, candidate, shared in scored[:4]]

    def _select_sdevice_references(
        self,
        query_text: str,
        query_tokens: set[str],
        state: SessionState,
        phase: str,
    ) -> list[dict[str, Any]]:
        candidates = [item for item in _load_active_cases() if item.get("code_excerpt")]
        scored: list[tuple[float, dict[str, Any], list[str]]] = []
        for candidate in candidates:
            score, shared = _score_candidate(
                candidate,
                query_text=query_text,
                query_tokens=query_tokens,
                device_type=state.spec.device_type,
                simulation_type=state.spec.simulation_type,
                phase="generate_sdevice",
            )
            if score <= 6.0:
                continue
            scored.append((score, candidate, shared))
        scored.sort(key=lambda item: item[0], reverse=True)
        limit = 2 if phase == "generate_sdevice" else 1
        return [_format_reference(candidate, score=score, shared=shared, role="sdevice") for score, candidate, shared in scored[:limit]]

    def _select_function_references(self, query_text: str, query_tokens: set[str]) -> list[dict[str, Any]]:
        query_low = query_text.lower()
        forced_names: list[str] = []
        for cue, names in FUNCTION_CUE_MAP.items():
            if cue in query_low:
                forced_names.extend(names)
        function_docs = {item["title"]: item for item in _load_function_docs()}
        selected: list[dict[str, Any]] = []
        for name in forced_names[:4]:
            if name in function_docs:
                selected.append(_format_reference(function_docs[name], score=9.0, shared=[name], role="function"))
        if selected:
            return selected[:4]
        return []

    def _build_reference_context(
        self,
        sde_references: list[dict[str, Any]],
        sdevice_references: list[dict[str, Any]],
        function_references: list[dict[str, Any]],
        phase: str,
    ) -> str:
        lines = [
            "【参考选择】",
            "以下参考仅用于约束首轮生成与失败修复，不要逐字照抄；若与当前需求冲突，以当前需求为准。",
        ]
        if sde_references:
            lines.extend(["", "【SDE 参考】"])
            for index, item in enumerate(sde_references[:3], start=1):
                lines.append(f"[{index}] {item['title']} ({item['source_kind']})")
                lines.append(f"- 命中原因: {item['why_matched']}")
                if item["summary"]:
                    lines.append(f"- Prompt 摘要: {item['summary']}")
                if item["ir_excerpt"]:
                    lines.append(f"- IR 摘要: {item['ir_excerpt']}")
                if item["code_excerpt"]:
                    lines.append(f"- 关键代码片段:\n{item['code_excerpt']}")
        if phase == "generate_sdevice" and sdevice_references:
            lines.extend(["", "【SDevice / Full-flow 参考】"])
            for index, item in enumerate(sdevice_references[:2], start=1):
                lines.append(f"[{index}] {item['title']} ({item['source_kind']})")
                lines.append(f"- 命中原因: {item['why_matched']}")
                if item["summary"]:
                    lines.append(f"- Prompt 摘要: {item['summary']}")
                if item["code_excerpt"]:
                    lines.append(f"- 关键代码片段:\n{item['code_excerpt']}")
        if function_references:
            lines.extend(["", "【函数/接口参考】"])
            for index, item in enumerate(function_references[:3], start=1):
                lines.append(f"[{index}] {item['title']}")
                if item["summary"]:
                    lines.append(f"- 摘要: {item['summary']}")
                if item["code_excerpt"]:
                    lines.append(f"- 语法: {item['code_excerpt']}")
        return "\n".join(lines)

    @staticmethod
    def _build_summary_note(
        sde_references: list[dict[str, Any]],
        sdevice_references: list[dict[str, Any]],
        function_references: list[dict[str, Any]],
    ) -> str:
        parts: list[str] = []
        if sde_references:
            parts.append(f"已选中 {len(sde_references)} 个 SDE 参考")
        if sdevice_references:
            parts.append(f"{len(sdevice_references)} 个 full-flow / SDevice 参考")
        if function_references:
            parts.append(f"{len(function_references)} 条函数知识")
        return "，".join(parts) if parts else "当前未命中可用参考，将按通用工程约束继续生成"
