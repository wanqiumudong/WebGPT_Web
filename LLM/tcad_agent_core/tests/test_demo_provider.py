from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path("/data/yphu/Web-FabGPT/LLM/tcad_agent_core")
sys.path.insert(0, str(ROOT))

from web.demo_provider import TcadDemoProvider


def test_default_demo_provider_prefers_original_prototype_tasks() -> None:
    provider = TcadDemoProvider(workspace=ROOT)

    payload = provider.list_cases(limit=8)
    cases = payload["cases"]

    assert [item["case_id"] for item in cases] == [
        "structure_generation_task",
        "electrical_simulation_task",
        "compact_model_task",
    ]
    assert [item["title"] for item in cases] == [
        "结构生成任务",
        "电学仿真任务",
        "紧凑模型构建任务",
    ]
    assert "平面 MOS gate-tunneling" in cases[0]["summary"]
    assert "LDMOS" in cases[1]["summary"]
    assert "NMOS" in cases[2]["summary"]
    assert "New_Data" in cases[0]["reference_basis"][0]
    assert "BV 曲线" in cases[1]["artifact_files"]
    assert "Verilog-A导出" in cases[2]["capabilities"]
    assert "PolySilicon" in cases[0]["prompt"]
    assert "Sentaurus SDE Scheme" in cases[0]["prompt"]
    assert "长漂移区" in cases[1]["prompt"]
    assert "横向硅 LDMOS" in cases[1]["prompt"]
    assert "180 nm 平面 NMOS" in cases[2]["prompt"]


def _tool_sequence(events: list[dict]) -> list[str]:
    return [item["tool_name"] for item in events if item.get("kind") == "tool_end"]


def test_structure_case_advances_across_turns_and_accumulates_artifacts(tmp_path: Path) -> None:
    provider = TcadDemoProvider(workspace=ROOT)
    record = SimpleNamespace(workdir=tmp_path / "session-finfet")
    record.workdir.mkdir(parents=True, exist_ok=True)
    structure_prompt = provider.list_cases(limit=3)["cases"][0]["prompt"]
    structure_visual_prompt = (
        "我想继续查看这个器件的结构结果，请导出结构图和掺杂分布图。"
        "我想重点检查 PolySilicon / oxide / Silicon 三层边界、top 与 bot 接触位置，以及超薄绝缘层附近的局部网格加密是否保持合理。"
    )

    first = provider.run_case(
        record=record,
        case_id="structure_generation_task",
        user_message=structure_prompt,
    )
    first_state = json.loads((record.workdir / "state.json").read_text(encoding="utf-8"))

    assert _tool_sequence(first["events"]) == ["generate_sde_code", "check_sde_syntax"]
    assert first_state["stage"] == "sde_checked"
    assert set(first_state["artifacts"]) == {"sde_cmd"}

    second = provider.run_case(
        record=record,
        case_id="structure_generation_task",
        user_message=structure_visual_prompt,
    )
    second_state = json.loads((record.workdir / "state.json").read_text(encoding="utf-8"))

    assert _tool_sequence(second["events"]) == ["run_svisual_sde_export"]
    assert second_state["stage"] == "svisual_sde_done"
    assert {"sde_cmd", "svisual_png", "svisual_doping_png"} <= set(second_state["artifacts"])


def test_mosfet_compact_case_requires_electrical_phase_before_verilog_a(tmp_path: Path) -> None:
    provider = TcadDemoProvider(workspace=ROOT)
    record = SimpleNamespace(workdir=tmp_path / "session-mosfet")
    record.workdir.mkdir(parents=True, exist_ok=True)

    provider.run_case(
        record=record,
        case_id="compact_model_task",
        user_message=provider.list_cases(limit=3)["cases"][2]["prompt"],
    )
    blocked = provider.run_case(
        record=record,
        case_id="compact_model_task",
        user_message="请直接导出 Verilog-A。",
    )
    blocked_state = json.loads((record.workdir / "state.json").read_text(encoding="utf-8"))

    assert blocked["events"] == []
    assert blocked["result"]["stage"] == "structure_checked"
    assert "Verilog-A" in blocked["result"]["assistant_reply"]
    assert blocked_state["stage"] == "structure_checked"


def test_ldmos_case_can_export_structure_images_before_electrical_chain(tmp_path: Path) -> None:
    provider = TcadDemoProvider(workspace=ROOT)
    record = SimpleNamespace(workdir=tmp_path / "session-ldmos")
    record.workdir.mkdir(parents=True, exist_ok=True)
    electrical_prompt = provider.list_cases(limit=3)["cases"][1]["prompt"]
    electrical_results_prompt = (
        "我想继续对这个 LDMOS 进行电学仿真，请生成适配的 Sentaurus SDevice 仿真脚本，并导出输出特性与击穿结果图。"
        "我希望当前工作区中能够看到用于输出特性和击穿分析的脚本文件、结果曲线以及对应的运行日志，以便检查这条 LDMOS 电学仿真链路是否完整。"
    )

    provider.run_case(
        record=record,
        case_id="electrical_simulation_task",
        user_message=electrical_prompt,
    )
    second = provider.run_case(
        record=record,
        case_id="electrical_simulation_task",
        user_message=electrical_results_prompt,
    )
    second_state = json.loads((record.workdir / "state.json").read_text(encoding="utf-8"))

    assert _tool_sequence(second["events"]) == [
        "generate_sdevice_code",
        "check_sdevice_syntax",
        "run_sdevice",
        "run_svisual_export",
    ]
    assert second_state["stage"] == "svisual_done"
    assert {"sde_cmd", "sdevice_cmd", "plot_output", "plot_breakdown"} <= set(second_state["artifacts"])


