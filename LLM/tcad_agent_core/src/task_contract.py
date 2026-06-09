from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Iterable

from .core import PlanStep, SessionState, TaskTodo, preview_text
from .task_spec import infer_target_artifact, target_blocks_sdevice, target_satisfied


PLAN_PENDING_STATUSES = {"pending", "in_progress"}
PLAN_TERMINAL_STATUSES = {"completed", "failed", "skipped", "blocked"}


def _tool_available(tool_name: str, tool_names: Iterable[str]) -> bool:
    return tool_name in set(tool_names)


def _plan_id() -> str:
    return f"plan-{uuid.uuid4().hex[:8]}"


def _structure_done_criteria() -> list[str]:
    return [
        "SDE 脚本已生成并可继续复用。",
        "结构链路已跑通，mesh/TDR 等关键结构产物已落盘。",
        "至少一份结构侧证据已导出，例如 TDR 信息或结构图片。",
    ]


def _full_chain_done_criteria() -> list[str]:
    return [
        "SDE 结构链路已完成并保留可复用产物。",
        "SDevice 仿真链路已执行到结果导出阶段。",
        "验证结果已给出，或已经明确当前阻塞和建议下一步。",
    ]


def _build_summary(state: SessionState, instruction: str) -> str:
    target = state.spec.target_artifact or "unspecified"
    return f"围绕当前请求推进 TCAD 任务，目标终点为 {target}：{preview_text(instruction or state.spec.requirement or '(空)', 96)}"


def _structure_templates(attempt: int) -> list[PlanStep]:
    return [
        PlanStep(
            step_id=f"sde-generate-a{attempt}",
            title="生成 SDE 结构代码",
            tool_name="generate_sde_code",
            expected_artifacts=["sde_cmd"],
            attempt=attempt,
            narration_pre="我先把当前需求整理成可执行的 SDE 结构脚本。",
            narration_post_success="SDE 脚本已经生成，接下来做语法检查。",
            narration_post_failure="SDE 脚本生成失败，我先停在这里，随后准备修复该步骤。",
        ),
        PlanStep(
            step_id=f"sde-check-a{attempt}",
            title="检查 SDE 语法",
            tool_name="check_sde_syntax",
            expected_artifacts=["sde_cmd"],
            attempt=attempt,
            narration_pre="我先检查一遍 SDE 语法，避免后续执行直接中断。",
            narration_post_success="语法检查通过，我继续运行 SDE 生成结构产物。",
            narration_post_failure="语法检查没有通过，我先记录失败点并准备重规划。",
        ),
        PlanStep(
            step_id=f"sde-run-a{attempt}",
            title="运行 SDE 生成结构产物",
            tool_name="run_sde",
            expected_artifacts=["mesh", "bnd"],
            attempt=attempt,
            narration_pre="语法已经通过，我开始运行 SDE 生成 mesh 和 TDR。",
            narration_post_success="结构产物已经生成，我继续检查 TDR 内容。",
            narration_post_failure="SDE 执行失败，我先保留当前日志和中间产物。",
        ),
        PlanStep(
            step_id=f"tdr-inspect-a{attempt}",
            title="检查 TDR 结构信息",
            tool_name="inspect_tdr",
            expected_artifacts=["tdr_info_report"],
            attempt=attempt,
            narration_pre="我先检查 TDR 里的材料、区域和边界信息，确认结构符合预期。",
            narration_post_success="TDR 信息已经拿到，我再导出结构图方便直接核对。",
            narration_post_failure="TDR 检查没有完成，我先停在结构检查这一步。",
        ),
        PlanStep(
            step_id=f"sde-visualize-a{attempt}",
            title="导出结构图片",
            tool_name="run_svisual_sde_export",
            expected_artifacts=["svisual_sde_png", "svisual_png"],
            attempt=attempt,
            narration_pre="我再导出一份结构图，方便你直接核对几何和区域分布。",
            narration_post_success="",
            narration_post_failure="结构图导出失败，我先保留已有结构产物并标出失败点。",
        ),
    ]


