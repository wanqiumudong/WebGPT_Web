from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path("/data/yphu/Web-FabGPT/LLM/tcad_agent_core")
sys.path.insert(0, str(ROOT))

from mcp.service import TcadGatewayMCPService
from mcp.tool_service import MCPToolService
from src.core import SessionSpec, SessionState, Targets
from src.shared_contracts import MANIFEST_VERSION, REGISTRY_VERSION, emit_run_manifest


class _DummyTracer:
    def event(self, *args, **kwargs):
        return None


class _DummyTools:
    def describe_tools(self):
        return {"ok": True, "tool_count": 1, "tools": [{"name": "show_state"}]}


class _DummyAgent:
    DEFAULT_SESSION = "default"

    def __init__(self, runtime_root: Path):
        self.runtime_root = runtime_root
        self.mcp_tools = _DummyTools()

    def show_state(self):
        return {"stage": "created"}

    def create_session(self, requirement: str):
        return {"stage": "created", "requirement": requirement}

    def run_bash(self, command: str, cwd: str = "", timeout_s: int = 30):
        if command == "sleep":
            time.sleep(0.05)
        return {"stage": "bash_done", "command": command}


def test_build_registry_via_tool_service():
    def _noarg_tool():
        return {"stage": "sde_generated"}

    service = object.__new__(MCPToolService)
    service.tracer = _DummyTracer()
    service._dispatch = lambda: {"generate_sde_code": _noarg_tool, "show_state": lambda: {"stage": "created"}}  # type: ignore[method-assign]
    service._deny_tools = {"generate_sde_code"}

    out = service.describe_tools()
    assert out["registry_version"] == REGISTRY_VERSION
    items = {item["name"]: item for item in out["tools"]}
    assert items["generate_sde_code"]["enabled"] is False
    assert items["generate_sde_code"]["input_schema"]["type"] == "object"
    assert items["show_state"]["output_schema"]["type"] == "object"


def test_emit_run_manifest_from_core_state(tmp_path: Path):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    artifact_path = runtime_root / "reports" / "structure.png"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text("png", encoding="utf-8")

    state = SessionState(
        session_id="default",
        session_dir=runtime_root,
        spec=SessionSpec(
            requirement="生成一个结构",
            device_type="mosfet",
            simulation_type="structure_only",
            target_artifact="structure_png",
            parameters={"Lg": 0.1},
            targets=Targets(),
        ),
        stage="svisual_sde_done",
        artifacts={"svisual_png": str(artifact_path)},
        metrics={"node_count": 12.0},
        notes=["ok"],
    )

    manifest_path = emit_run_manifest(runtime_root, state)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["version"] == MANIFEST_VERSION
    assert payload["session"]["target_artifact"] == "structure_png"
    assert payload["artifacts"][0]["role"] == "svisual_png"
    assert payload["metrics"]["node_count"] == 12.0


def test_gateway_async_job_record_on_core(tmp_path: Path):
    service = TcadGatewayMCPService(
        workspace=tmp_path,
        agent_factory=lambda _ws, rr: _DummyAgent(rr),
        max_async_workers=1,
    )
    started = service.start_tcad_server(str(tmp_path / "inst"))
    iid = started["instance_id"]

    job = service.call_async_start(method="api_tcad_ping", instance_id=iid)
    assert job["ok"] is True
    assert job["job_record"]["status"] in {"submitted", "running"}

    waited = service.call_async_wait(job_id=job["job_id"], wait_timeout_ms=1000, include_response=True)
    assert waited["ok"] is True
    assert waited["done"] is True
    assert waited["job_record"]["status"] == "completed"
    assert waited["job_record"]["response_ok"] is True
