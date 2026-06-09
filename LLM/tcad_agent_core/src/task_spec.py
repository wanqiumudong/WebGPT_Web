from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


STRUCTURE_ONLY_TARGETS = {"structure", "structure_png", "tdr_info"}
STRUCTURE_ONLY_MARKERS = (
    "只做 sde",
    "只做sde",
    "只输出纯 sde 代码",
    "只输出纯sde代码",
    "sentaurus sde scheme",
    "只看结构",
    "结构图",
    "structure only",
    "only sde",
)
STRUCTURE_PNG_MARKERS = (
    "png",
    "结构图",
    "结构图片",
    "导出图片",
    "导出结构图",
    "图片呢",
    "图片",
    "给我图",
    "看图",
    "看图片",
    "看一下图片",
    "看看图片",
    "想看图片",
    "看到图片",
    "预览图",
    "结构预览",
)
TDR_INFO_MARKERS = (
    "tdr 信息",
    "tdr信息",
    "inspect",
    "材料信息",
    "区域信息",
    "边界信息",
    "tdr内容",
)
CONTINUATION_MARKERS = (
    "继续",
    "接着",
    "下一步",
    "后续",
    "继续执行",
    "继续做",
    "继续推进",
    "继续扩展",
    "往下做",
)
SDEVICE_CHAIN_TOOLS = {
    "generate_sdevice_code",
    "check_sdevice_syntax",
    "run_sdevice",
    "run_svisual_export",
    "validate_results",
}

TARGET_STAGE_ORDER: dict[str, set[str]] = {
    "unspecified": set(),
    "structure": {"sde_done", "tdr_inspected", "svisual_sde_done", "validated"},
    "structure_png": {"svisual_sde_done", "validated"},
    "tdr_info": {"tdr_inspected", "svisual_sde_done", "validated"},
    "sdevice_cmd": {"sdevice_generated", "sdevice_checked", "sdevice_done", "svisual_done", "validated"},
    "iv_curve": {"svisual_done", "validated"},
    "validation_report": {"validated"},
    "full_chain": {"validated"},
    "state_view": set(),
    "tool_list": set(),
    "text_answer": set(),
}


@dataclass
class TaskSpec:
    """声明式任务规格。

    不强行做过细的数值 schema，而是保留对编排最有价值的约束分块。
    每个列表元素应是可审计的单条需求。
    """

    geometry: list[str] = field(default_factory=list)
    materials: list[str] = field(default_factory=list)
    contacts: list[str] = field(default_factory=list)
    doping: list[str] = field(default_factory=list)
    mesh: list[str] = field(default_factory=list)
    simulation: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "TaskSpec":
        if not isinstance(payload, dict):
            return cls()

        def _normalize_list(key: str) -> list[str]:
            raw = payload.get(key, [])
            if raw is None:
                return []
            if isinstance(raw, str):
                raw = [raw]
            if not isinstance(raw, list):
                return []
            items: list[str] = []
            for item in raw:
                text = str(item).strip()
                if text:
                    items.append(text)
            return items

        return cls(
            geometry=_normalize_list("geometry"),
            materials=_normalize_list("materials"),
            contacts=_normalize_list("contacts"),
            doping=_normalize_list("doping"),
            mesh=_normalize_list("mesh"),
            simulation=_normalize_list("simulation"),
            outputs=_normalize_list("outputs"),
            constraints=_normalize_list("constraints"),
        )

    def to_section_map(self) -> dict[str, list[str]]:
        return {
            "geometry": list(self.geometry),
            "materials": list(self.materials),
            "contacts": list(self.contacts),
            "doping": list(self.doping),
            "mesh": list(self.mesh),
            "simulation": list(self.simulation),
            "outputs": list(self.outputs),
            "constraints": list(self.constraints),
        }

    def has_content(self) -> bool:
        return any(self.to_section_map().values())

    def as_lines(self) -> list[str]:
        lines: list[str] = []
        for section, items in self.to_section_map().items():
            if not items:
                continue
            lines.append(f"{section}:")
            for item in items:
                lines.append(f"- {item}")
        return lines


def normalize_target_artifact(value: str) -> str:
    target = (value or "").strip().lower()
    if target in TARGET_STAGE_ORDER:
        return target
    if target in {"structure_only", "sde_only"}:
        return "structure"
    if target in {"curve", "curve_png", "iv", "idvg", "idvd"}:
        return "iv_curve"
    if target in {"report", "validation", "validation_json"}:
        return "validation_report"
    if target in {"reply", "chat"}:
        return "text_answer"
    return "unspecified"


def target_satisfied(stage: str, target_artifact: str) -> bool:
    stages = TARGET_STAGE_ORDER.get(normalize_target_artifact(target_artifact), set())
    if not stages:
        return False
    return stage in stages


def target_blocks_sdevice(target_artifact: str) -> bool:
    return normalize_target_artifact(target_artifact) in STRUCTURE_ONLY_TARGETS


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def infer_target_artifact(requirement: str, simulation_type: str, current_target: str = "unspecified") -> str:
    low = (requirement or "").lower()
    if _contains_any(low, TDR_INFO_MARKERS):
        return "tdr_info"
    if _contains_any(low, STRUCTURE_PNG_MARKERS):
        return "structure_png"
    if _contains_any(low, STRUCTURE_ONLY_MARKERS):
        return "structure_png"
    normalized_target = normalize_target_artifact(current_target)
    if normalized_target != "unspecified" and (
        not low.strip() or _contains_any(low, CONTINUATION_MARKERS)
    ):
        return normalized_target
    simulation = (simulation_type or "").strip().lower()
    if normalized_target == "unspecified" and (
        simulation in {"structure", "structure_only"} or simulation.startswith("structure")
    ):
        return "structure_png"
    return "unspecified"