def _full_chain_templates(attempt: int) -> list[PlanStep]:
    return _structure_templates(attempt) + [
        PlanStep(
            step_id=f"sdevice-generate-a{attempt}",
            title="生成 SDevice 仿真代码",
            tool_name="generate_sdevice_code",
            expected_artifacts=["sdevice_cmd"],
            attempt=attempt,
            narration_pre="结构链路已经准备好，我开始补齐 SDevice 仿真 deck。",
            narration_post_success="SDevice 脚本已经生成，接下来做语法和前置检查。",
            narration_post_failure="SDevice 脚本生成失败，我先停下来准备修复这一段。",
        ),
        PlanStep(
            step_id=f"sdevice-check-a{attempt}",
            title="检查 SDevice 语法",
            tool_name="check_sdevice_syntax",
            expected_artifacts=["sdevice_cmd"],
            attempt=attempt,
            narration_pre="我先检查一遍 SDevice deck，确认接触名和求解设置没有明显问题。",
            narration_post_success="SDevice 检查通过，我继续执行仿真。",
            narration_post_failure="SDevice 检查没有通过，我先记录问题并准备重规划。",
        ),
        PlanStep(
            step_id=f"sdevice-run-a{attempt}",
            title="运行 SDevice 仿真",
            tool_name="run_sdevice",
            expected_artifacts=["plot", "tdr"],
            attempt=attempt,
            narration_pre="我开始执行 SDevice 仿真，生成曲线和结果数据。",
            narration_post_success="仿真已经完成，我继续导出可读结果。",
            narration_post_failure="SDevice 仿真失败，我先保留 deck 和日志，避免信息丢失。",
        ),
        PlanStep(
            step_id=f"sdevice-visualize-a{attempt}",
            title="导出仿真结果图",
            tool_name="run_svisual_export",
            expected_artifacts=["svisual_png", "svisual_curve_txt"],
            attempt=attempt,
            narration_pre="我把仿真结果导出成可直接查看的图和文本数据。",
            narration_post_success="结果图已经导出，最后我补一次结果验证。",
            narration_post_failure="结果导出失败，我先保留原始结果文件。",
        ),
        PlanStep(
            step_id=f"validate-a{attempt}",
            title="验证结果并汇总",
            tool_name="validate_results",
            expected_artifacts=["validation_report"],
            attempt=attempt,
            narration_pre="我最后检查一下结构、结果和指标是否满足当前目标。",
            narration_post_success="",
            narration_post_failure="验证没有通过，我会把当前阻塞和下一步建议保留下来。",
        ),
    ]


def _active_templates(state: SessionState, attempt: int) -> list[PlanStep]:
    if target_blocks_sdevice(state.spec.target_artifact):
        return _structure_templates(attempt)
    return _full_chain_templates(attempt)


def _step_completed_by_stage(step: PlanStep, stage: str) -> bool:
    mapping = {
        "generate_sde_code": {"sde_generated", "sde_checked", "sde_done", "svisual_sde_done", "tdr_inspected", "sdevice_generated", "sdevice_checked", "sdevice_done", "svisual_done", "validated", "validation_failed"},
        "check_sde_syntax": {"sde_checked", "sde_done", "svisual_sde_done", "tdr_inspected", "sdevice_generated", "sdevice_checked", "sdevice_done", "svisual_done", "validated", "validation_failed"},
        "run_sde": {"sde_done", "svisual_sde_done", "tdr_inspected", "sdevice_generated", "sdevice_checked", "sdevice_done", "svisual_done", "validated", "validation_failed"},
        "inspect_tdr": {"tdr_inspected", "sdevice_generated", "sdevice_checked", "sdevice_done", "svisual_done", "validated", "validation_failed"},
        "run_svisual_sde_export": {"svisual_sde_done", "sdevice_generated", "sdevice_checked", "sdevice_done", "svisual_done", "validated", "validation_failed"},
        "generate_sdevice_code": {"sdevice_generated", "sdevice_checked", "sdevice_done", "svisual_done", "validated", "validation_failed"},
        "check_sdevice_syntax": {"sdevice_checked", "sdevice_done", "svisual_done", "validated", "validation_failed"},
        "run_sdevice": {"sdevice_done", "svisual_done", "validated", "validation_failed"},
        "run_svisual_export": {"svisual_done", "validated", "validation_failed"},
        "validate_results": {"validated"},
    }
    return stage in mapping.get(step.tool_name, set())


def _step_completed_by_artifact(step: PlanStep, artifacts: dict[str, str]) -> bool:
    if not step.expected_artifacts:
        return False
    return any(str(artifacts.get(key) or "").strip() for key in step.expected_artifacts)


def _step_already_satisfied(step: PlanStep, state: SessionState) -> bool:
    return _step_completed_by_stage(step, state.stage) or _step_completed_by_artifact(step, state.artifacts)


def _derive_todos(plan_steps: list[PlanStep]) -> list[TaskTodo]:
    return [TaskTodo(content=step.title, status=step.status) for step in plan_steps]