def test_ldmos_case_materializes_realistic_logs_after_electrical_run(tmp_path: Path) -> None:
    provider = TcadDemoProvider(workspace=ROOT)
    record = SimpleNamespace(workdir=tmp_path / "session-ldmos-electrical")
    record.workdir.mkdir(parents=True, exist_ok=True)
    electrical_prompt = provider.list_cases(limit=3)["cases"][1]["prompt"]
    electrical_results_prompt = (
        "我想继续对这个 LDMOS 进行电学仿真，请生成适配的 Sentaurus SDevice 仿真脚本，并导出输出特性与击穿结果图。"
        "我希望当前工作区中能够看到用于输出特性和击穿分析的脚本文件、结果曲线以及对应的运行日志，以便检查这条 LDMOS 电学仿真链路是否完整。"
    )

    provider.run_case(
        record=record,
        case_id="electrical_simulation_task",
        user_message=electrical_prompt,
    )
    third = provider.run_case(
        record=record,
        case_id="electrical_simulation_task",
        user_message=electrical_results_prompt,
    )
    third_state = json.loads((record.workdir / "state.json").read_text(encoding="utf-8"))

    assert _tool_sequence(third["events"]) == [
        "generate_sdevice_code",
        "check_sdevice_syntax",
        "run_sdevice",
        "run_svisual_export",
    ]
    assert third_state["stage"] == "svisual_done"
    assert {"sde_cmd", "sdevice_cmd", "plot_output", "plot_breakdown"} <= set(third_state["artifacts"])
    assert (record.workdir / "logs" / "run_breakdown.log_des.log").exists()
    assert (record.workdir / "logs" / "n33_des.log").exists()
    assert (record.workdir / "logs" / "sdemodel_msh.log").exists()


def test_mosfet_compact_case_materializes_realistic_log_files(tmp_path: Path) -> None:
    provider = TcadDemoProvider(workspace=ROOT)
    record = SimpleNamespace(workdir=tmp_path / "session-mosfet-logs")
    record.workdir.mkdir(parents=True, exist_ok=True)
    compact_prompt = provider.list_cases(limit=3)["cases"][2]["prompt"]
    compact_electrical_prompt = (
        "我想继续得到这个器件的参考电学结果，请生成适配的 Sentaurus SDevice 仿真脚本，并导出可直接查看的参考 Id-Vg 和 Id-Vd 曲线。"
        "我希望当前工作区中能够看到用于转移特性和输出特性分析的脚本文件、结果曲线以及相关日志，以便检查这条 NMOS 电学仿真链路是否完整。"
    )

    provider.run_case(
        record=record,
        case_id="compact_model_task",
        user_message=compact_prompt,
    )
    provider.run_case(
        record=record,
        case_id="compact_model_task",
        user_message=compact_electrical_prompt,
    )

    log_dir = record.workdir / "logs"
    assert (log_dir / "n21_des.log").exists()
    assert (log_dir / "SVisualTcl.log").exists()
    assert (log_dir / "generate_sdevice_code.log").exists()


def test_mosfet_compact_case_exports_transfer_and_output_curves(tmp_path: Path) -> None:
    provider = TcadDemoProvider(workspace=ROOT)
    record = SimpleNamespace(workdir=tmp_path / "session-mosfet-curves")
    record.workdir.mkdir(parents=True, exist_ok=True)
    compact_prompt = provider.list_cases(limit=3)["cases"][2]["prompt"]
    compact_electrical_prompt = (
        "我想继续得到这个器件的参考电学结果，请生成适配的 Sentaurus SDevice 仿真脚本，并导出可直接查看的参考 Id-Vg 和 Id-Vd 曲线。"
        "我希望当前工作区中能够看到用于转移特性和输出特性分析的脚本文件、结果曲线以及相关日志，以便检查这条 NMOS 电学仿真链路是否完整。"
    )

    provider.run_case(
        record=record,
        case_id="compact_model_task",
        user_message=compact_prompt,
    )
    second = provider.run_case(
        record=record,
        case_id="compact_model_task",
        user_message=compact_electrical_prompt,
    )
    state = json.loads((record.workdir / "state.json").read_text(encoding="utf-8"))

    assert _tool_sequence(second["events"]) == [
        "generate_sdevice_code",
        "check_sdevice_syntax",
        "run_sdevice",
        "run_svisual_export",
    ]
    assert state["stage"] == "svisual_done"
    assert {"sde_cmd", "sdevice_cmd", "plot_transfer", "plot_output"} <= set(state["artifacts"])


def test_exact_prompt_mode_blocks_non_scripted_follow_up(tmp_path: Path) -> None:
    provider = TcadDemoProvider(workspace=ROOT)
    record = SimpleNamespace(workdir=tmp_path / "session-exact-mode")
    record.workdir.mkdir(parents=True, exist_ok=True)

    provider.run_case(
        record=record,
        case_id="structure_generation_task",
        user_message=provider.list_cases(limit=3)["cases"][0]["prompt"],
    )
    blocked = provider.run_case(
        record=record,
        case_id="structure_generation_task",
        user_message="继续给我看一下图片。",
    )

    assert blocked["events"] == []
    assert blocked["result"]["stage"] == "sde_checked"
