from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path("/data/yphu/TCAD_Agent/code")
sys.path.insert(0, str(ROOT))

from mcp.tool_service import MCPToolService


class _DummyTracer:
    def event(self, *args, **kwargs):
        return None


def _make_service(dispatch: dict):
    service = object.__new__(MCPToolService)
    service.tracer = _DummyTracer()
    service._dispatch = lambda: dispatch  # type: ignore[method-assign]
    service._deny_tools = set()
    return service


def test_call_tool_ignores_unexpected_kwargs_for_noarg_tool():
    called = {"ok": False}

    def _noarg_tool():
        called["ok"] = True
        return {"stage": "sde_generated"}

    service = _make_service({"generate_sde_code": _noarg_tool})
    out = service.call_tool("generate_sde_code", parameters={"foo": 1}, extra="x")
    assert out["stage"] == "sde_generated"
    assert called["ok"] is True


def test_call_tool_unwraps_parameters_wrapper_for_expected_fields():
    captured: dict[str, str] = {}

    def _export_tool(source_file: str = "", mode: str = "tdr"):
        captured["source_file"] = source_file
        captured["mode"] = mode
        return {"stage": "svisual_sde_done"}

    service = _make_service({"run_svisual_sde_export": _export_tool})
    out = service.call_tool(
        "run_svisual_sde_export",
        parameters={"source_file": "/tmp/a.tdr", "mode": "tdr"},
    )
    assert out["stage"] == "svisual_sde_done"
    assert captured == {"source_file": "/tmp/a.tdr", "mode": "tdr"}


def test_call_tool_keeps_valid_kwargs_and_drops_unknown():
    captured: dict[str, str] = {}

    def _bash_tool(command: str, cwd: str = "", timeout_s: int = 30):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["timeout_s"] = str(timeout_s)
        return {"stage": "bash_done"}

    service = _make_service({"run_bash": _bash_tool})
    out = service.call_tool("run_bash", command="ls", cwd="/tmp", timeout_s=5, unknown=1)
    assert out["stage"] == "bash_done"
    assert captured == {"command": "ls", "cwd": "/tmp", "timeout_s": "5"}


def test_call_tool_respects_deny_list():
    def _noarg_tool():
        return {"stage": "sde_generated"}

    service = _make_service({"generate_sde_code": _noarg_tool})
    service._deny_tools = {"generate_sde_code"}

    with pytest.raises(PermissionError):
        service.call_tool("generate_sde_code")


def test_describe_tools_reports_enabled_flag():
    def _noarg_tool():
        return {"stage": "sde_generated"}

    service = _make_service({"generate_sde_code": _noarg_tool, "show_state": lambda: {"stage": "created"}})
    service._deny_tools = {"generate_sde_code"}

    out = service.describe_tools()
    assert out["ok"] is True
    assert "generate_sde_code" in out["denied_tools"]
    items = {item["name"]: item for item in out["tools"]}
    assert items["generate_sde_code"]["enabled"] is False
    assert items["show_state"]["enabled"] is True