def _sync_state_from_plan(state: SessionState) -> None:
    state.todos = _derive_todos(state.plan_steps)
    if any(step.status == "failed" for step in state.plan_steps):
        failed_step = next(step for step in state.plan_steps if step.status == "failed")
        state.current_step = failed_step.title
        state.blocker = failed_step.notes[-1] if failed_step.notes else failed_step.title
        state.next_step_hint = "已自动重规划后续步骤；如仍失败，请先查看该步骤日志。"
        return
    next_step = select_next_plan_step(state)
    state.current_step = next_step.title if next_step is not None else ""
    state.blocker = ""
    state.next_step_hint = next_step.title if next_step is not None else "当前任务已达到本轮完成条件。"


def build_execution_plan(state: SessionState, instruction: str, tool_names: list[str]) -> list[PlanStep]:
    state.spec.target_artifact = infer_target_artifact(
        requirement=instruction or state.spec.requirement,
        simulation_type=state.spec.simulation_type,
        current_target=state.spec.target_artifact,
    )
    if target_satisfied(state.stage, state.spec.target_artifact):
        state.plan_steps = []
        state.plan_id = _plan_id()
        state.plan_attempt = 1
        state.task_summary = _build_summary(state, instruction)
        state.done_criteria = _structure_done_criteria() if target_blocks_sdevice(state.spec.target_artifact) else _full_chain_done_criteria()
        _sync_state_from_plan(state)
        return state.plan_steps
    attempt = 1
    templates = [step for step in _active_templates(state, attempt) if _tool_available(step.tool_name, tool_names)]
    state.plan_steps = [step for step in templates if not _step_already_satisfied(step, state)]
    state.plan_id = _plan_id()
    state.plan_attempt = attempt
    state.task_summary = _build_summary(state, instruction)
    state.done_criteria = _structure_done_criteria() if target_blocks_sdevice(state.spec.target_artifact) else _full_chain_done_criteria()
    _sync_state_from_plan(state)
    return state.plan_steps


def select_next_plan_step(state: SessionState) -> PlanStep | None:
    for step in state.plan_steps:
        if step.status == "in_progress":
            return step
    for step in state.plan_steps:
        if step.status == "pending":
            return step
    return None


def update_plan_step(
    state: SessionState,
    *,
    step_id: str,
    status: str,
    note: str = "",
) -> PlanStep | None:
    for index, step in enumerate(state.plan_steps):
        if step.step_id != step_id:
            continue
        next_step = replace(step)
        next_step.status = status
        if note:
            next_step.notes = [*step.notes, note]
        state.plan_steps[index] = next_step
        _sync_state_from_plan(state)
        return next_step
    return None


def finalize_plan_if_done(state: SessionState) -> None:
    if not target_satisfied(state.stage, state.spec.target_artifact):
        return
    for index, step in enumerate(state.plan_steps):
        if step.status in PLAN_PENDING_STATUSES:
            next_step = replace(step)
            next_step.status = "skipped"
            state.plan_steps[index] = next_step
    _sync_state_from_plan(state)


def replan_failed_tail(state: SessionState, *, failed_step_id: str) -> bool:
    if state.plan_attempt >= 2:
        return False
    failed_index = next((idx for idx, step in enumerate(state.plan_steps) if step.step_id == failed_step_id), None)
    if failed_index is None:
        return False

    failed_step = state.plan_steps[failed_index]
    structure_only = target_blocks_sdevice(state.spec.target_artifact)
    restart_tool = failed_step.tool_name
    if restart_tool in {"check_sde_syntax", "run_sde"}:
        restart_tool = "generate_sde_code"
    elif restart_tool in {"inspect_tdr"}:
        restart_tool = "inspect_tdr"
    elif restart_tool in {"run_svisual_sde_export"}:
        restart_tool = "run_svisual_sde_export"
    elif restart_tool in {"check_sdevice_syntax", "run_sdevice"}:
        restart_tool = "generate_sdevice_code"
    elif restart_tool in {"run_svisual_export", "validate_results"}:
        restart_tool = restart_tool

    next_attempt = state.plan_attempt + 1
    templates = _active_templates(state, next_attempt)
    restart_index = next((idx for idx, step in enumerate(templates) if step.tool_name == restart_tool), 0)
    prefix = [replace(step) for step in state.plan_steps[: failed_index + 1]]
    suffix = templates[restart_index:]
    if structure_only and suffix and suffix[0].tool_name not in {"generate_sde_code", "inspect_tdr", "run_svisual_sde_export"}:
        return False
    state.plan_steps = prefix + suffix
    state.plan_attempt = next_attempt
    state.plan_id = _plan_id()
    _sync_state_from_plan(state)
    return True
