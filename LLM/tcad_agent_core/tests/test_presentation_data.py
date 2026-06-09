from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path("/data/yphu/Web-FabGPT/LLM/tcad_agent_core")
sys.path.insert(0, str(ROOT))

from web.presentation_data import build_session_export, build_session_summary, build_workspace_manifest


def _touch(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_workspace_manifest_hides_internal_files_and_counts_primary_outputs(tmp_path: Path) -> None:
    workdir = tmp_path / "session"
    _touch(workdir / "run" / "sde_dvs.cmd", "sde")
    _touch(workdir / "run" / "sde_result_msh.tdr", "mesh")
    _touch(workdir / "run" / "sde_result_bnd.tdr", "bnd")
    _touch(workdir / "run" / "sde_result_msh.cmd", "internal-sidecar")
    _touch(workdir / "run" / "sde_dvs.log", "log")
    _touch(workdir / "reports" / "sde_result_msh.png", "png")
    _touch(workdir / "reports" / "tdr_info_sde_result_msh.txt", "report")
    _touch(workdir / "reports" / "generate_sde_reference_context.txt", "internal-report")
    _touch(workdir / "reports" / "sde_coverage_audit.json", "{}")
    _touch(workdir / "logs" / "debug_trace.jsonl", "{}")
    _touch(workdir / "logs" / "main_agent_sde_brief.txt", "brief")
    _touch(workdir / "logs" / "sde_syntax.log", "syntax")

    manifest = build_workspace_manifest(
        workdir=workdir,
        state={"stage": "svisual_sde_done", "notes": ["done"]},
        artifacts={
            "sde_cmd": str(workdir / "run" / "sde_dvs.cmd"),
            "mesh": str(workdir / "run" / "sde_result_msh.tdr"),
            "bnd": str(workdir / "run" / "sde_result_bnd.tdr"),
            "svisual_png": str(workdir / "reports" / "sde_result_msh.png"),
            "tdr_info_report": str(workdir / "reports" / "tdr_info_sde_result_msh.txt"),
        },
        uploads=[],
    )

    groups = {group["key"]: group for group in manifest["groups"]}
    output_names = [item["file_name"] for item in groups["outputs"]["items"]]
    report_names = [item["file_name"] for item in groups["reports"]["items"]]
    log_names = [item["file_name"] for item in groups["logs"]["items"]]

    assert output_names == ["sde_dvs.cmd", "sde_result_msh.tdr", "sde_result_bnd.tdr"]
    assert report_names == ["tdr_info_sde_result_msh.txt", "sde_result_msh.png"]
    assert "sde_result_msh.cmd" not in log_names
    assert "generate_sde_reference_context.txt" not in log_names
    assert "sde_coverage_audit.json" not in log_names
    assert "debug_trace.jsonl" not in log_names
    assert "main_agent_sde_brief.txt" not in log_names
    assert log_names == ["sde_dvs.log", "sde_syntax.log"]
    assert manifest["primary_file_count"] == 5
    assert manifest["log_file_count"] == 2


def test_session_summary_and_export_include_meta_and_tool_sequence(tmp_path: Path) -> None:
    workdir = tmp_path / "session"
    _touch(
        workdir / "logs" / "debug_trace.jsonl",
        "\n".join(
            [
                json.dumps({"action": "mcp_tool_done", "payload": {"tool": "generate_sde_code", "stage": "sde_generated"}}),
                json.dumps({"action": "mcp_tool_done", "payload": {"tool": "check_sde_syntax", "stage": "sde_checked"}}),
            ]
        ),
    )
    _touch(workdir / "run" / "sde_dvs.cmd", "sde")
    _touch(workdir / "reports" / "reference_candidates.json", json.dumps({
        "summary_note": "已选中 2 个 SDE 参考，1 条函数知识",
        "selected_sde_references": [{"ref_id": "a"}],
        "selected_sdevice_references": [],
        "selected_function_references": [{"ref_id": "f"}],
    }, ensure_ascii=False))
    _touch(workdir / "web_session_meta.json", json.dumps({
        "last_user_message": "生成一个 NMOS",
        "updated_at": "2026-03-11T12:00:00",
    }, ensure_ascii=False))
    state = {
        "stage": "sde_checked",
        "notes": ["Default single session initialized.", "SDE syntax check passed."],
        "artifacts": {"reference_candidates": str(workdir / "reports" / "reference_candidates.json")},
        "metrics": {},
        "spec": {"requirement": "生成一个 NMOS"},
    }
    record = SimpleNamespace(
        conversation_id="conv-1",
        user_id="yphu",
        workdir=workdir,
        uploads={},
    )
    public_artifacts = [
        {"label": "SDE 脚本", "file_name": "sde_dvs.cmd", "is_image": False},
    ]

    summary = build_session_summary(record=record, state=state, public_artifacts=public_artifacts)
    exported = build_session_export(record=record, state=state, public_artifacts=public_artifacts, export_format="markdown")

    assert summary["tool_sequence"] == ["generate_sde_code", "check_sde_syntax"]
    assert summary["reference_stats"] == {"sde": 1, "sdevice": 0, "function": 1}
    assert exported["content_type"].startswith("text/markdown")
    assert "实验模式" not in exported["body"]
    assert "- `generate_sde_code`" in exported["body"]


def test_session_summary_includes_demo_case_metadata(tmp_path: Path) -> None:
    workdir = tmp_path / "session"
    _touch(workdir / "web_session_meta.json", json.dumps({
        "last_user_message": "请演示 compact model",
        "updated_at": "2026-03-29T12:00:00",
    }, ensure_ascii=False))
    record = SimpleNamespace(
        conversation_id="conv-demo",
        user_id="demo-user",
        workdir=workdir,
        uploads={},
    )
    state = {
        "stage": "validated",
        "notes": ["已完成固定案例演示。"],
        "spec": {"requirement": "请基于 IdVg/IdVd 构建紧凑模型"},
        "demo_case": {
            "case_id": "compact-model-demo",
            "title": "案例 3 · 紧凑模型构建",
            "capabilities": ["自然语言输入", "电学仿真", "Verilog-A 导出"],
        },
        "demo_panels": [
            {
                "title": "固定输入",
                "items": ["请基于已有二维 planar nMOS 的 IdVg 和 IdVd 结果，整理紧凑模型参数。"],
            },
            {
                "title": "导出结果",
                "items": ["Verilog-A 骨架", "参数卡", "拟合对比图"],
            },
        ],
    }

    summary = build_session_summary(record=record, state=state, public_artifacts=[])

    assert summary["demo_case"]["case_id"] == "compact-model-demo"
    assert summary["demo_case"]["title"] == "案例 3 · 紧凑模型构建"
    assert summary["demo_panels"][0]["title"] == "固定输入"
    assert "Verilog-A 导出" in summary["demo_case"]["capabilities"]


def test_session_export_uses_neutral_task_wording_for_prebuilt_cases(tmp_path: Path) -> None:
    workdir = tmp_path / "session"
    record = SimpleNamespace(
        conversation_id="conv-task",
        user_id="task-user",
        workdir=workdir,
        uploads={},
    )
    state = {
        "stage": "validated",
        "notes": ["已整理预置任务结果。"],
        "spec": {"requirement": "请基于 IdVg/IdVd 构建紧凑模型"},
        "demo_case": {
            "case_id": "compact-model",
            "title": "二维 nMOS 紧凑模型构建",
            "capabilities": ["自然语言输入", "电学仿真", "Verilog-A 导出"],
        },
        "demo_panels": [
            {
                "title": "输入说明",
                "items": ["请基于已有二维 planar nMOS 的 IdVg 和 IdVd 结果，整理紧凑模型参数。"],
            },
            {
                "title": "输出内容",
                "items": ["Verilog-A 骨架", "参数卡", "拟合对比图"],
            },
        ],
    }

    exported = build_session_export(record=record, state=state, public_artifacts=[], export_format="markdown")
    body = exported["body"]

    assert "## 任务概览" in body
    assert "## 任务说明" in body
    assert "## 演示案例" not in body
    assert "## 案例说明" not in body


def test_workspace_manifest_supports_breakdown_curve_artifact(tmp_path: Path) -> None:
    workdir = tmp_path / "session"
    breakdown_path = _touch(workdir / "reports" / "breakdown_BV.png", "png")

    manifest = build_workspace_manifest(
        workdir=workdir,
        state={"stage": "validated", "notes": ["done"]},
        artifacts={"plot_breakdown": str(breakdown_path)},
        uploads=[],
    )

    reports = next(group for group in manifest["groups"] if group["key"] == "reports")
    labels = [item["label"] for item in reports["items"]]

    assert "BV 曲线" in labels
