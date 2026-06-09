from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path("/data/yphu/Web-FabGPT/LLM/tcad_agent_core")
sys.path.insert(0, str(ROOT))

from src.agent_system import TCADAgentSystem
from src.core import SessionSpec, SessionState, Targets
from src.task_spec import infer_target_artifact


class _DummyTracer:
    def event(self, *args, **kwargs):
        return None


def test_infer_target_artifact_prefers_structure_png_for_sde_only_request():
    target = infer_target_artifact(
        requirement="请直接给出 Sentaurus SDE Scheme 脚本，只输出纯 SDE 代码。",
        simulation_type="unspecified",
        current_target="unspecified",
    )
    assert target == "structure_png"


def test_infer_target_artifact_allows_followup_upgrade_to_png():
    target = infer_target_artifact(
        requirement="png图片呢？给我结构图。",
        simulation_type="structure_only",
        current_target="structure",
    )
    assert target == "structure_png"


def test_agent_stops_after_structure_artifacts_ready(tmp_path: Path):
    session_dir = tmp_path / "runtime" / "default"
    (session_dir / "run").mkdir(parents=True, exist_ok=True)
    (session_dir / "logs").mkdir(parents=True, exist_ok=True)
    (session_dir / "reports").mkdir(parents=True, exist_ok=True)
    (session_dir / "state.json").write_text("{}", encoding="utf-8")

    state = SessionState(
        session_id="default",
        session_dir=session_dir,
        spec=SessionSpec(
            requirement="请直接给出 Sentaurus SDE Scheme 脚本，只输出纯 SDE 代码。",
            device_type="sic_diode",
            simulation_type="structure_only",
            target_artifact="structure",
            parameters={},
            targets=Targets(),
        ),
        stage="created",
    )

    agent = object.__new__(TCADAgentSystem)
    agent.runtime_root = session_dir
    agent.tracer = _DummyTracer()
    agent._load_state = lambda: state
    agent._dump = lambda st, extra=None: {"stage": st.stage, **(extra or {})}
    agent._execution_reply = lambda instruction, st, executed, last: "done"

    stages = iter(["sde_generated", "sde_checked", "sde_done"])
    calls: list[str] = []

    def _run_op(
        op: str,
        args: dict | None = None,
        instruction: str = "",
        reason: str = "",
        assistant_pre: str = "",
        assistant_post_success: str = "",
        assistant_post_failure: str = "",
        event_sink=None,
    ):
        calls.append(op)
        state.stage = next(stages)
        return {"stage": state.stage, "_tool_ok": True}

    agent.run_operation = _run_op

    out = agent.agent_decide_and_execute("生成一个结构")

    assert calls == [
        "generate_sde_code",
        "check_sde_syntax",
        "run_sde",
    ]
    assert out["stage"] == "sde_done"


def test_agent_structure_png_followup_continues_until_visualization(tmp_path: Path):
    session_dir = tmp_path / "runtime" / "default"
    (session_dir / "run").mkdir(parents=True, exist_ok=True)
    (session_dir / "logs").mkdir(parents=True, exist_ok=True)
    (session_dir / "reports").mkdir(parents=True, exist_ok=True)
    (session_dir / "state.json").write_text("{}", encoding="utf-8")

    state = SessionState(
        session_id="default",
        session_dir=session_dir,
        spec=SessionSpec(
            requirement="先生成结构，后续如果用户要求图片则继续导出结构图。",
            device_type="nmos",
            simulation_type="structure_only",
            target_artifact="structure",
            parameters={},
            targets=Targets(),
        ),
        stage="sde_done",
    )
    state.artifacts["mesh"] = str(session_dir / "run" / "device_msh.tdr")

    agent = object.__new__(TCADAgentSystem)
    agent.runtime_root = session_dir
    agent.tracer = _DummyTracer()
    agent._load_state = lambda: state
    agent._dump = lambda st, extra=None: {"stage": st.stage, **(extra or {})}
    agent._execution_reply = lambda instruction, st, executed, last: "done"

    stages = iter(["tdr_inspected", "svisual_sde_done"])
    calls: list[str] = []

    def _run_op(
        op: str,
        args: dict | None = None,
        instruction: str = "",
        reason: str = "",
        assistant_pre: str = "",
        assistant_post_success: str = "",
        assistant_post_failure: str = "",
        event_sink=None,
    ):
        calls.append(op)
        state.stage = next(stages)
        return {"stage": state.stage, "_tool_ok": True}

    agent.run_operation = _run_op

    out = agent.agent_decide_and_execute("png图片呢？请导出结构图。")

    assert state.spec.target_artifact == "structure_png"
    assert calls == [
        "inspect_tdr",
        "run_svisual_sde_export",
    ]
    assert out["stage"] == "svisual_sde_done"
