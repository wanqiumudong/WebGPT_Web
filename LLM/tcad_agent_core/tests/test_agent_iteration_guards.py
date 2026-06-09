from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path("/data/yphu/TCAD_Agent/code")
sys.path.insert(0, str(ROOT))

from src.agent_system import TCADAgentSystem
from src.core import SessionSpec, SessionState, Targets
from src.failure_taxonomy import classify_failure


class _DummyTracer:
    def event(self, *args, **kwargs):
        return None


class _DummyMCP:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def list_tool_names(self):
        return ["run_svisual_sde_export", "run_bash"]

    def call_tool(self, name: str, **kwargs):
        self.calls.append((name, kwargs))
        return {"session_id": "default", "stage": "svisual_done", "artifacts": {}, "notes": []}


class _DummyLLM:
    def __init__(self, image_answer: str = "图像分析结果"):
        self.image_answer = image_answer
        self.image_calls: list[tuple[str, str]] = []

    def chat_with_image(self, *, question: str, image_path: Path) -> str:
        self.image_calls.append((question, str(image_path)))
        return self.image_answer

    def chat_main(self, *args, **kwargs):
        raise AssertionError("direct image questions should bypass chat_main planning")


class _DummyPlannerLLM:
    def __init__(self, raw_response: str, image_error: Exception | None = None):
        self.skills = type("_Skills", (), {"load": staticmethod(lambda name, default="": "")})()
        self._raw_response = raw_response
        self._image_error = image_error
        self.image_calls: list[tuple[str, str]] = []
        self.main_calls = 0

    def chat_with_image(self, *, question: str, image_path: Path) -> str:
        self.image_calls.append((question, str(image_path)))
        if self._image_error is not None:
            raise self._image_error
        return "image-answer"

    def chat_main(self, messages, verbose: bool = False):
        self.main_calls += 1
        return self._raw_response


def _make_state(tmp_path: Path) -> SessionState:
    session_dir = tmp_path / "runtime" / "default"
    (session_dir / "run").mkdir(parents=True, exist_ok=True)
    (session_dir / "logs").mkdir(parents=True, exist_ok=True)
    (session_dir / "reports").mkdir(parents=True, exist_ok=True)

    return SessionState(
        session_id="default",
        session_dir=session_dir,
        spec=SessionSpec(
            requirement="demo",
            device_type="nmos",
            simulation_type="IdVg",
            parameters={},
            targets=Targets(),
        ),
        stage="svisual_done",
    )


def test_run_operation_prefers_mesh_for_sde_visual_export(tmp_path: Path):
    state = _make_state(tmp_path)
    mesh = state.session_dir / "run" / "sde_result_msh.tdr"
    tdr = state.session_dir / "run" / "IdVg_des.tdr"
    mesh.write_text("mesh", encoding="utf-8")
    tdr.write_text("tdr", encoding="utf-8")
    state.artifacts.update({"mesh": str(mesh), "tdr": str(tdr)})

    agent = object.__new__(TCADAgentSystem)
    agent.tracer = _DummyTracer()
    agent.mcp_tools = _DummyMCP()
    agent.runtime_root = state.session_dir
    agent._load_state = lambda: state

    agent.run_operation("run_svisual_sde_export")

    assert agent.mcp_tools.calls, "tool should be called"
    tool, kwargs = agent.mcp_tools.calls[-1]
    assert tool == "run_svisual_sde_export"
    assert kwargs.get("source_file") == str(mesh)


