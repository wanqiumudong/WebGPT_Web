from __future__ import annotations

from datetime import datetime
from dataclasses import replace
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import queue
import re
import shutil
import sys
import threading
from typing import Any
from urllib.parse import quote
import uuid

from flask import Flask, Response, abort, jsonify, request, send_file, stream_with_context
from flask_cors import CORS
import requests

THIS_DIR = Path(__file__).resolve().parent
ROOT = THIS_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp.service import TcadGatewayMCPService
from src.llm_engine import OpenAICompatClient, build_config
from web.demo_provider import TcadDemoProvider
from web.presentation_data import (
    build_artifact_preview,
    build_brief_summary,
    build_reference_preview,
    build_session_export,
    build_session_summary,
    build_validation_summary,
    build_workspace_manifest,
    load_state_payload,
)
from web.session_store import WebSessionStore
ARTIFACT_LABELS = {
    "sde_cmd": "SDE 脚本",
    "sdevice_cmd": "SDevice 脚本",
    "mesh": "网格文件",
    "bnd": "边界文件",
    "plot": "仿真结果",
    "tdr": "TDR 数据",
    "tdr_info_report": "TDR 信息报告",
    "validation_report": "验证报告",
    "svisual_png": "结构图片",
    "svisual_sde_png": "结构图片",
    "svisual_doping_png": "掺杂分布图",
    "svisual_curve_txt": "曲线数据",
    "plot_transfer": "Id-Vg 曲线",
    "plot_output": "Id-Vd 曲线",
    "plot_breakdown": "BV 曲线",
    "plot_cv": "C-V 曲线",
    "compact_model_plot": "拟合对比图",
    "compact_model_card": "参数卡",
    "compact_model_report": "参数提取摘要",
    "verilog_a_model": "Verilog-A 模型",
}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
STREAM_SEGMENT_RE = re.compile(r".{1,48}(?:[。！？!?；;，,、：:]|\s|$)|.{1,48}", re.DOTALL)


