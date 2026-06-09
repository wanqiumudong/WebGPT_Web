from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path("/data/yphu/Web-FabGPT/LLM/tcad_agent_core")
sys.path.insert(0, str(ROOT))

from mcp.service import TcadGatewayMCPService
from web.tcad_web_adapter import create_app


class _DummyTools:
    def describe_tools(self):
        return {"ok": True, "tool_count": 1, "tools": [{"name": "show_state"}]}


class _DummyAgent:
    DEFAULT_SESSION = "default"

    def __init__(self, runtime_root: Path):
        self.runtime_root = runtime_root
        self.mcp_tools = _DummyTools()
        self.assets: dict[str, dict[str, str]] = {}
        self.has_session = False

    def create_session(self, requirement: str):
        self.has_session = True
        return {
            "session_id": self.DEFAULT_SESSION,
            "stage": "created",
            "artifacts": {"project_root": str(self.runtime_root)},
            "notes": [requirement],
        }

    def show_state(self):
        if not self.has_session:
            raise FileNotFoundError("no session")
        return {
            "session_id": self.DEFAULT_SESSION,
            "stage": "created",
            "artifacts": {"project_root": str(self.runtime_root)},
            "notes": [],
        }

    def register_session_asset(self, source_path: str, file_name: str = "", role: str = "auto"):
        resolved_name = file_name or Path(source_path).name
        self.assets[resolved_name] = {
            "file_name": resolved_name,
            "stored_path": source_path,
            "role": role,
        }
        return {"stage": "created", "asset": self.assets[resolved_name]}

    def list_session_assets(self):
        return {"stage": "created", "assets": list(self.assets.values())}

    def delete_session_asset(self, file_name: str):
        removed = self.assets.pop(file_name, None)
        return {"stage": "created", "deleted": removed is not None, "file_name": file_name}

    def agent_decide_and_execute(self, instruction: str, *, event_sink=None, should_abort=None):
        run_dir = self.runtime_root / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        sde_path = run_dir / "sde_dvs.cmd"
        sde_path.write_text("sde", encoding="utf-8")
        if event_sink is not None:
            event_sink({"kind": "tool_start", "tool_name": "generate_sde_code", "stage": "created"})
            event_sink({"kind": "tool_end", "tool_name": "generate_sde_code", "stage": "sde_generated", "ok": True})
            event_sink({"kind": "artifact", "artifact_key": "sde_cmd", "artifact_path": str(sde_path)})
            event_sink({"kind": "assistant_chunk", "chunk": f"processed:{instruction}"})
        return {
            "session_id": self.DEFAULT_SESSION,
            "stage": "validated",
            "assistant_reply": f"processed:{instruction}",
            "artifacts": {"sde_cmd": str(sde_path)},
        }


class _NarratedDummyAgent(_DummyAgent):
    def agent_decide_and_execute(self, instruction: str, *, event_sink=None, should_abort=None):
        run_dir = self.runtime_root / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        sde_path = run_dir / "sde_dvs.cmd"
        sde_path.write_text("sde", encoding="utf-8")
        if event_sink is not None:
            event_sink(
                {
                    "kind": "plan_created",
                    "summary": "先生成结构，再检查语法并执行结构链路。",
                    "plan_id": "plan-1",
                    "plan_attempt": 1,
                    "plan_steps": [
                        {
                            "step_id": "step-1",
                            "title": "生成 SDE 结构代码",
                            "tool_name": "generate_sde_code",
                            "status": "pending",
                        },
                        {
                            "step_id": "step-2",
                            "title": "检查 SDE 语法",
                            "tool_name": "check_sde_syntax",
                            "status": "pending",
                        },
                    ],
                }
            )
            event_sink(
                {
                    "kind": "plan_step_update",
                    "plan_id": "plan-1",
                    "step_id": "step-1",
                    "title": "生成 SDE 结构代码",
                    "tool_name": "generate_sde_code",
                    "status": "in_progress",
                }
            )
            event_sink({"kind": "assistant_chunk", "chunk": "我先生成结构脚本。"})
            event_sink({"kind": "tool_start", "tool_name": "generate_sde_code", "stage": "created"})
            event_sink({"kind": "tool_end", "tool_name": "generate_sde_code", "stage": "sde_generated", "ok": True})
            event_sink({"kind": "assistant_chunk", "chunk": "脚本已经生成，接下来做语法检查。"})
        return {
            "session_id": self.DEFAULT_SESSION,
            "stage": "sde_generated",
            "assistant_reply": "本轮执行已完成。",
            "artifacts": {"sde_cmd": str(sde_path)},
        }


