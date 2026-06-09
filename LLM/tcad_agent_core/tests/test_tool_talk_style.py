from __future__ import annotations

import sys
from pathlib import Path
from io import StringIO
from contextlib import redirect_stderr

ROOT = Path("/data/yphu/TCAD_Agent/code")
sys.path.insert(0, str(ROOT))

from src.agent_system import TCADAgentSystem
from src.core import SessionSpec, SessionState, Targets


class _DummyTracer:
    def event(self, *args, **kwargs):
        return None


class _DummyMCP:
    def list_tool_names(self):
        return ["show_state"]

    def call_tool(self, name: str, **kwargs):
        return {"session_id": "default", "stage": "created", "artifacts": {}, "notes": []}


def test_run_operation_uses_model_speech_directly(tmp_path: Path):
    session_dir = tmp_path / "runtime" / "default"
    (session_dir / "run").mkdir(parents=True, exist_ok=True)
    (session_dir / "logs").mkdir(parents=True, exist_ok=True)
    (session_dir / "reports").mkdir(parents=True, exist_ok=True)
    state = SessionState(
        session_id="default",
        session_dir=session_dir,
        spec=SessionSpec(
            requirement="demo",
            device_type="nmos",
            simulation_type="IdVg",
            parameters={},
            targets=Targets(),
        ),
        stage="created",
    )

    agent = object.__new__(TCADAgentSystem)
    agent.tracer = _DummyTracer()
    agent.mcp_tools = _DummyMCP()
    agent._load_state = lambda: state
    pre_message = "pre-message"
    post_message = "post-message"

    buf = StringIO()
    with redirect_stderr(buf):
        agent.run_operation(
            "show_state",
            assistant_pre=pre_message,
            assistant_post_success=post_message,
        )
    text = buf.getvalue()
    assert pre_message in text
    assert post_message in text
    assert "这一步完成了" not in text


def test_run_operation_skips_duplicate_post_message(tmp_path: Path):
    session_dir = tmp_path / "runtime" / "default"
    (session_dir / "run").mkdir(parents=True, exist_ok=True)
    (session_dir / "logs").mkdir(parents=True, exist_ok=True)
    (session_dir / "reports").mkdir(parents=True, exist_ok=True)
    state = SessionState(
        session_id="default",
        session_dir=session_dir,
        spec=SessionSpec(
            requirement="demo",
            device_type="nmos",
            simulation_type="IdVg",
            parameters={},
            targets=Targets(),
        ),
        stage="created",
    )

    agent = object.__new__(TCADAgentSystem)
    agent.tracer = _DummyTracer()
    agent.mcp_tools = _DummyMCP()
    agent._load_state = lambda: state
    repeated_message = "same-message"

    buf = StringIO()
    with redirect_stderr(buf):
        agent.run_operation(
            "show_state",
            assistant_pre=repeated_message,
            assistant_post_success=repeated_message,
        )
    text = buf.getvalue()
    assert text.count(repeated_message) == 1


def test_run_operation_emits_tool_events(tmp_path: Path):
    session_dir = tmp_path / "runtime" / "default"
    (session_dir / "run").mkdir(parents=True, exist_ok=True)
    (session_dir / "logs").mkdir(parents=True, exist_ok=True)
    (session_dir / "reports").mkdir(parents=True, exist_ok=True)
    state = SessionState(
        session_id="default",
        session_dir=session_dir,
        spec=SessionSpec(
            requirement="demo",
            device_type="nmos",
            simulation_type="IdVg",
            parameters={},
            targets=Targets(),
        ),
        stage="created",
    )

    class _EventMCP(_DummyMCP):
        def call_tool(self, name: str, **kwargs):
            return {
                "session_id": "default",
                "stage": "sde_done",
                "artifacts": {"mesh": str(session_dir / "run" / "sde_result_msh.tdr")},
                "notes": [],
            }

    agent = object.__new__(TCADAgentSystem)
    agent.tracer = _DummyTracer()
    agent.mcp_tools = _EventMCP()
    agent._load_state = lambda: state

    events: list[dict] = []
    agent.run_operation("show_state", event_sink=events.append)

    assert events[0]["kind"] == "tool_start"
    assert events[0]["tool_name"] == "show_state"
    assert events[1]["kind"] == "tool_end"