def test_agent_deduplicates_same_readonly_tool_calls(tmp_path: Path):
    state = _make_state(tmp_path)
    state_file = state.session_dir / "state.json"
    state_file.write_text("{}", encoding="utf-8")

    agent = object.__new__(TCADAgentSystem)
    agent.runtime_root = state.session_dir
    agent.tracer = _DummyTracer()
    agent._load_state = lambda: state
    agent._dump = lambda st, extra=None: {"stage": st.stage, **(extra or {})}
    agent._execution_reply = lambda instruction, st, executed, last: "ok"

    decisions = iter(
        [
            {
                "done": False,
                "next_operation": "run_bash",
                "next_args": {"command": "ls -la", "cwd": "."},
            },
            {
                "done": False,
                "next_operation": "run_bash",
                "next_args": {"command": "ls -la", "cwd": "."},
            },
            {
                "done": True,
                "assistant_reply": "done",
            },
        ]
    )

    agent.decide_next_operation = lambda instruction, executed_history=None: next(decisions)

    calls: list[tuple[str, dict]] = []

    def _run_op(
        op: str,
        args: dict | None = None,
        instruction: str = "",
        reason: str = "",
        assistant_pre: str = "",
        assistant_post_success: str = "",
        assistant_post_failure: str = "",
    ):
        calls.append((op, dict(args or {})))
        return {"stage": state.stage, "_tool_ok": True}

    agent.run_operation = _run_op

    out = agent.agent_decide_and_execute("看图")

    assert len(calls) == 1
    assert calls[0][0] == "run_bash"
    assert out.get("assistant_reply")


def test_decide_next_operation_answers_image_question_directly(tmp_path: Path):
    state = _make_state(tmp_path)
    state.stage = "svisual_sde_done"
    png = state.session_dir / "reports" / "sde_result_msh.png"
    png.write_bytes(b"fake-png")
    state.artifacts["svisual_sde_png"] = str(png)
    (state.session_dir / "state.json").write_text("{}", encoding="utf-8")

    agent = object.__new__(TCADAgentSystem)
    agent.runtime_root = state.session_dir
    agent.tracer = _DummyTracer()
    agent._load_state = lambda: state
    agent._save_state = lambda st: None
    agent.llm = _DummyLLM("这是主模型直接给出的图片说明。")
    agent.mcp_tools = _DummyMCP()
    agent.create_session = lambda instruction: None

    out = agent.decide_next_operation(f"{png} 这是一个什么结构？")

    assert out["done"] is True
    assert out["source"] == "multimodal_direct"
    assert out["reason"] == "direct_multimodal_image_reply"
    assert out["assistant_reply"] == "这是主模型直接给出的图片说明。"
    assert state.artifacts["last_image_file"] == str(png)


def test_decide_next_operation_does_not_short_circuit_for_path_plus_action_request(tmp_path: Path):
    state = _make_state(tmp_path)
    state.stage = "tdr_inspected"
    png = state.session_dir / "reports" / "sde_result_msh.png"
    png.write_bytes(b"fake-png")
    state.artifacts["svisual_sde_png"] = str(png)
    (state.session_dir / "state.json").write_text("{}", encoding="utf-8")

    planner_llm = _DummyPlannerLLM('{"done": true, "assistant_reply": "planner-path"}')

    agent = object.__new__(TCADAgentSystem)
    agent.runtime_root = state.session_dir
    agent.tracer = _DummyTracer()
    agent._load_state = lambda: state
    agent._save_state = lambda st: None
    agent.llm = planner_llm
    agent.mcp_tools = _DummyMCP()
    agent.create_session = lambda instruction: None
    agent._language_only_reply = lambda instruction, _state=None: f"lang:{instruction}"

    out = agent.decide_next_operation(f"{png} 继续运行 SDevice")

    assert out["source"] == "llm"
    assert out["assistant_reply"] == "planner-path"
    assert planner_llm.main_calls == 1
    assert planner_llm.image_calls == []


def test_decide_next_operation_does_not_short_circuit_for_png_failure_question(tmp_path: Path):
    state = _make_state(tmp_path)
    state.stage = "svisual_sde_failed"
    png = state.session_dir / "reports" / "old.png"
    png.write_bytes(b"fake-png")
    state.artifacts["svisual_sde_png"] = str(png)
    (state.session_dir / "state.json").write_text("{}", encoding="utf-8")

    planner_llm = _DummyPlannerLLM('{"done": true, "assistant_reply": "planner-failure"}')

    agent = object.__new__(TCADAgentSystem)
    agent.runtime_root = state.session_dir
    agent.tracer = _DummyTracer()
    agent._load_state = lambda: state
    agent._save_state = lambda st: None
    agent.llm = planner_llm
    agent.mcp_tools = _DummyMCP()
    agent.create_session = lambda instruction: None
    agent._language_only_reply = lambda instruction, _state=None: f"lang:{instruction}"

    out = agent.decide_next_operation("png 导出失败了吗？")

    assert out["source"] == "llm"
    assert out["assistant_reply"] == "planner-failure"
    assert planner_llm.main_calls == 1
    assert planner_llm.image_calls == []


