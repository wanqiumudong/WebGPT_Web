from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .task_spec import TaskSpec, normalize_target_artifact


@dataclass
class CoverageItem:
    section: str
    requirement: str
    status: str
    evidence: str = ""
    reason: str = ""


@dataclass
class CoverageAudit:
    phase: str
    target_artifact: str
    success: bool
    summary: str
    items: list[CoverageItem] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)


def _extract_json_block(text: str) -> dict[str, Any] | None:
    raw = text.strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(raw[start : end + 1])
        if isinstance(payload, dict):
            return payload
    except Exception:
        return None
    return None


def audit_generated_artifact(
    chat_fn: Callable[[list[dict[str, str]]], str],
    *,
    phase: str,
    device_type: str,
    target_artifact: str,
    task_spec: TaskSpec,
    code_text: str,
    tdr_info: str = "",
) -> CoverageAudit:
    """基于 TaskSpec 对生成结果做覆盖审计。

    这里使用主模型做结构化审计，不引入通用 RAG。
    """

    spec_lines = task_spec.as_lines() or ["(empty task spec)"]
    system_prompt = (
        "你是 TCAD 代码覆盖审计器。"
        "请根据声明式 TaskSpec 审计当前 deck/结果是否覆盖了需求。"
        "只输出一个 JSON 对象，不要输出其他文字。"
    )
    user_prompt = (
        f"phase={phase}\n"
        f"device_type={device_type}\n"
        f"target_artifact={normalize_target_artifact(target_artifact)}\n\n"
        "TaskSpec:\n"
        + "\n".join(spec_lines)
        + "\n\n生成结果:\n"
        + code_text[:14000]
        + ("\n\nTDR/结构摘要:\n" + tdr_info[:6000] if tdr_info.strip() else "")
        + "\n\n请输出 JSON："
        '{"summary":"...",'
        '"items":[{"section":"geometry","requirement":"...","status":"realized|explicitly_omitted_with_reason|ambiguous_requires_clarification","evidence":"...","reason":"..."}]}'
        "\n要求："
        "\n1) 每条 requirement 都必须给出状态。"
        "\n2) 不要臆造 deck 中不存在的事实。"
        "\n3) 若 deck 明显未覆盖需求，标记为 explicitly_omitted_with_reason。"
        "\n4) 若信息不足无法判断，标记为 ambiguous_requires_clarification。"
    )
    raw = chat_fn(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    )
    payload = _extract_json_block(raw) or {}
    items_raw = payload.get("items", [])
    items: list[CoverageItem] = []
    counts = {
        "realized": 0,
        "explicitly_omitted_with_reason": 0,
        "ambiguous_requires_clarification": 0,
    }
    if isinstance(items_raw, list):
        for item in items_raw:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "")).strip()
            if status not in counts:
                status = "ambiguous_requires_clarification"
            counts[status] += 1
            items.append(
                CoverageItem(
                    section=str(item.get("section", "")).strip() or "unknown",
                    requirement=str(item.get("requirement", "")).strip(),
                    status=status,
                    evidence=str(item.get("evidence", "")).strip(),
                    reason=str(item.get("reason", "")).strip(),
                )
            )
    success = counts["explicitly_omitted_with_reason"] == 0 and counts["ambiguous_requires_clarification"] == 0
    return CoverageAudit(
        phase=phase,
        target_artifact=normalize_target_artifact(target_artifact),
        success=success,
        summary=str(payload.get("summary", "")).strip() or "coverage audit finished",
        items=items,
        stats=counts,
    )
