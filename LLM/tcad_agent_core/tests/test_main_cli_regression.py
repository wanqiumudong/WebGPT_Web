from __future__ import annotations
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


ROOT = Path("/data/yphu/TCAD_Agent/code")
sys.path.insert(0, str(ROOT))

import main as main_mod


class _DummyTracer:
    def event(self, *args, **kwargs):
        return None


class _DummyAgent:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.tracer = _DummyTracer()

    def show_state(self):
        return {"stage": "created", "artifacts": {"mesh": "/tmp/demo.tdr"}}

    def show_capabilities(self):
        return {
            "mcp_tools[2]": ["show_state", "describe_tools"],
        }

    def agent_decide_and_execute(self, instruction: str):
        return {"stage": "created", "assistant_reply": instruction.upper()}


def test_run_client_mode_keeps_commands_json_and_dialog_text(monkeypatch, tmp_path: Path):
    inputs = iter(["/tools", "/state", "你好", "/exit"])

    monkeypatch.setattr(main_mod, "TCADAgentSystem", _DummyAgent)
    monkeypatch.setattr(main_mod, "rotate_default_runtime", lambda: None)
    monkeypatch.setattr(main_mod, "_build_line_reader", lambda: lambda: next(inputs))

    buf = StringIO()
    with redirect_stdout(buf):
        exit_code = main_mod.run_client_mode(tmp_path)

    assert exit_code == 0
    text = buf.getvalue()
    assert "TCAD Agent Client" in text
    assert '"mcp_tools[2]"' in text
    assert '"artifacts"' in text
    assert "你好" in text
    assert "{'stage': 'created'" not in text


def test_print_dialog_reply_prefers_assistant_reply(capsys):
    reply = "assistant reply payload"
    main_mod._print_dialog_reply({"assistant_reply": reply, "stage": "validated"})
    captured = capsys.readouterr()
    assert captured.out.strip() == reply


def test_print_dialog_reply_falls_back_to_stage_message(capsys):
    main_mod._print_dialog_reply({"stage": "validated"})
    captured = capsys.readouterr()
    text = captured.out.strip()
    assert "validated" in text
    assert "/state" in text
    assert "已完成本轮处理" in text
