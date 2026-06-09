from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path("/data/yphu/Web-FabGPT/LLM/tcad_agent_core")
sys.path.insert(0, str(ROOT))

from src.core import SessionSpec, SessionState, Targets
from src.task_contract import build_execution_plan, replan_failed_tail, select_next_plan_step


def _make_state(tmp_path: Path, *, requirement: str, simulation_type: str, target_artifact: str) -> SessionState:
    session_dir = tmp_path / "runtime" / "default"
    session_dir.mkdir(parents=True, exist_ok=True)
    return SessionState(
        session_id="default",
        session_dir=session_dir,
        spec=SessionSpec(
            requirement=requirement,
            device_type="nmos",
            simulation_type=simulation_type,
            target_artifact=target_artifact,
            parameters={},
            targets=Targets(),
        ),
        stage="created",
    )


def test_build_execution_plan_for_structure_task_excludes_sdevice_chain(tmp_path: Path):
    state = _make_state(
        tmp_path,
        requirement="请直接生成二维 NMOS 的 Sentaurus SDE 结构代码，并导出结构图。",
        simulation_type="structure_only",
        target_artifact="structure_png",
    )

    steps = build_execution_plan(
        state=state,
        instruction=state.spec.requirement,
        tool_names=[
            "generate_sde_code",
            "check_sde_syntax",
            "run_sde",
            "inspect_tdr",
            "run_svisual_sde_export",
            "generate_sdevice_code",
            "run_sdevice",
        ],
    )

    tool_names = [step.tool_name for step in steps]
    assert tool_names == [
        "generate_sde_code",
        "check_sde_syntax",
        "run_sde",
        "inspect_tdr",
        "run_svisual_sde_export",
    ]
    assert state.plan_id
    assert state.plan_attempt == 1
    assert select_next_plan_step(state).tool_name == "generate_sde_code"


def test_build_execution_plan_resumes_from_existing_stage(tmp_path: Path):
    state = _make_state(
        tmp_path,
        requirement="继续完成结构链路。",
        simulation_type="structure_only",
        target_artifact="structure_png",
    )
    state.stage = "sde_done"
    state.artifacts["mesh"] = str(state.session_dir / "run" / "device_msh.tdr")

    steps = build_execution_plan(
        state=state,
        instruction="继续完成结构链路。",
        tool_names=[
            "generate_sde_code",
            "check_sde_syntax",
            "run_sde",
            "inspect_tdr",
            "run_svisual_sde_export",
        ],
    )

    assert [step.status for step in steps[:3]] == ["completed", "completed", "completed"]
    assert select_next_plan_step(state).tool_name == "inspect_tdr"


def test_build_execution_plan_followup_png_request_upgrades_target_and_resumes(tmp_path: Path):
    state = _make_state(
        tmp_path,
        requirement="先生成结构。",
        simulation_type="structure_only",
        target_artifact="structure",
    )
    state.stage = "sde_done"
    state.artifacts["mesh"] = str(state.session_dir / "run" / "device_msh.tdr")

    steps = build_execution_plan(
        state=state,
        instruction="png图片呢？请导出结构图。",
        tool_names=[
            "generate_sde_code",
            "check_sde_syntax",
            "run_sde",
            "inspect_tdr",
            "run_svisual_sde_export",
        ],
    )

    assert state.spec.target_artifact == "structure_png"
    assert [step.status for step in steps[:3]] == ["completed", "completed", "completed"]
    assert select_next_plan_step(state).tool_name == "inspect_tdr"


def test_replan_failed_tail_preserves_completed_prefix_and_increments_attempt(tmp_path: Path):
    state = _make_state(
        tmp_path,
        requirement="请生成结构并继续仿真。",
        simulation_type="full_chain",
        target_artifact="full_chain",
    )
    build_execution_plan(
        state=state,
        instruction=state.spec.requirement,
        tool_names=[
            "generate_sde_code",
            "check_sde_syntax",
            "run_sde",
            "inspect_tdr",
            "run_svisual_sde_export",
            "generate_sdevice_code",
            "check_sdevice_syntax",
            "run_sdevice",
            "run_svisual_export",
            "validate_results",
        ],
    )
    state.plan_steps[0].status = "completed"
    state.plan_steps[1].status = "failed"

    replanned = replan_failed_tail(state, failed_step_id=state.plan_steps[1].step_id)

    assert replanned is True
    assert state.plan_attempt == 2
    assert state.plan_steps[0].status == "completed"
    assert state.plan_steps[1].status == "failed"
    next_step = select_next_plan_step(state)
    assert next_step is not None
    assert next_step.step_id != state.plan_steps[1].step_id
    assert next_step.tool_name == "generate_sde_code"
