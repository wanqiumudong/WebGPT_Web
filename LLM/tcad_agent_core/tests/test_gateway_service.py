from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path("/data/yphu/TCAD_Agent/code")
sys.path.insert(0, str(ROOT))

from mcp.service import TcadGatewayMCPService


class _DummyTools:
    def describe_tools(self):
        return {"ok": True, "tool_count": 2, "tools": [{"name": "show_state"}, {"name": "run_sde"}]}


class _DummyAgent:
    DEFAULT_SESSION = "default"

    def __init__(self, runtime_root: Path):
        self.runtime_root = runtime_root
        self.mcp_tools = _DummyTools()
        self.assets: dict[str, dict[str, str]] = {}

    def create_session(self, requirement: str):
        return {"stage": "created", "requirement": requirement}

    def show_state(self):
        return {"stage": "created"}

    def show_capabilities(self):
        return {"mcp_tools[2]": ["show_state", "run_sde"]}

    def decide_next_operation(self, instruction: str):
        return {"done": True, "assistant_reply": f"ok:{instruction}"}

    def run_operation(self, op: str, args: dict | None = None, instruction: str = ""):
        return {"stage": "done", "op": op, "args": args or {}, "instruction": instruction}

    def agent_decide_and_execute(self, instruction: str):
        return {"stage": "validated", "assistant_reply": instruction}

    def generate_sde(self):
        return {"stage": "sde_generated"}

    def check_sde(self):
        return {"stage": "sde_checked"}

    def run_sde(self):
        return {"stage": "sde_done"}

    def run_svisual_sde(self, source_file: str = "", mode: str = "tdr"):
        return {"stage": "svisual_sde_done", "source_file": source_file, "mode": mode}

    def inspect_tdr(self, tdr_filename: str = "sde_result_msh.tdr"):
        return {"stage": "tdr_inspected", "tdr_filename": tdr_filename}

    def generate_sdevice(self):
        return {"stage": "sdevice_generated"}

    def check_sdevice(self):
        return {"stage": "sdevice_checked"}

    def run_sdevice(self):
        return {"stage": "sdevice_done"}

    def run_svisual(self, source_file: str = "", mode: str = "plt"):
        return {"stage": "svisual_done", "source_file": source_file, "mode": mode}

    def validate(self):
        return {"stage": "validated"}

    def run_bash(self, command: str, cwd: str = "", timeout_s: int = 30):
        if command == "sleep":
            time.sleep(0.08)
        return {"stage": "bash_done", "command": command, "cwd": cwd, "timeout_s": timeout_s}

    def tdx_convert(self, command: str, source_file: str, dest_file: str = "", options: list[str] | None = None):
        return {"stage": "tdx_done", "command": command, "source_file": source_file, "dest_file": dest_file, "options": options}

    def tdx_tclcmd(self, tcl_command: str):
        return {"stage": "tdx_tcl_done", "tcl_command": tcl_command}

    def run_svisual_tcl_script(self, **kwargs):
        return {"stage": "svisual_tcl_done", **kwargs}

    def run_svisual_cutline_export(self, **kwargs):
        return {"stage": "cutline_done", **kwargs}

    def run_inspect_script(self, **kwargs):
        return {"stage": "inspect_done", **kwargs}

    def register_session_asset(self, source_path: str, file_name: str = "", role: str = "auto"):
        resolved_name = file_name or Path(source_path).name
        self.assets[resolved_name] = {
            "file_name": resolved_name,
            "source_path": source_path,
            "role": role,
        }
        return {"stage": "created", "asset": self.assets[resolved_name]}

    def list_session_assets(self):
        return {"stage": "created", "assets": list(self.assets.values())}

    def delete_session_asset(self, file_name: str):
        removed = self.assets.pop(file_name, None)
        return {"stage": "created", "deleted": removed is not None, "file_name": file_name}


def _make_service(tmp_path: Path) -> TcadGatewayMCPService:
    return TcadGatewayMCPService(
        workspace=tmp_path,
        agent_factory=lambda _ws, rr: _DummyAgent(rr),
        max_async_workers=1,
    )


def test_gateway_start_list_stop(tmp_path: Path):
    service = _make_service(tmp_path)
    started = service.start_tcad_server(str(tmp_path / "inst1"))
    assert started["ok"] is True
    instance_id = started["instance_id"]

    listed = service.list_instances()
    assert listed["ok"] is True
    assert listed["active_instance_id"] == instance_id
    assert len(listed["instances"]) == 1

    stopped = service.stop_server(instance_id=instance_id)
    assert stopped["ok"] is True
    assert stopped["stopped_instance_id"] == instance_id