class _StubDemoProvider:
    def list_cases(self, *, limit: int = 8):
        return {
            "source": "stub-demo-provider",
            "cases": [
                {
                    "case_id": "demo-compact",
                    "title": "案例 3 · 紧凑模型构建",
                    "summary": "基于固定 IdVg / IdVd 结果展示参数卡与 Verilog-A 导出。",
                    "prompt": "请基于已有二维 planar nMOS 的 IdVg 和 IdVd 结果整理紧凑模型参数，并导出 Verilog-A。",
                    "device_type": "mosfet",
                    "simulation_type": "compact_model",
                    "capabilities": ["自然语言输入", "电学仿真", "紧凑模型构建"],
                    "artifact_files": ["nmos_demo.va", "parameter_card.json"],
                    "reference_basis": ["mw90_f01_01"],
                    "is_featured": True,
                }
            ][:limit],
        }

    def has_case(self, case_id: str) -> bool:
        return case_id == "demo-compact"

    def run_case(self, *, record, case_id: str, user_message: str):
        run_dir = record.workdir / "run"
        reports_dir = record.workdir / "reports"
        logs_dir = record.workdir / "logs"
        run_dir.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)

        sde_path = run_dir / "demo_nmos.cmd"
        va_path = run_dir / "nmos_demo.va"
        png_path = reports_dir / "fit_overlay.png"
        validation_path = reports_dir / "validation_report.json"

        sde_path.write_text("(sde:clear)\n", encoding="utf-8")
        va_path.write_text("module nmos_demo; endmodule\n", encoding="utf-8")
        png_path.write_bytes(b"fake-png")
        validation_path.write_text(json.dumps({"ok": True, "metrics": {"VTH0": 0.52}}), encoding="utf-8")
        (logs_dir / "debug_trace.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"action": "mcp_tool_done", "payload": {"tool": "generate_sde_code", "stage": "sde_generated"}}),
                    json.dumps({"action": "mcp_tool_done", "payload": {"tool": "build_compact_model", "stage": "validated"}}),
                ]
            ),
            encoding="utf-8",
        )

        state = {
            "stage": "validated",
            "notes": [
                "已整理自然语言需求。",
                "已完成紧凑模型参数整理与 Verilog-A 导出。",
            ],
            "spec": {"requirement": user_message},
            "metrics": {"VTH0": 0.52, "KP": 0.0017},
            "artifacts": {
                "sde_cmd": str(sde_path),
                "compact_model_plot": str(png_path),
                "verilog_a_model": str(va_path),
                "validation_report": str(validation_path),
            },
            "demo_case": {
                "case_id": case_id,
                "title": "案例 3 · 紧凑模型构建",
                "capabilities": ["自然语言输入", "电学仿真", "紧凑模型构建"],
            },
            "demo_panels": [
                {"title": "固定输入", "items": [user_message]},
                {"title": "导出结果", "items": ["参数卡", "Verilog-A 骨架"]},
            ],
        }
        (record.workdir / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

        return {
            "events": [
                {
                    "kind": "plan_created",
                    "summary": "按照固定案例展示自然语言输入、电学结果整理与紧凑模型导出。",
                    "plan_id": "demo-plan",
                    "plan_attempt": 1,
                    "plan_steps": [
                        {
                            "step_id": "step-generate",
                            "title": "生成 SDE 结构代码",
                            "tool_name": "generate_sde_code",
                            "status": "pending",
                        },
                        {
                            "step_id": "step-compact",
                            "title": "整理紧凑模型参数",
                            "tool_name": "build_compact_model",
                            "status": "pending",
                        },
                    ],
                },
                {"kind": "assistant_chunk", "chunk": "我先整理这组固定的器件曲线与结构输入。"},
                {
                    "kind": "tool_start",
                    "tool_name": "generate_sde_code",
                    "stage": "created",
                },
                {
                    "kind": "tool_end",
                    "tool_name": "generate_sde_code",
                    "stage": "sde_generated",
                    "ok": True,
                },
                {
                    "kind": "artifact",
                    "artifact_key": "sde_cmd",
                    "artifact_path": str(sde_path),
                },
                {"kind": "assistant_chunk", "chunk": "结构脚本已经就绪，接下来整理紧凑模型参数。"},
                {
                    "kind": "tool_start",
                    "tool_name": "build_compact_model",
                    "stage": "sde_generated",
                },
                {
                    "kind": "tool_end",
                    "tool_name": "build_compact_model",
                    "stage": "validated",
                    "ok": True,
                },
                {
                    "kind": "artifact",
                    "artifact_key": "verilog_a_model",
                    "artifact_path": str(va_path),
                },
                {
                    "kind": "artifact",
                    "artifact_key": "compact_model_plot",
                    "artifact_path": str(png_path),
                },
                {"kind": "assistant_chunk", "chunk": "参数卡、拟合图和 Verilog-A 骨架都已经整理好了。"},
            ],
            "result": {
                "stage": "validated",
                "assistant_reply": "参数卡、拟合图和 Verilog-A 骨架都已经整理好了。",
                "artifacts": {
                    "sde_cmd": str(sde_path),
                    "compact_model_plot": str(png_path),
                    "verilog_a_model": str(va_path),
                    "validation_report": str(validation_path),
                },
                "metrics": {"VTH0": 0.52, "KP": 0.0017},
                "notes": state["notes"],
            },
        }


class _ScriptedDemoProvider:
    def list_cases(self, *, limit: int = 8):
        return {
            "source": "stub-scripted-provider",
            "cases": [
                {
                    "case_id": "demo-finfet",
                    "title": "三维结构任务",
                    "summary": "先生成结构代码，再在下一轮导出结构图片。",
                    "prompt": "请先生成三维结构代码。",
                    "device_type": "finfet",
                    "simulation_type": "sde_generation",
                    "capabilities": ["自然语言输入", "SDE代码生成", "三维结构检查"],
                    "artifact_files": ["SDE 脚本", "结构图片"],
                    "reference_basis": ["stub-case"],
                    "is_featured": True,
                }
            ][:limit],
        }

    def has_case(self, case_id: str) -> bool:
        return case_id == "demo-finfet"

    def run_case(self, *, record, case_id: str, user_message: str):
        state_path = record.workdir / "state.json"
        prior_state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        artifacts = dict(prior_state.get("artifacts") or {})
        turn_index = int(prior_state.get("demo_turn_index") or 0)

        run_dir = record.workdir / "run"
        reports_dir = record.workdir / "reports"
        logs_dir = record.workdir / "logs"
        run_dir.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)

        if turn_index == 0:
            sde_path = run_dir / "scripted_finfet.cmd"
            sde_path.write_text("(sde:clear)\n", encoding="utf-8")
            artifacts["sde_cmd"] = str(sde_path)
            state = {
                "stage": "sde_checked",
                "notes": ["已完成结构代码与语法检查。"],
                "spec": {"requirement": user_message},
                "metrics": {},
                "artifacts": artifacts,
                "demo_case": {"case_id": case_id, "title": "三维结构任务", "capabilities": ["自然语言输入", "SDE代码生成"]},
                "demo_phase": "structure_ready",
                "demo_turn_index": 1,
            }
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            return {
                "events": [
                    {"kind": "assistant_chunk", "chunk": "我先把结构代码生成出来，并检查语法。"},
                    {"kind": "tool_start", "tool_name": "generate_sde_code", "stage": "created"},
                    {"kind": "tool_end", "tool_name": "generate_sde_code", "stage": "sde_generated", "ok": True},
                    {"kind": "tool_start", "tool_name": "check_sde_syntax", "stage": "sde_generated"},
                    {"kind": "tool_end", "tool_name": "check_sde_syntax", "stage": "sde_checked", "ok": True},
                    {"kind": "artifact", "artifact_key": "sde_cmd", "artifact_path": str(sde_path)},
                ],
                "result": {
                    "stage": "sde_checked",
                    "assistant_reply": "结构代码已经准备好了。",
                    "artifacts": artifacts,
                    "metrics": {},
                    "notes": state["notes"],
                    "demo_phase": "structure_ready",
                    "demo_turn_index": 1,
                },
            }

        png_path = reports_dir / "scripted_finfet.png"
        png_path.write_bytes(b"png")
        artifacts["svisual_png"] = str(png_path)
        state = {
            "stage": "svisual_sde_done",
            "notes": ["已导出结构图片。"],
            "spec": {"requirement": user_message},
            "metrics": {},
            "artifacts": artifacts,
            "demo_case": {"case_id": case_id, "title": "三维结构任务", "capabilities": ["自然语言输入", "SDE代码生成", "三维结构检查"]},
            "demo_phase": "structure_visualized",
            "demo_turn_index": 2,
        }
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "events": [
                {"kind": "assistant_chunk", "chunk": "我继续把结构图片导出来。"},
                {"kind": "tool_start", "tool_name": "run_svisual_sde_export", "stage": "sde_checked"},
                {"kind": "tool_end", "tool_name": "run_svisual_sde_export", "stage": "svisual_sde_done", "ok": True},
                {"kind": "artifact", "artifact_key": "svisual_png", "artifact_path": str(png_path)},
            ],
            "result": {
                "stage": "svisual_sde_done",
                "assistant_reply": "结构图片已经导出了。",
                "artifacts": artifacts,
                "metrics": {},
                "notes": state["notes"],
                "demo_phase": "structure_visualized",
                "demo_turn_index": 2,
            },
        }


