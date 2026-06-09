from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CONVERGENCE_HINTS = (
    "converg",
    "newton",
    "rhs",
    "residual",
    "line search",
    "step cut",
    "timestep",
    "time step",
)

SDE_GEOMETRY_HINTS = (
    "vertex:fillet",
    "cannot calculate normal vector",
    "could_not_fillet",
    "divide by zero",
    "shortest edge:",
    "pm_unbalanced_states",
    "self-intersection",
    "boolean operation",
    "topology",
)


@dataclass
class FailureRecord:
    stage: str
    failure_class: str
    rollback_stage: str
    summary: str
    log_paths: dict[str, str] = field(default_factory=dict)
    evidence_preview: dict[str, str] = field(default_factory=dict)
    suggested_focus: list[str] = field(default_factory=list)


def _read_preview(path: str, max_chars: int = 2400) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8", errors="ignore")
    return text[-max_chars:] if len(text) > max_chars else text


def classify_failure(
    *,
    stage: str,
    message: str,
    logs: dict[str, str] | None = None,
    validation: dict[str, Any] | None = None,
) -> FailureRecord:
    logs = logs or {}
    validation = validation or {}
    previews = {name: _read_preview(path) for name, path in logs.items() if path}
    corpus = "\n".join([stage, message, *previews.values()]).lower()

    failure_class = "unknown_failure"
    rollback_stage = "created"
    suggested_focus: list[str] = []

    if stage == "sde_generation_failed":
        if "llm_call_error" in corpus or "missing api key" in corpus or "llm request failed" in corpus:
            failure_class = "llm_generation_failure"
            rollback_stage = "created"
            suggested_focus = ["llm api connectivity", "model availability", "credential configuration"]
        elif "syntax check: passed" in corpus and any(hint in corpus for hint in SDE_GEOMETRY_HINTS):
            failure_class = "sde_geometry_failure"
            rollback_stage = "sde_generated"
            suggested_focus = ["fillet radius", "vertex selection", "geometry topology", "boolean consistency"]
        elif "syntax check: passed" in corpus:
            failure_class = "sde_runtime_failure"
            rollback_stage = "sde_generated"
            suggested_focus = ["geometry execution", "mesh output generation", "sde runtime log"]
        else:
            failure_class = "sde_syntax"
            rollback_stage = "created"
            suggested_focus = ["scheme syntax", "sde command validity", "contact/doping/mesh section completeness"]
    elif stage in {"sde_check_failed", "sde_checkrun_failed"}:
        failure_class = "sde_syntax"
        rollback_stage = "sde_generated"
        suggested_focus = ["scheme syntax", "sde command validity", "contact/doping/mesh section completeness"]
    elif stage == "sde_failed":
        if any(hint in corpus for hint in SDE_GEOMETRY_HINTS):
            failure_class = "sde_geometry_failure"
            rollback_stage = "sde_generated"
            suggested_focus = ["fillet radius", "vertex selection", "geometry topology", "boolean consistency"]
        else:
            failure_class = "sde_runtime_failure"
            rollback_stage = "sde_generated"
            suggested_focus = ["geometry execution", "region/material layout", "mesh output generation"]
    elif stage == "tdr_inspect_failed":
        failure_class = "sde_semantic_mismatch"
        rollback_stage = "sde_generated"
        suggested_focus = ["geometry consistency", "region/material layout", "mesh output generation"]
    elif stage in {"sdevice_generation_failed", "sdevice_check_failed"}:
        failure_class = "sdevice_syntax"
        rollback_stage = "tdr_inspected" if stage == "sdevice_generation_failed" else "sdevice_generated"
        suggested_focus = ["electrode names", "physics/math/solve sections", "grid-contact consistency"]
    elif stage == "sdevice_failed":
        if any(hint in corpus for hint in CONVERGENCE_HINTS):
            failure_class = "sdevice_convergence"
            suggested_focus = ["math solver settings", "bias path", "initialization", "mesh quality"]
        else:
            failure_class = "sdevice_goal_mismatch"
            suggested_focus = ["sdevice deck completeness", "output plot generation", "physics configuration"]
        rollback_stage = "sdevice_generated"
    elif stage in {"svisual_failed", "svisual_sde_failed"}:
        failure_class = "render_insufficient"
        rollback_stage = "sde_done" if stage == "svisual_sde_failed" else "sdevice_done"
        suggested_focus = ["source file selection", "svisual script mode", "non-blank export"]
    elif stage == "validation_failed":
        checks = validation.get("checks", {}) if isinstance(validation, dict) else {}
        if checks.get("no_exit_due_to_failure") is False:
            failure_class = "sdevice_convergence"
            rollback_stage = "sdevice_generated"
            suggested_focus = ["solver convergence", "bias schedule", "math section"]
        elif checks.get("curve_y_span_nonzero") is False or checks.get("svisual_curve_exported") is False:
            failure_class = "render_insufficient"
            rollback_stage = "sdevice_done"
            suggested_focus = ["plot column selection", "svisual curve export", "curve postprocess"]
        else:
            failure_class = "sdevice_goal_mismatch"
            rollback_stage = "sdevice_done"
            suggested_focus = ["target metrics", "curve validity", "physics-result consistency"]

    return FailureRecord(
        stage=stage,
        failure_class=failure_class,
        rollback_stage=rollback_stage,
        summary=message.strip() or stage,
        log_paths=logs,
        evidence_preview=previews,
        suggested_focus=suggested_focus,
    )


def write_failure_report(session_dir: Path, record: FailureRecord, *, prefix: str = "failure") -> Path:
    reports_dir = session_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{prefix}_{record.stage}.json"
    path = reports_dir / stamp
    path.write_text(
        json.dumps(
            {
                "stage": record.stage,
                "failure_class": record.failure_class,
                "rollback_stage": record.rollback_stage,
                "summary": record.summary,
                "log_paths": record.log_paths,
                "evidence_preview": record.evidence_preview,
                "suggested_focus": record.suggested_focus,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path