def test_gateway_call_and_method_validation(tmp_path: Path):
    service = _make_service(tmp_path)
    started = service.start_tcad_server(str(tmp_path / "inst2"))
    iid = started["instance_id"]

    bad = service.call(method="run_sde", instance_id=iid)
    assert bad["ok"] is False
    assert bad["error"]["code"] == "TCAD-1000"

    methods = service.call(method="api_tcad_list_methods", instance_id=iid)
    assert methods["ok"] is True
    assert "api_tcad_run_sde" in methods["data"]["methods"]

    op = service.call(
        method="api_tcad_run_operation",
        params={"op": "run_sde", "args": {"x": 1}, "instruction": "go"},
        instance_id=iid,
    )
    assert op["ok"] is True
    assert op["data"]["op"] == "run_sde"
    assert op["data"]["args"] == {"x": 1}


def test_gateway_async_flow(tmp_path: Path):
    service = _make_service(tmp_path)
    started = service.start_tcad_server(str(tmp_path / "inst3"))
    iid = started["instance_id"]

    job = service.call_async_start(method="api_tcad_ping", instance_id=iid)
    assert job["ok"] is True

    waited = service.call_async_wait(job_id=job["job_id"], wait_timeout_ms=2000, include_response=True)
    assert waited["ok"] is True
    assert waited["done"] is True
    assert waited["response"]["ok"] is True
    assert waited["response"]["data"]["pong"] is True


def test_gateway_denied_method_and_invalid_return_mode(tmp_path: Path):
    service = _make_service(tmp_path)
    started = service.start_tcad_server(str(tmp_path / "inst4"))
    iid = started["instance_id"]

    service._deny_methods = {"api_tcad_run_bash"}
    denied = service.call(method="api_tcad_run_bash", params={"command": "ls"}, instance_id=iid)
    assert denied["ok"] is False
    assert denied["error"]["code"] == "TCAD-1005"

    bad_mode = service.call(method="api_tcad_ping", return_mode="yaml", instance_id=iid)
    assert bad_mode["ok"] is False
    assert bad_mode["error"]["code"] == "TCAD-1006"


def test_gateway_timeout_and_async_policy(tmp_path: Path):
    service = _make_service(tmp_path)
    started = service.start_tcad_server(str(tmp_path / "inst5"))
    iid = started["instance_id"]

    service._method_default_timeout_ms = {"api_tcad_run_bash": 20}
    timeout = service.call(method="api_tcad_run_bash", params={"command": "sleep"}, instance_id=iid)
    assert timeout["ok"] is False
    assert timeout["error"]["code"] == "TCAD-1004"

    busy = service.call(method="api_tcad_ping", instance_id=iid)
    assert busy["ok"] is False
    assert busy["error"]["code"] == "TCAD-2002"

    time.sleep(0.12)
    ping = service.call(method="api_tcad_ping", instance_id=iid)
    assert ping["ok"] is True

    service._async_allow_all = False
    service._async_enabled_methods = {"api_tcad_ping"}
    blocked = service.call_async_start(method="api_tcad_run_sde", instance_id=iid)
    assert blocked["ok"] is False
    assert blocked["error"]["code"] == "TCAD-1007"


def test_gateway_asset_management_methods(tmp_path: Path):
    service = _make_service(tmp_path)
    started = service.start_tcad_server(str(tmp_path / "inst6"))
    iid = started["instance_id"]

    source = tmp_path / "input.cmd"
    source.write_text("deck", encoding="utf-8")

    registered = service.call(
        method="api_tcad_register_asset",
        params={"source_path": str(source), "file_name": "input.cmd", "role": "sde_cmd"},
        instance_id=iid,
    )
    assert registered["ok"] is True
    assert registered["data"]["asset"]["file_name"] == "input.cmd"
    assert registered["data"]["asset"]["role"] == "sde_cmd"

    listed = service.call(method="api_tcad_list_assets", instance_id=iid)
    assert listed["ok"] is True
    assert listed["data"]["assets"][0]["file_name"] == "input.cmd"

    deleted = service.call(
        method="api_tcad_delete_asset",
        params={"file_name": "input.cmd"},
        instance_id=iid,
    )
    assert deleted["ok"] is True
    assert deleted["data"]["deleted"] is True