def test_decide_next_operation_falls_back_when_chat_with_image_errors(tmp_path: Path):
    state = _make_state(tmp_path)
    state.stage = "svisual_sde_done"
    png = state.session_dir / "reports" / "sde_result_msh.png"
    png.write_bytes(b"fake-png")
    state.artifacts["svisual_sde_png"] = str(png)
    (state.session_dir / "state.json").write_text("{}", encoding="utf-8")

    planner_llm = _DummyPlannerLLM(
        '{"done": true, "assistant_reply": "planner-after-image-error"}',
        image_error=RuntimeError("multimodal unavailable"),
    )

    agent = object.__new__(TCADAgentSystem)
    agent.runtime_root = state.session_dir
    agent.tracer = _DummyTracer()
    agent._load_state = lambda: state
    agent._save_state = lambda st: None
    agent.llm = planner_llm
    agent.mcp_tools = _DummyMCP()
    agent.create_session = lambda instruction: None
    agent._language_only_reply = lambda instruction, _state=None: f"lang:{instruction}"

    out = agent.decide_next_operation("请看这张图，这是什么结构？")

    assert out["source"] == "llm"
    assert out["assistant_reply"] == "planner-after-image-error"
    assert planner_llm.main_calls == 1
    assert len(planner_llm.image_calls) == 1


def test_decide_next_operation_updates_target_artifact_from_same_llm_payload(tmp_path: Path):
    state = _make_state(tmp_path)
    state.spec.target_artifact = "unspecified"
    (state.session_dir / "state.json").write_text("{}", encoding="utf-8")

    planner_llm = _DummyPlannerLLM(
        '{"done": true, "target_artifact": "structure_png", "assistant_reply": "ok"}'
    )

    agent = object.__new__(TCADAgentSystem)
    agent.runtime_root = state.session_dir
    agent.tracer = _DummyTracer()
    agent._load_state = lambda: state
    agent._save_state = lambda st: None
    agent.llm = planner_llm
    agent.mcp_tools = _DummyMCP()
    agent.create_session = lambda instruction: None
    agent._language_only_reply = lambda instruction, _state=None: f"lang:{instruction}"

    out = agent.decide_next_operation("请导出结构图")

    assert out["done"] is True
    assert state.spec.target_artifact == "structure_png"
    assert planner_llm.main_calls == 1


def test_decide_next_operation_blocks_sdevice_when_target_is_structure_only(tmp_path: Path):
    state = _make_state(tmp_path)
    state.stage = "svisual_sde_done"
    state.spec.target_artifact = "structure_png"
    (state.session_dir / "state.json").write_text("{}", encoding="utf-8")

    planner_llm = _DummyPlannerLLM(
        '{"done": false, "target_artifact": "structure_png", "next_tool": "generate_sdevice_code", "assistant_pre": "继续生成仿真脚本"}'
    )

    agent = object.__new__(TCADAgentSystem)
    agent.runtime_root = state.session_dir
    agent.tracer = _DummyTracer()
    agent._load_state = lambda: state
    agent._save_state = lambda st: None
    agent.llm = planner_llm
    agent.mcp_tools = type("_GuardMCP", (), {"list_tool_names": staticmethod(lambda: ["generate_sdevice_code", "run_bash"])})()
    agent.create_session = lambda instruction: None
    agent._language_only_reply = lambda instruction, _state=None: f"lang:{instruction}"

    out = agent.decide_next_operation("继续")

    assert out["done"] is True
    assert out["reason"] == "target_artifact_guard"
    assert "不会继续推进到 SDevice" in out["assistant_reply"]
    assert planner_llm.main_calls == 1


def test_classify_failure_marks_sdevice_convergence_from_log(tmp_path: Path):
    log_path = tmp_path / "sdevice.log"
    log_path.write_text("Newton failed to converge after timestep cut", encoding="utf-8")

    record = classify_failure(
        stage="sdevice_failed",
        message="solver failed",
        logs={"sdevice": str(log_path)},
    )

    assert record.failure_class == "sdevice_convergence"
    assert record.rollback_stage == "sdevice_generated"
    assert record.suggested_focus