def _make_app(tmp_path: Path, *, demo_provider=None):
    service = TcadGatewayMCPService(
        workspace=tmp_path,
        agent_factory=lambda _ws, rr: _DummyAgent(rr),
        max_async_workers=1,
    )
    app = create_app(service=service, workspace=tmp_path, demo_provider=demo_provider)
    app.config["TESTING"] = True
    app.config["TCAD_SMALLTALK_RESPONDER"] = lambda text: f"smalltalk:{text}"
    return app, service


def _make_narrated_app(tmp_path: Path, *, demo_provider=None):
    service = TcadGatewayMCPService(
        workspace=tmp_path,
        agent_factory=lambda _ws, rr: _NarratedDummyAgent(rr),
        max_async_workers=1,
    )
    app = create_app(service=service, workspace=tmp_path, demo_provider=demo_provider)
    app.config["TESTING"] = True
    return app, service


def _iter_sse_events(raw_text: str) -> list[dict]:
    events: list[dict] = []
    for block in raw_text.strip().split("\n\n"):
        line = block.strip()
        if not line.startswith("data: "):
            continue
        events.append(json.loads(line[6:]))
    return events


def _collect_assistant_text(events: list[dict]) -> str:
    return "".join(item.get("chunk", "") for item in events if item.get("kind") == "assistant_chunk")