def create_app(
    *,
    service: TcadGatewayMCPService | None = None,
    workspace: Path | None = None,
    demo_provider: TcadDemoProvider | None = None,
) -> Flask:
    resolved_workspace = (workspace or Path(__file__).resolve().parent.parent).resolve()
    gateway = service or TcadGatewayMCPService.from_env()
    sessions = WebSessionStore(workspace=resolved_workspace, service=gateway)
    provider = demo_provider or TcadDemoProvider(workspace=resolved_workspace)
    app = Flask(__name__)
    CORS(app)
    max_concurrent = max(1, int(os.environ.get("WEB_FABGPT_TCAD_MAX_CONCURRENT", "2")))
    app.config["TCAD_WORKSPACE"] = str(resolved_workspace)
    app.config["TCAD_SESSION_STORE"] = sessions
    app.config["TCAD_GATEWAY_SERVICE"] = gateway
    app.config["TCAD_DEMO_PROVIDER"] = provider
    app.config["TCAD_CONCURRENCY_GATE"] = threading.BoundedSemaphore(max_concurrent)
    app.config["TCAD_MAX_CONCURRENT"] = max_concurrent

    def _payload() -> dict[str, Any]:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def _conversation_id(data: dict[str, Any]) -> str:
        value = data.get("conversation_id") or data.get("session_id")
        return sessions.normalize_conversation_id(value)

    def _user_id(data: dict[str, Any]) -> str:
        value = data.get("user_id") or data.get("username") or data.get("user")
        return sessions.normalize_user_id(value)

    def _message_text(data: dict[str, Any]) -> str:
        text = data.get("user_message") or data.get("message") or data.get("content") or ""
        return str(text).strip()

    def _demo_case_id(data: dict[str, Any]) -> str:
        value = data.get("demo_case_id") or data.get("demoCaseId") or ""
        return str(value).strip()

    def _normalize_demo_prompt(text: str) -> str:
        return " ".join(str(text or "").strip().split())

    def _match_demo_case_id_from_prompt(user_message: str) -> str:
        normalized_message = _normalize_demo_prompt(user_message)
        if not normalized_message:
            return ""
        provider = app.config["TCAD_DEMO_PROVIDER"]
        listed = provider.list_cases(limit=12)
        for item in listed.get("cases", []) if isinstance(listed, dict) else []:
            case_id = str(item.get("case_id") or "").strip()
            prompt = _normalize_demo_prompt(item.get("prompt") or "")
            if case_id and prompt and normalized_message == prompt and provider.has_case(case_id):
                return case_id
        return ""

    def _resolve_effective_demo_case_id(record: Any, requested_case_id: str, user_message: str) -> str:
        normalized_requested = str(requested_case_id or "").strip()
        provider = app.config["TCAD_DEMO_PROVIDER"]
        if normalized_requested:
            if not provider.has_case(normalized_requested):
                raise KeyError(f"unknown demo case: {normalized_requested}")
            return normalized_requested
        meta = sessions.load_meta(record)
        stored_case_id = str(meta.get("demo_case_id") or "").strip()
        if stored_case_id and provider.has_case(stored_case_id):
            return stored_case_id
        return _match_demo_case_id_from_prompt(user_message)

    structure_clarify_pattern = re.compile(
        r"^(器件结构设计|结构设计|先做结构设计|先做器件结构设计|先做结构|做结构吧|结构吧)([吧呀啊呢]*)$",
        re.IGNORECASE,
    )
    simulation_clarify_pattern = re.compile(
        r"^(电学仿真分析|电学仿真|仿真分析|先做仿真|做仿真吧|仿真吧)([吧呀啊呢]*)$",
        re.IGNORECASE,
    )

    def _normalize_smalltalk(text: str) -> str:
        normalized = str(text or "").strip().lower()
        normalized = re.sub(r"[\s，。！？!?,、；;：:\"'“”‘’（）()【】\[\]<>《》]+", "", normalized)
        return normalized

    smalltalk_phrases = {
        "你好",
        "您好",
        "嗨",
        "hello",
        "hi",
        "你是谁",
        "介绍一下你自己",
        "自我介绍",
        "你能做什么",
        "你能生成什么",
        "你可以做什么",
        "whoareyou",
        "whatcanyoudo",
        "你好你是谁",
        "您好你是谁",
        "你好你能做什么",
        "您好你能做什么",
        "你好你可以做什么",
        "您好你可以做什么",
    }

    def _looks_like_smalltalk_variant(normalized: str) -> bool:
        if not normalized or len(normalized) > 8 or re.search(r"\d", normalized):
            return False
        short_phrases = [phrase for phrase in smalltalk_phrases if len(phrase) <= 8]
        return any(SequenceMatcher(None, normalized, phrase).ratio() >= 0.8 for phrase in short_phrases)

    def _is_smalltalk_request(text: str) -> bool:
        raw = str(text or "").strip()
        if not raw or len(raw) > 80:
            return False
        normalized = _normalize_smalltalk(raw)
        if normalized in smalltalk_phrases:
            return True
        return _looks_like_smalltalk_variant(normalized)

    def _smalltalk_reply(text: str) -> str:
        responder = app.config.get("TCAD_SMALLTALK_RESPONDER")
        if callable(responder):
            return str(responder(text)).strip()
        cfg = replace(build_config(), stream=False, max_tokens=512, temperature=0.3, timeout_s=60, total_timeout_s=60)
        client = OpenAICompatClient(cfg)
        system_prompt = (
            "你是一个面向Sentaurus TCAD的中文助手。"
            "当前用户只是问候、身份介绍或能力介绍，不要调用工具，不要进入工程执行流程，"
            "也不要要求用户立刻补一长串参数。请用自然、简洁、专业的中文回答，2到4句即可。"
            "如果用户在问你能做什么，就概括你能辅助的TCAD工作类型。"
        )
        reply = client.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": str(text or "").strip()},
            ],
            verbose=False,
        )
        return str(reply).strip()

    def _is_structure_clarify_request(text: str) -> bool:
        normalized = str(text or "").strip()
        if not normalized or len(normalized) > 24:
            return False
        return bool(structure_clarify_pattern.match(normalized))

    def _is_simulation_clarify_request(text: str) -> bool:
        normalized = str(text or "").strip()
        if not normalized or len(normalized) > 24:
            return False
        return bool(simulation_clarify_pattern.match(normalized))

    def _clarify_reply(text: str) -> str:
        normalized = str(text or "").strip()
        if _is_structure_clarify_request(normalized):
            return (
                "好的，我们先做器件结构设计。请告诉我你想构建的器件类型"
                "（如 MOSFET、FinFET、HEMT、二极管、BJT/HBT），以及你已经确定的关键约束，"
                "例如维度（2D/3D）、材料、主要尺寸、接触名称、掺杂方式和网格要求。"
            )
        return (
            "好的，我们先看电学仿真分析。请告诉我你现在已有的结构或输入文件，"
            "以及希望得到的结果类型，例如 Id-Vg、Id-Vd、击穿、C-V、验证报告或参数提取。"
        )

    def _sse(payload: dict[str, Any]) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def _iter_stream_segments(text: str) -> list[str]:
        content = str(text or "").strip()
        if not content:
            return []
        segments = [segment.strip() for segment in STREAM_SEGMENT_RE.findall(content) if segment.strip()]
        return segments or [content]

    def _artifact_download_path(record: Any, artifact_key: str) -> str:
        return "/artifacts/{user_id}/{conversation_id}/{artifact_key}".format(
            user_id=quote(record.user_id, safe=""),
            conversation_id=quote(record.conversation_id, safe=""),
            artifact_key=quote(artifact_key, safe=""),
        )

    def _workspace_download_path(record: Any, relative_path: str) -> str:
        encoded_path = quote(relative_path, safe="")
        return "/workspace_file/{user_id}/{conversation_id}?path={path}".format(
            user_id=quote(record.user_id, safe=""),
            conversation_id=quote(record.conversation_id, safe=""),
            path=encoded_path,
        )

    def _resolve_workspace_relative(record: Any, relative_path: str) -> Path | None:
        normalized = str(relative_path or "").strip().lstrip("/")
        if not normalized:
            return None
        try:
            resolved = (record.workdir / normalized).resolve()
        except OSError:
            return None
        try:
            resolved.relative_to(record.workdir.resolve())
        except ValueError:
            return None
        return resolved if resolved.exists() else None

    def _public_artifact(record: Any, artifact_key: str, artifact_path: str) -> dict[str, Any] | None:
        if artifact_key not in ARTIFACT_LABELS:
            return None
        path = Path(artifact_path).expanduser().resolve()
        if not path.exists():
            return None
        try:
            path.relative_to(record.workdir.resolve())
        except ValueError:
            return None
        suffix = path.suffix.lower()
        return {
            "key": artifact_key,
            "label": ARTIFACT_LABELS.get(artifact_key, artifact_key),
            "file_name": path.name,
            "download_path": _artifact_download_path(record, artifact_key),
            "is_image": suffix in IMAGE_SUFFIXES,
            "file_type": suffix.lstrip(".") or "file",
        }

    def _collect_public_artifacts(record: Any, artifacts: dict[str, Any]) -> list[dict[str, Any]]:
        preferred_order = [
            "sde_cmd",
            "sdevice_cmd",
            "svisual_png",
            "svisual_sde_png",
            "svisual_doping_png",
            "mesh",
            "plot",
            "plot_transfer",
            "plot_output",
            "plot_cv",
            "compact_model_plot",
            "compact_model_card",
            "compact_model_report",
            "verilog_a_model",
            "tdr_info_report",
            "validation_report",
            "tdr",
            "bnd",
            "svisual_curve_txt",
        ]
        collected: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for key in preferred_order:
            raw_path = artifacts.get(key)
            if not raw_path:
                continue
            resolved_key = str(Path(str(raw_path)).expanduser().resolve())
            if resolved_key in seen_paths:
                continue
            public = _public_artifact(record, key, str(raw_path))
            if public is not None:
                collected.append(public)
                seen_paths.add(resolved_key)
        return collected

    def _load_record_artifacts(record: Any) -> dict[str, Any]:
        state_file = record.workdir / "state.json"
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
                artifacts = state.get("artifacts", {})
                if isinstance(artifacts, dict):
                    return artifacts
            except (OSError, json.JSONDecodeError):
                pass
        if record.instance_id:
            state = gateway.call(method="api_tcad_show_state", instance_id=record.instance_id)
            payload = state.get("data", {}) if isinstance(state, dict) else {}
            artifacts = payload.get("artifacts", {}) if isinstance(payload, dict) else {}
            if isinstance(artifacts, dict):
                return artifacts
        return {}

    def _record_for_request(user_id: str, conversation_id: str) -> Any:
        return sessions.get_record(user_id, conversation_id) or sessions.get_or_create_record(user_id, conversation_id)

    def _fallback_user_reply(result: dict[str, Any], public_artifacts: list[dict[str, Any]]) -> str:
        stage = str(result.get("stage", "") or "")
        metrics = result.get("metrics", {}) or {}
        notes = result.get("notes", []) or []
        reference_summary = ""
        artifacts = result.get("artifacts", {}) if isinstance(result.get("artifacts"), dict) else {}
        last_failure_class = str(artifacts.get("last_failure_class") or "").strip()
        reference_candidates = str(artifacts.get("reference_candidates") or "").strip()
        if reference_candidates:
            try:
                payload = json.loads(Path(reference_candidates).read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    reference_summary = str(payload.get("summary_note") or "").strip()
            except (OSError, json.JSONDecodeError):
                reference_summary = ""
        if stage.endswith("failed"):
            last_note = str(notes[-1]) if notes else "某一步执行失败"
            if last_failure_class == "sde_geometry_failure":
                lines = ["本轮执行未完全成功：SDE 代码已生成并通过语法检查，但结构执行在几何构造阶段失败。"]
            elif last_failure_class == "sde_runtime_failure":
                lines = ["本轮执行未完全成功：SDE 代码已生成并通过语法检查，但执行阶段失败。"]
            elif last_failure_class == "sde_syntax":
                lines = ["本轮执行未完全成功：SDE 代码已生成，但语法检查未通过。"]
            elif last_failure_class == "llm_generation_failure":
                lines = ["本轮执行未完全成功：代码生成阶段没有成功拿到可执行结果。"]
            else:
                lines = [f"本轮执行未完全成功：{last_note}"]
            if last_note and all(last_note not in line for line in lines):
                lines.append(f"失败摘要：{last_note}")
            if reference_summary:
                lines.append(f"本轮已启用参考增强：{reference_summary}。")
            if public_artifacts:
                labels = "、".join(item["label"] for item in public_artifacts[:4])
                lines.append(f"已保留可查看的中间产物：{labels}。")
            lines.append("建议先查看失败日志或中间产物，再决定是否继续重跑。")
            return "\n".join(lines)

        lines = ["本轮执行已推进当前任务。"]
        if reference_summary:
            lines.append(f"本轮参考了：{reference_summary}。")
        if public_artifacts:
            labels = "、".join(item["label"] for item in public_artifacts[:4])
            lines.append(f"关键产物：{labels}。")
        metric_line = ", ".join(
            f"{k}={v:.3g}" for k, v in metrics.items() if isinstance(v, (int, float))
        )
        if metric_line:
            lines.append(f"指标：{metric_line}")
        return "\n".join(lines)

    def _build_user_reply(result: dict[str, Any], public_artifacts: list[dict[str, Any]]) -> str:
        assistant_reply = str(result.get("assistant_reply") or "").strip()
        if assistant_reply:
            return assistant_reply
        return _fallback_user_reply(result, public_artifacts)

    def _run_demo_case(*, record: Any, user_message: str, case_id: str) -> dict[str, Any]:
        demo = app.config["TCAD_DEMO_PROVIDER"]
        if not demo.has_case(case_id):
            raise KeyError(f"unknown demo case: {case_id}")
        return demo.run_case(record=record, case_id=case_id, user_message=user_message)

    def _llm_health() -> dict[str, Any]:
        llm_base_url = str(os.environ.get("TCAD_LLM_BASE_URL") or os.environ.get("TCAD_WEB_LLM_BASE_URL") or "https://api.siliconflow.cn/v1").rstrip("/")
        if llm_base_url.endswith("/chat/completions"):
            llm_base_url = llm_base_url[: -len("/chat/completions")]
        models_url = f"{llm_base_url}/models"
        api_key = os.environ.get("TCAD_LLM_API_KEY", "").strip()
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            response = requests.get(models_url, headers=headers, timeout=2.0)
            return {"reachable": response.ok, "status_code": response.status_code, "url": models_url}
        except requests.RequestException as exc:
            return {"reachable": False, "error": str(exc), "url": models_url}

    def _binary_health() -> dict[str, Any]:
        required = ["sde", "sdevice", "svisual", "tdx", "inspect"]
        return {name: shutil.which(name) for name in required}

    def _ensure_session(record: Any, user_message: str) -> None:
        sessions.ensure_instance(record)
        state = gateway.call(method="api_tcad_show_state", instance_id=record.instance_id)
        stage = state.get("data", {}).get("stage")
        if stage == "no_session":
            create_response = gateway.call(
                method="api_tcad_create_session",
                params={"requirement": sessions.compose_instruction(record, user_message)},
                instance_id=record.instance_id,
            )
            if not create_response.get("ok"):
                raise RuntimeError(create_response.get("error", {}).get("message", "create session failed"))
        sessions.bind_uploaded_assets(record)

    def _worker(
        *,
        record: Any,
        request_id: str,
        user_message: str,
        event_queue: "queue.Queue[dict[str, Any]]",
    ) -> None:
        sessions.begin_request(record, request_id)
        sessions.save_meta(
            record,
            last_request_id=request_id,
            last_user_message=user_message,
            updated_at=datetime.now().isoformat(timespec="seconds"),
            status="running",
        )
        conversation_lock = record.execution_lock
        concurrency_gate = app.config["TCAD_CONCURRENCY_GATE"]
        try:
            queued = False
            if not conversation_lock.acquire(blocking=False):
                queued = True
                event_queue.put({"kind": "status", "status": "queued", "queue_position": 1, "scope": "conversation"})
                conversation_lock.acquire()
            try:
                if not concurrency_gate.acquire(blocking=False):
                    queued = True
                    event_queue.put({"kind": "status", "status": "queued", "queue_position": 1, "scope": "global"})
                    concurrency_gate.acquire()
                try:
                    event_queue.put({"kind": "status", "status": "running", "queue_position": 0, "queued": queued})
                    _ensure_session(record, user_message)
                    inst = gateway.get_instance(record.instance_id)
                    result = inst.agent.agent_decide_and_execute(
                        sessions.compose_instruction(record, user_message),
                        should_abort=lambda: sessions.should_abort(request_id),
                        event_sink=event_queue.put,
                    )
                    sessions.save_meta(
                        record,
                        last_request_id=request_id,
                        last_user_message=user_message,
                        latest_stage=str(result.get("stage") or ""),
                        status="done",
                        updated_at=datetime.now().isoformat(timespec="seconds"),
                    )
                    event_queue.put({"kind": "_result", "result": result})
                finally:
                    concurrency_gate.release()
            finally:
                conversation_lock.release()
        except Exception as exc:  # noqa: BLE001
            sessions.save_meta(
                record,
                last_request_id=request_id,
                last_user_message=user_message,
                status="error",
                latest_error=str(exc),
                updated_at=datetime.now().isoformat(timespec="seconds"),
            )
            event_queue.put({"kind": "error", "error": str(exc)})
        finally:
            sessions.finish_request(request_id)
            event_queue.put({"kind": "_finished"})

    @app.route("/uploadFile", methods=["POST"])
    def upload_file() -> Response:
        file_storage = request.files.get("file")
        if file_storage is None:
            return jsonify({"success": False, "error": "missing file"}), 400
        user_id = _user_id(request.form)
        conversation_id = _conversation_id(request.form)
        asset = sessions.save_upload(user_id=user_id, conversation_id=conversation_id, file_storage=file_storage)
        return jsonify({"success": True, "isUploaded": True, **asset, "conversation_id": conversation_id})

    @app.route("/deleteFile", methods=["POST"])
    def delete_file() -> Response:
        data = _payload()
        user_id = _user_id(data)
        conversation_id = _conversation_id(data)
        file_name = str(data.get("file_name") or data.get("fileName") or "").strip()
        if not file_name:
            return jsonify({"isDeleted": False, "error": "missing file_name"}), 400
        result = sessions.delete_asset(user_id=user_id, conversation_id=conversation_id, file_name=file_name)
        return jsonify({"isDeleted": result["deleted"], "fileName": file_name})

    @app.route("/clear_file_context", methods=["POST"])
    def clear_file_context() -> Response:
        data = _payload()
        result = sessions.clear_file_context(
            user_id=_user_id(data),
            conversation_id=_conversation_id(data),
            file_name=str(data.get("file_name") or data.get("fileName") or "").strip(),
        )
        return jsonify({"success": True, **result})

    @app.route("/delete_session_runtime", methods=["POST"])
    def delete_session_runtime() -> Response:
        data = _payload()
        result = sessions.delete_session(
            user_id=_user_id(data),
            conversation_id=_conversation_id(data),
        )
        return jsonify(
            {
                "success": result["deleted"],
                "deleted": result["deleted"],
                "workdirDeleted": result["workdir_deleted"],
                "stoppedInstance": result["stopped_instance"],
                "conversationId": result["conversation_id"],
                "userId": result["user_id"],
            }
        )

    @app.route("/abort_stream", methods=["POST"])
    def abort_stream() -> Response:
        data = _payload()
        request_id = str(data.get("request_id") or "").strip()
        if not request_id:
            return jsonify({"success": False, "error": "missing request_id"}), 400
        aborted = sessions.abort_request(request_id)
        return jsonify({"success": aborted, "request_id": request_id})

    @app.route("/health", methods=["GET"])
    def health() -> Response:
        instances = gateway.list_instances()
        return jsonify(
            {
                "ok": True,
                "adapter": "tcad-web-adapter",
                "workspace": str(resolved_workspace),
                "active_instances": len(instances.get("instances", [])),
                "llm": {
                    "provider": os.environ.get("TCAD_LLM_PROVIDER", "siliconflow"),
                    "base_url": os.environ.get("TCAD_LLM_BASE_URL", "https://api.siliconflow.cn/v1"),
                    "model_main": os.environ.get("TCAD_MODEL_MAIN", ""),
                    "model_sde": os.environ.get("TCAD_MODEL_SDE", ""),
                    "model_sdevice": os.environ.get("TCAD_MODEL_SDEVICE", ""),
                    "health": _llm_health(),
                },
                "scheduler": {
                    "max_concurrent": app.config["TCAD_MAX_CONCURRENT"],
                },
                "sentaurus_binaries": _binary_health(),
            }
        )

    @app.route("/demo_cases", methods=["GET"])
    def demo_cases() -> Response:
        try:
            limit = max(1, min(int(request.args.get("limit", "8")), 12))
        except ValueError:
            limit = 8
        demo = app.config["TCAD_DEMO_PROVIDER"]
        return jsonify({"success": True, **demo.list_cases(limit=limit)})

    @app.route("/session_summary", methods=["GET"])
    def session_summary() -> Response:
        user_id = _user_id(request.args)
        conversation_id = _conversation_id(request.args)
        record = _record_for_request(user_id, conversation_id)
        state = load_state_payload(record.workdir)
        artifacts = _load_record_artifacts(record)
        public_artifacts = _collect_public_artifacts(record, artifacts)
        return jsonify(
            {
                "success": True,
                "summary": build_session_summary(record=record, state=state, public_artifacts=public_artifacts),
            }
        )

    @app.route("/session_export", methods=["GET"])
    def session_export() -> Response:
        user_id = _user_id(request.args)
        conversation_id = _conversation_id(request.args)
        export_format = str(request.args.get("format") or "json").strip().lower()
        if export_format not in {"json", "markdown"}:
            return jsonify({"success": False, "error": "unsupported format"}), 400
        record = _record_for_request(user_id, conversation_id)
        state = load_state_payload(record.workdir)
        artifacts = _load_record_artifacts(record)
        public_artifacts = _collect_public_artifacts(record, artifacts)
        exported = build_session_export(
            record=record,
            state=state,
            public_artifacts=public_artifacts,
            export_format=export_format,
        )
        headers = {
            "Content-Type": exported["content_type"],
            "Content-Disposition": f"attachment; filename={exported['file_name']}",
        }
        return Response(exported["body"], headers=headers)

    @app.route("/artifact_preview", methods=["GET"])
    def artifact_preview() -> Response:
        artifact_key = str(request.args.get("artifact_key") or "").strip()
        if not artifact_key:
            return jsonify({"success": False, "error": "missing artifact_key"}), 400
        try:
            max_lines = max(20, min(int(request.args.get("max_lines", "80")), 200))
        except ValueError:
            max_lines = 80
        user_id = _user_id(request.args)
        conversation_id = _conversation_id(request.args)
        record = _record_for_request(user_id, conversation_id)
        artifacts = _load_record_artifacts(record)
        artifact_path = str(artifacts.get(artifact_key) or "").strip()
        if not artifact_path:
            return jsonify({"success": False, "error": "artifact not found"}), 404
        preview = build_artifact_preview(
            workdir=record.workdir,
            artifact_key=artifact_key,
            artifact_path=artifact_path,
            max_lines=max_lines,
        )
        if preview is None:
            return jsonify({"success": False, "error": "preview not available"}), 404
        return jsonify({"success": True, "preview": preview})

    @app.route("/brief_summary", methods=["GET"])
    def brief_summary() -> Response:
        user_id = _user_id(request.args)
        conversation_id = _conversation_id(request.args)
        record = _record_for_request(user_id, conversation_id)
        artifacts = _load_record_artifacts(record)
        return jsonify({"success": True, **build_brief_summary(workdir=record.workdir, artifacts=artifacts)})

    @app.route("/validation_summary", methods=["GET"])
    def validation_summary() -> Response:
        user_id = _user_id(request.args)
        conversation_id = _conversation_id(request.args)
        record = _record_for_request(user_id, conversation_id)
        state = load_state_payload(record.workdir)
        artifacts = _load_record_artifacts(record)
        return jsonify({"success": True, **build_validation_summary(workdir=record.workdir, state=state, artifacts=artifacts)})

    @app.route("/reference_preview", methods=["GET"])
    def reference_preview() -> Response:
        ref_id = str(request.args.get("ref_id") or "").strip()
        if not ref_id:
            return jsonify({"success": False, "error": "missing ref_id"}), 400
        user_id = _user_id(request.args)
        conversation_id = _conversation_id(request.args)
        record = _record_for_request(user_id, conversation_id)
        state = load_state_payload(record.workdir)
        preview = build_reference_preview(workdir=record.workdir, state=state, ref_id=ref_id)
        if preview is None:
            return jsonify({"success": False, "error": "reference not found"}), 404
        return jsonify({"success": True, "reference": preview})

    @app.route("/workspace_manifest", methods=["GET"])
    def workspace_manifest() -> Response:
        user_id = _user_id(request.args)
        conversation_id = _conversation_id(request.args)
        record = _record_for_request(user_id, conversation_id)
        state = load_state_payload(record.workdir)
        artifacts = _load_record_artifacts(record)
        manifest = build_workspace_manifest(
            workdir=record.workdir,
            state=state,
            artifacts=artifacts,
            uploads=list(record.uploads.values()),
        )
        for group in manifest.get("groups", []):
            items = group.get("items", [])
            for item in items:
                relative_path = str(item.get("relative_path") or "").strip()
                if relative_path:
                    item["download_path"] = _workspace_download_path(record, relative_path)
                    item["preview_path"] = "/workspace_preview?user_id={user_id}&conversation_id={conversation_id}&path={path}".format(
                        user_id=quote(record.user_id, safe=""),
                        conversation_id=quote(record.conversation_id, safe=""),
                        path=quote(relative_path, safe=""),
                    )
        return jsonify({"success": True, "manifest": manifest})

    @app.route("/workspace_preview", methods=["GET"])
    def workspace_preview() -> Response:
        relative_path = str(request.args.get("path") or "").strip()
        if not relative_path:
            return jsonify({"success": False, "error": "missing path"}), 400
        try:
            max_lines = max(20, min(int(request.args.get("max_lines", "80")), 200))
        except ValueError:
            max_lines = 80
        user_id = _user_id(request.args)
        conversation_id = _conversation_id(request.args)
        record = _record_for_request(user_id, conversation_id)
        resolved = _resolve_workspace_relative(record, relative_path)
        if resolved is None:
            return jsonify({"success": False, "error": "file not found"}), 404
        preview = build_artifact_preview(
            workdir=record.workdir,
            artifact_key=relative_path,
            artifact_path=str(resolved),
            max_lines=max_lines,
        )
        if preview is None:
            return jsonify({"success": False, "error": "preview not available"}), 404
        return jsonify({"success": True, "preview": preview})

    @app.route("/generate_session_title", methods=["POST"])
    def generate_session_title() -> Response:
        data = _payload()
        text = _message_text(data)
        title = text[:16] if text else "TCAD会话"
        return jsonify({"success": True, "title": title})

    @app.route("/artifacts/<path:user_id>/<path:conversation_id>/<artifact_key>", methods=["GET"])
    def download_artifact(user_id: str, conversation_id: str, artifact_key: str) -> Response:
        record = sessions.get_record(user_id, conversation_id) or sessions.get_or_create_record(user_id, conversation_id)
        artifacts = _load_record_artifacts(record)
        artifact_path = artifacts.get(artifact_key)
        if not artifact_path:
            abort(404)
        public = _public_artifact(record, artifact_key, str(artifact_path))
        if public is None:
            abort(404)
        resolved_path = Path(artifact_path).expanduser().resolve()
        as_attachment = request.args.get("download") == "1" or not public["is_image"]
        return send_file(resolved_path, as_attachment=as_attachment, download_name=public["file_name"])

    @app.route("/workspace_file/<path:user_id>/<path:conversation_id>", methods=["GET"])
    def download_workspace_file(user_id: str, conversation_id: str) -> Response:
        relative_path = str(request.args.get("path") or "").strip()
        if not relative_path:
            abort(400)
        record = sessions.get_record(user_id, conversation_id) or sessions.get_or_create_record(user_id, conversation_id)
        resolved = _resolve_workspace_relative(record, relative_path)
        if resolved is None:
            abort(404)
        is_image = resolved.suffix.lower() in IMAGE_SUFFIXES
        as_attachment = request.args.get("download") == "1" or not is_image
        return send_file(resolved, as_attachment=as_attachment, download_name=resolved.name)

    @app.route("/generate", methods=["POST"])
    def generate() -> Response:
        data = _payload()
        user_id = _user_id(data)
        conversation_id = _conversation_id(data)
        user_message = _message_text(data)
        demo_case_id = _demo_case_id(data)
        if _is_smalltalk_request(user_message):
            record = sessions.get_or_create_record(user_id, conversation_id)
            sessions.save_meta(
                record,
                last_user_message=user_message,
                updated_at=datetime.now().isoformat(timespec="seconds"),
                status="done",
                latest_stage="smalltalk",
            )
            greeting = _smalltalk_reply(user_message)
            return jsonify(
                {
                    "success": True,
                    "content": greeting,
                    "assistant_reply": greeting,
                    "stage": "smalltalk",
                    "conversation_id": conversation_id,
                }
            )
        if _is_structure_clarify_request(user_message) or _is_simulation_clarify_request(user_message):
            record = sessions.get_or_create_record(user_id, conversation_id)
            sessions.save_meta(
                record,
                last_user_message=user_message,
                updated_at=datetime.now().isoformat(timespec="seconds"),
                status="done",
                latest_stage="clarify",
            )
            reply = _clarify_reply(user_message)
            return jsonify(
                {
                    "success": True,
                    "content": reply,
                    "assistant_reply": reply,
                    "stage": "clarify",
                    "conversation_id": conversation_id,
                }
            )
        record = sessions.get_or_create_record(user_id, conversation_id)
        try:
            effective_demo_case_id = _resolve_effective_demo_case_id(record, demo_case_id, user_message)
        except KeyError:
            return jsonify({"success": False, "error": "unknown demo_case_id"}), 400
        if effective_demo_case_id:
            sessions.save_meta(
                record,
                last_user_message=user_message,
                updated_at=datetime.now().isoformat(timespec="seconds"),
                status="running",
                latest_stage="demo",
                demo_case_id=effective_demo_case_id,
                demo_mode="scripted_case",
            )
            demo_run = _run_demo_case(record=record, user_message=user_message, case_id=effective_demo_case_id)
            result = demo_run["result"]
            public_artifacts = _collect_public_artifacts(record, result.get("artifacts", {}) or {})
            sessions.save_meta(
                record,
                last_user_message=user_message,
                updated_at=datetime.now().isoformat(timespec="seconds"),
                status="done",
                latest_stage=str(result.get("stage") or ""),
                demo_case_id=effective_demo_case_id,
                demo_mode="scripted_case",
                demo_phase=str(result.get("demo_phase") or "").strip(),
                demo_turn_index=int(result.get("demo_turn_index") or 0),
            )
            return jsonify(
                {
                    "success": True,
                    "content": _build_user_reply(result, public_artifacts),
                    "assistant_reply": _build_user_reply(result, public_artifacts),
                    "stage": result.get("stage", ""),
                    "conversation_id": conversation_id,
                    "artifacts": public_artifacts,
                }
            )
        _ensure_session(record, user_message)
        inst = gateway.get_instance(record.instance_id)
        result = inst.agent.agent_decide_and_execute(sessions.compose_instruction(record, user_message))
        public_artifacts = _collect_public_artifacts(record, result.get("artifacts", {}) or {})
        return jsonify(
            {
                "success": True,
                "content": _build_user_reply(result, public_artifacts),
                "assistant_reply": _build_user_reply(result, public_artifacts),
                "stage": result.get("stage", ""),
                "conversation_id": conversation_id,
                "artifacts": public_artifacts,
            }
        )

    @app.route("/stream_generate", methods=["POST"])
    def stream_generate() -> Response:
        data = _payload()
        user_id = _user_id(data)
        conversation_id = _conversation_id(data)
        user_message = _message_text(data)
        demo_case_id = _demo_case_id(data)
        if not user_message:
            return jsonify({"success": False, "error": "missing message"}), 400
        if _is_smalltalk_request(user_message):
            record = sessions.get_or_create_record(user_id, conversation_id)
            sessions.save_meta(
                record,
                last_user_message=user_message,
                updated_at=datetime.now().isoformat(timespec="seconds"),
                status="done",
                latest_stage="smalltalk",
            )
            request_id = f"tcad-{uuid.uuid4().hex[:12]}"
            greeting = _smalltalk_reply(user_message)

            def _greeting_stream():
                yield _sse(
                    {
                        "kind": "start",
                        "request_id": request_id,
                        "conversation_id": conversation_id,
                        "user_id": user_id,
                        "start_streaming": True,
                    }
                )
                for chunk in _iter_stream_segments(greeting):
                    yield _sse(
                        {
                            "kind": "assistant_chunk",
                            "request_id": request_id,
                            "conversation_id": conversation_id,
                            "user_id": user_id,
                            "chunk": chunk,
                        }
                    )
                yield _sse(
                    {
                        "kind": "done",
                        "request_id": request_id,
                        "conversation_id": conversation_id,
                        "stage": "smalltalk",
                        "assistant_reply": greeting,
                        "aborted": False,
                        "is_complete": True,
                    }
                )

            return Response(stream_with_context(_greeting_stream()), mimetype="text/event-stream")
        if _is_structure_clarify_request(user_message) or _is_simulation_clarify_request(user_message):
            record = sessions.get_or_create_record(user_id, conversation_id)
            sessions.save_meta(
                record,
                last_user_message=user_message,
                updated_at=datetime.now().isoformat(timespec="seconds"),
                status="done",
                latest_stage="clarify",
            )
            request_id = f"tcad-{uuid.uuid4().hex[:12]}"
            reply = _clarify_reply(user_message)

            def _clarify_stream():
                yield _sse(
                    {
                        "kind": "start",
                        "request_id": request_id,
                        "conversation_id": conversation_id,
                        "user_id": user_id,
                        "start_streaming": True,
                    }
                )
                for chunk in _iter_stream_segments(reply):
                    yield _sse(
                        {
                            "kind": "assistant_chunk",
                            "request_id": request_id,
                            "conversation_id": conversation_id,
                            "user_id": user_id,
                            "chunk": chunk,
                        }
                    )
                yield _sse(
                    {
                        "kind": "done",
                        "request_id": request_id,
                        "conversation_id": conversation_id,
                        "stage": "clarify",
                        "assistant_reply": reply,
                        "aborted": False,
                        "is_complete": True,
                    }
                )

            return Response(stream_with_context(_clarify_stream()), mimetype="text/event-stream")
        record = sessions.get_or_create_record(user_id, conversation_id)
        request_id = f"tcad-{uuid.uuid4().hex[:12]}"
        try:
            effective_demo_case_id = _resolve_effective_demo_case_id(record, demo_case_id, user_message)
        except KeyError:
            return jsonify({"success": False, "error": "unknown demo_case_id"}), 400
        if effective_demo_case_id:

            def _demo_stream():
                sessions.save_meta(
                    record,
                    last_request_id=request_id,
                    last_user_message=user_message,
                    updated_at=datetime.now().isoformat(timespec="seconds"),
                    status="running",
                    latest_stage="demo",
                    demo_case_id=effective_demo_case_id,
                    demo_mode="scripted_case",
                )
                yield _sse(
                    {
                        "kind": "start",
                        "request_id": request_id,
                        "conversation_id": conversation_id,
                        "user_id": user_id,
                        "start_streaming": True,
                    }
                )
                try:
                    demo_run = _run_demo_case(record=record, user_message=user_message, case_id=effective_demo_case_id)
                    result = demo_run["result"]
                    for event in demo_run["events"]:
                        payload = {
                            "request_id": request_id,
                            "conversation_id": conversation_id,
                            "user_id": user_id,
                            **event,
                        }
                        if payload.get("kind") == "artifact":
                            public = _public_artifact(record, str(payload.get("artifact_key", "")), str(payload.get("artifact_path", "")))
                            if public is None:
                                continue
                            payload["artifact_path"] = public["file_name"]
                            payload["artifact_download_path"] = public["download_path"]
                            payload["artifact_label"] = public["label"]
                            payload["is_image"] = public["is_image"]
                        yield _sse(payload)
                    public_artifacts = _collect_public_artifacts(record, result.get("artifacts", {}) or {})
                    assistant_reply = _build_user_reply(result, public_artifacts)
                    sessions.save_meta(
                        record,
                        last_request_id=request_id,
                        last_user_message=user_message,
                        updated_at=datetime.now().isoformat(timespec="seconds"),
                        status="done",
                        latest_stage=str(result.get("stage") or ""),
                        demo_case_id=effective_demo_case_id,
                        demo_mode="scripted_case",
                        demo_phase=str(result.get("demo_phase") or "").strip(),
                        demo_turn_index=int(result.get("demo_turn_index") or 0),
                    )
                    yield _sse(
                        {
                            "kind": "done",
                            "request_id": request_id,
                            "conversation_id": conversation_id,
                            "stage": result.get("stage", ""),
                            "assistant_reply": assistant_reply,
                            "aborted": False,
                            "artifacts": public_artifacts,
                            "is_complete": True,
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    sessions.save_meta(
                        record,
                        last_request_id=request_id,
                        last_user_message=user_message,
                        updated_at=datetime.now().isoformat(timespec="seconds"),
                        status="error",
                        latest_error=str(exc),
                        demo_case_id=effective_demo_case_id,
                        demo_mode="scripted_case",
                    )
                    yield _sse(
                        {
                            "kind": "error",
                            "request_id": request_id,
                            "conversation_id": conversation_id,
                            "user_id": user_id,
                            "error": str(exc),
                            "is_complete": True,
                        }
                    )

            return Response(stream_with_context(_demo_stream()), mimetype="text/event-stream")
        event_queue: "queue.Queue[dict[str, Any]]" = queue.Queue()

        def _event_stream():
            assistant_chunk_seen = False
            threading.Thread(
                target=_worker,
                kwargs={
                    "record": record,
                    "request_id": request_id,
                    "user_message": user_message,
                    "event_queue": event_queue,
                },
                daemon=True,
            ).start()
            yield _sse(
                {
                    "kind": "start",
                    "request_id": request_id,
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "start_streaming": True,
                }
            )
            while True:
                event = event_queue.get()
                kind = event.get("kind")
                if kind == "_result":
                    result = event["result"]
                    public_artifacts = _collect_public_artifacts(record, result.get("artifacts", {}) or {})
                    assistant_reply = _build_user_reply(result, public_artifacts)
                    if assistant_reply and not assistant_chunk_seen:
                        for chunk in _iter_stream_segments(assistant_reply):
                            yield _sse(
                                {
                                    "kind": "assistant_chunk",
                                    "request_id": request_id,
                                    "conversation_id": conversation_id,
                                    "user_id": user_id,
                                    "chunk": chunk,
                                }
                            )
                    yield _sse(
                        {
                            "kind": "done",
                            "request_id": request_id,
                            "conversation_id": conversation_id,
                            "stage": result.get("stage", ""),
                            "assistant_reply": assistant_reply,
                            "aborted": bool(result.get("aborted")),
                            "artifacts": public_artifacts,
                            "is_complete": True,
                        }
                    )
                    break
                if kind == "error":
                    yield _sse(
                        {
                            "kind": "error",
                            "request_id": request_id,
                            "conversation_id": conversation_id,
                            "error": event.get("error", "unknown error"),
                            "is_complete": True,
                        }
                    )
                    break
                if kind == "_finished":
                    continue
                payload = {
                    "request_id": request_id,
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    **event,
                }
                if payload.get("kind") == "artifact":
                    public = _public_artifact(record, str(payload.get("artifact_key", "")), str(payload.get("artifact_path", "")))
                    if public is None:
                        continue
                    payload["artifact_path"] = public["file_name"]
                    payload["artifact_download_path"] = public["download_path"]
                    payload["artifact_label"] = public["label"]
                    payload["is_image"] = public["is_image"]
                if payload.get("kind") == "assistant_chunk":
                    assistant_chunk_seen = True
                    for chunk in _iter_stream_segments(str(payload.get("chunk", ""))):
                        yield _sse({**payload, "chunk": chunk})
                    continue
                yield _sse(payload)

        return Response(stream_with_context(_event_stream()), mimetype="text/event-stream")

    return app


def main() -> int:
    import os

    app = create_app()
    host = os.environ.get("TCAD_WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("TCAD_WEB_PORT", "5004"))
    app.run(host=host, port=port, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