def test_upload_then_stream_registers_asset_and_emits_events(tmp_path: Path):
    app, service = _make_app(tmp_path)
    client = app.test_client()

    upload = client.post(
        "/uploadFile",
        data={
            "conversation_id": "conv-1",
            "user_id": "user-1",
            "file": (io.BytesIO(b"deck"), "seed.cmd"),
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200

    response = client.post(
        "/stream_generate",
        json={
            "conversation_id": "conv-1",
            "user_id": "user-1",
            "user_message": "继续执行这个 deck",
        },
    )
    assert response.status_code == 200

    events = _iter_sse_events(response.get_data(as_text=True))
    kinds = [item["kind"] for item in events]
    assert "start" in kinds
    assert "tool_start" in kinds
    assert "tool_end" in kinds
    assert "artifact" in kinds
    assert "assistant_chunk" in kinds
    assert kinds[-1] == "done"
    assistant_text = _collect_assistant_text(events)
    assert assistant_text.startswith("processed:")
    assert "继续执行这个 deck" in assistant_text

    record = app.config["TCAD_SESSION_STORE"].get_record("user-1", "conv-1")
    listed = service.call(method="api_tcad_list_assets", instance_id=record.instance_id)
    assert listed["ok"] is True
    assert listed["data"]["assets"][0]["file_name"] == "seed.cmd"


def test_exact_demo_prompt_can_trigger_scripted_case_without_demo_case_id(tmp_path: Path):
    demo_provider = _StubDemoProvider()
    app, _service = _make_app(tmp_path, demo_provider=demo_provider)
    client = app.test_client()

    prompt = demo_provider.list_cases(limit=1)["cases"][0]["prompt"]
    response = client.post(
        "/generate",
        json={
            "conversation_id": "conv-demo-prompt",
            "user_id": "user-demo-prompt",
            "user_message": prompt,
        },
    )

    assert response.status_code == 200
    assert response.json["stage"] == "validated"
    assert response.json["assistant_reply"] == "参数卡、拟合图和 Verilog-A 骨架都已经整理好了。"
    assert any(item["label"] == "Verilog-A 模型" for item in response.json["artifacts"])


def test_delete_file_endpoint_removes_uploaded_asset(tmp_path: Path):
    app, service = _make_app(tmp_path)
    client = app.test_client()

    upload = client.post(
        "/uploadFile",
        data={
            "conversation_id": "conv-2",
            "user_id": "user-2",
            "file": (io.BytesIO(b"plot"), "curve.plt"),
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200

    streamed = client.post(
        "/stream_generate",
        json={
            "conversation_id": "conv-2",
            "user_id": "user-2",
            "user_message": "分析这个文件",
        },
    )
    assert streamed.status_code == 200

    deleted = client.post(
        "/deleteFile",
        json={"conversation_id": "conv-2", "user_id": "user-2", "file_name": "curve.plt"},
    )
    assert deleted.status_code == 200
    assert deleted.json["isDeleted"] is True

    record = app.config["TCAD_SESSION_STORE"].get_record("user-2", "conv-2")
    listed = service.call(method="api_tcad_list_assets", instance_id=record.instance_id)
    assert listed["ok"] is True
    assert listed["data"]["assets"] == []


def test_abort_request_is_isolated_per_request(tmp_path: Path):
    app, _service = _make_app(tmp_path)
    sessions = app.config["TCAD_SESSION_STORE"]
    record = sessions.get_or_create_record("user-3", "conv-3")

    sessions.begin_request(record, "req-1")
    sessions.begin_request(record, "req-2")

    assert sessions.abort_request("req-1") is True
    assert sessions.should_abort("req-1") is True
    assert sessions.should_abort("req-2") is False


def test_generate_prefers_agent_assistant_reply(tmp_path: Path):
    app, _service = _make_app(tmp_path)
    client = app.test_client()

    response = client.post(
        "/generate",
        json={
            "conversation_id": "conv-4",
            "user_id": "user-4",
            "user_message": "请生成一个简单结构",
        },
    )

    assert response.status_code == 200
    assert response.json["assistant_reply"].startswith("processed:")
    assert "本轮执行已完成" not in response.json["assistant_reply"]


def test_stream_generate_structure_clarify_request_skips_tool_execution(tmp_path: Path):
    app, _service = _make_app(tmp_path)
    client = app.test_client()

    response = client.post(
        "/stream_generate",
        json={
            "conversation_id": "conv-clarify",
            "user_id": "user-clarify",
            "user_message": "器件结构设计吧",
        },
    )

    assert response.status_code == 200
    events = _iter_sse_events(response.get_data(as_text=True))
    assert events[0]["kind"] == "start"
    assert events[-1]["kind"] == "done"
    assert "generate_sde_code" not in response.get_data(as_text=True)
    assert "器件结构设计" in _collect_assistant_text(events)


def test_stream_generate_preserves_narrated_chunks_without_done_fallback_duplication(tmp_path: Path):
    app, _service = _make_narrated_app(tmp_path)
    client = app.test_client()

    response = client.post(
        "/stream_generate",
        json={
            "conversation_id": "conv-narrated",
            "user_id": "user-narrated",
            "user_message": "请生成结构",
        },
    )

    assert response.status_code == 200
    events = _iter_sse_events(response.get_data(as_text=True))
    assistant_chunks = [item["chunk"] for item in events if item.get("kind") == "assistant_chunk"]

    assert assistant_chunks == [
        "我先生成结构脚本。",
        "脚本已经生成，接下来做语法检查。",
    ]
    assert "本轮执行已完成。" not in _collect_assistant_text(events)


def test_generate_smalltalk_uses_dynamic_responder_not_hardcoded(tmp_path: Path):
    app, _service = _make_app(tmp_path)
    client = app.test_client()

    response = client.post(
        "/generate",
        json={
            "conversation_id": "conv-smalltalk",
            "user_id": "user-smalltalk",
            "user_message": "你能做什么",
        },
    )

    assert response.status_code == 200


def test_demo_cases_endpoint_uses_configured_demo_provider(tmp_path: Path):
    app, _service = _make_app(tmp_path, demo_provider=_StubDemoProvider())
    client = app.test_client()

    response = client.get("/demo_cases?limit=3")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["source"] == "stub-demo-provider"
    assert payload["cases"][0]["case_id"] == "demo-compact"


def test_stream_generate_demo_case_bypasses_agent_and_materializes_demo_outputs(tmp_path: Path):
    app, _service = _make_app(tmp_path, demo_provider=_StubDemoProvider())
    client = app.test_client()

    response = client.post(
        "/stream_generate",
        json={
            "conversation_id": "conv-demo",
            "user_id": "user-demo",
            "user_message": "请基于已有二维 planar nMOS 的 IdVg 和 IdVd 结果整理紧凑模型参数，并导出 Verilog-A。",
            "demo_case_id": "demo-compact",
        },
    )

    assert response.status_code == 200
    events = _iter_sse_events(response.get_data(as_text=True))
    kinds = [item["kind"] for item in events]
    assert "plan_created" in kinds
    assert "tool_start" in kinds
    assert "tool_end" in kinds
    assert "artifact" in kinds
    assert kinds[-1] == "done"
    assert "processed:" not in response.get_data(as_text=True)

    record = app.config["TCAD_SESSION_STORE"].get_record("user-demo", "conv-demo")
    assert record is not None
    state = json.loads((record.workdir / "state.json").read_text(encoding="utf-8"))
    assert state["demo_case"]["case_id"] == "demo-compact"
    assert (record.workdir / "run" / "nmos_demo.va").exists()
    assert (record.workdir / "reports" / "fit_overlay.png").exists()


def test_stream_generate_scripted_case_continues_without_repeating_demo_case_id(tmp_path: Path):
    app, _service = _make_app(tmp_path, demo_provider=_ScriptedDemoProvider())
    client = app.test_client()

    first = client.post(
        "/stream_generate",
        json={
            "conversation_id": "conv-scripted",
            "user_id": "user-scripted",
            "user_message": "请先生成三维结构代码。",
            "demo_case_id": "demo-finfet",
        },
    )
    assert first.status_code == 200
    first_events = _iter_sse_events(first.get_data(as_text=True))
    assert "generate_sde_code" in [item.get("tool_name") for item in first_events if item.get("kind") == "tool_end"]

    second = client.post(
        "/stream_generate",
        json={
            "conversation_id": "conv-scripted",
            "user_id": "user-scripted",
            "user_message": "请继续帮我导出结构图。",
        },
    )
    assert second.status_code == 200
    second_events = _iter_sse_events(second.get_data(as_text=True))
    second_tools = [item.get("tool_name") for item in second_events if item.get("kind") == "tool_end"]

    assert second_tools == ["run_svisual_sde_export"]
    assert "processed:" not in second.get_data(as_text=True)

    record = app.config["TCAD_SESSION_STORE"].get_record("user-scripted", "conv-scripted")
    assert record is not None
    state = json.loads((record.workdir / "state.json").read_text(encoding="utf-8"))
    meta = app.config["TCAD_SESSION_STORE"].load_meta(record)

    assert state["stage"] == "svisual_sde_done"
    assert {"sde_cmd", "svisual_png"} <= set(state["artifacts"])
    assert meta["demo_case_id"] == "demo-finfet"


def test_stream_generate_smalltalk_uses_dynamic_responder_not_tool_execution(tmp_path: Path):
    app, _service = _make_app(tmp_path)
    client = app.test_client()

    response = client.post(
        "/stream_generate",
        json={
            "conversation_id": "conv-smalltalk-stream",
            "user_id": "user-smalltalk-stream",
            "user_message": "你好",
        },
    )

    assert response.status_code == 200
    events = _iter_sse_events(response.get_data(as_text=True))
    assert events[0]["kind"] == "start"
    assert events[-1]["kind"] == "done"
    assert _collect_assistant_text(events) == "smalltalk:你好"
    assert "generate_sde_code" not in response.get_data(as_text=True)


def test_stream_generate_smalltalk_variant_hii_skips_tool_execution(tmp_path: Path):
    app, _service = _make_app(tmp_path)
    client = app.test_client()

    response = client.post(
        "/stream_generate",
        json={
            "conversation_id": "conv-smalltalk-variant",
            "user_id": "user-smalltalk-variant",
            "user_message": "hii",
        },
    )

    assert response.status_code == 200
    events = _iter_sse_events(response.get_data(as_text=True))
    assert events[0]["kind"] == "start"
    assert events[-1]["kind"] == "done"
    assert _collect_assistant_text(events) == "smalltalk:hii"
    assert "generate_sde_code" not in response.get_data(as_text=True)


def test_stream_generate_emits_plan_events_before_tool_start(tmp_path: Path):
    app, _service = _make_narrated_app(tmp_path)
    client = app.test_client()

    response = client.post(
        "/stream_generate",
        json={
            "conversation_id": "conv-plan",
            "user_id": "user-plan",
            "user_message": "请生成一个二维 NMOS 结构。",
        },
    )

    assert response.status_code == 200
    events = _iter_sse_events(response.get_data(as_text=True))
    kinds = [item["kind"] for item in events]
    assert "plan_created" in kinds
    assert "plan_step_update" in kinds
    assert kinds.index("plan_created") < kinds.index("tool_start")
    assert kinds.index("plan_step_update") < kinds.index("tool_start")


def test_delete_session_runtime_removes_workdir_and_instance(tmp_path: Path):
    app, service = _make_app(tmp_path)
    client = app.test_client()

    upload = client.post(
        "/uploadFile",
        data={
            "conversation_id": "conv-clean",
            "user_id": "user-clean",
            "file": (io.BytesIO(b"deck"), "seed.cmd"),
        },
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200

    generated = client.post(
        "/generate",
        json={
            "conversation_id": "conv-clean",
            "user_id": "user-clean",
            "user_message": "继续执行这个 deck",
        },
    )
    assert generated.status_code == 200

    workdir = tmp_path / "runtime" / "web_sessions" / "user-clean" / "conv-clean"
    assert workdir.exists() is True
    assert service.list_instances()["instances"] != []

    deleted = client.post(
        "/delete_session_runtime",
        json={"conversation_id": "conv-clean", "user_id": "user-clean"},
    )
    assert deleted.status_code == 200
    assert deleted.json["success"] is True
    assert deleted.json["workdirDeleted"] is True
    assert deleted.json["stoppedInstance"] is True
    assert workdir.exists() is False
    assert service.list_instances()["instances"] == []
