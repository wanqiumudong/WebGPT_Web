from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
from typing import Any


GENERIC_CONTINUE_MARKERS = (
    "继续",
    "接着",
    "下一步",
    "后续",
    "继续做",
    "继续执行",
    "往下",
    "然后",
)


def _normalize_prompt_text(text: str) -> str:
    return " ".join(str(text or "").strip().split()).lower()


class TcadDemoProvider:
    def __init__(self, *, workspace: Path, case_specs: list[dict[str, Any]] | None = None) -> None:
        self.workspace = workspace.resolve()
        specs = case_specs if case_specs is not None else _default_case_specs(self.workspace)
        self._case_specs = {
            str(spec.get("case_id") or "").strip(): spec
            for spec in specs
            if str(spec.get("case_id") or "").strip()
        }

    def has_case(self, case_id: str) -> bool:
        return str(case_id or "").strip() in self._case_specs

    def list_cases(self, *, limit: int = 8) -> dict[str, Any]:
        ordered = list(self._case_specs.values())
        ordered.sort(key=lambda item: int(item.get("order") or 999))
        cases = []
        for spec in ordered[: max(1, limit)]:
            cases.append(
                {
                    "case_id": spec["case_id"],
                    "title": spec.get("title", spec["case_id"]),
                    "summary": spec.get("summary", ""),
                    "prompt": spec.get("prompt", ""),
                    "device_type": spec.get("device_type", "tcad"),
                    "simulation_type": spec.get("simulation_type", "structure"),
                    "profile": spec.get("profile", ""),
                    "capabilities": list(spec.get("capabilities") or []),
                    "artifact_files": list(spec.get("artifact_files") or []),
                    "reference_basis": list(spec.get("reference_basis") or []),
                    "is_featured": bool(spec.get("is_featured")),
                }
            )
        return {
            "source": "tcad-task-provider",
            "cases": cases,
        }

    def run_case(self, *, record: Any, case_id: str, user_message: str) -> dict[str, Any]:
        normalized_case_id = str(case_id or "").strip()
        if normalized_case_id not in self._case_specs:
            raise KeyError(f"unknown task case: {normalized_case_id}")

        spec = self._case_specs[normalized_case_id]
        workdir = Path(getattr(record, "workdir")).resolve()
        run_dir = workdir / "run"
        reports_dir = workdir / "reports"
        logs_dir = workdir / "logs"
        run_dir.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)

        prior_state = self._load_state(workdir)
        current_case_id = str(((prior_state.get("demo_case") or {}).get("case_id") or "")).strip()
        if current_case_id and current_case_id != normalized_case_id:
            self._reset_workspace(workdir)
            run_dir.mkdir(parents=True, exist_ok=True)
            reports_dir.mkdir(parents=True, exist_ok=True)
            logs_dir.mkdir(parents=True, exist_ok=True)
            prior_state = {}

        phases = list(spec.get("phases") or [])
        completed_turns = int(prior_state.get("demo_turn_index") or 0)
        artifacts = dict(prior_state.get("artifacts") or {})
        metrics = dict(prior_state.get("metrics") or {})
        notes = [str(item) for item in (prior_state.get("notes") or []) if str(item).strip()]
        stage = str(prior_state.get("stage") or "created")
        current_phase = str(prior_state.get("demo_phase") or "").strip()

        candidate_phases = self._resolve_candidate_phases(
            phases=phases,
            current_phase=current_phase,
            completed_turns=completed_turns,
        )
        if current_phase and not candidate_phases:
            return {
                "events": [],
                "result": {
                    "stage": stage,
                    "assistant_reply": str(spec.get("completed_reply") or "当前这条会话的结果已经全部准备好了，可以直接查看当前工作区中的文件。").strip(),
                    "artifacts": artifacts,
                    "metrics": metrics,
                    "notes": notes,
                    "demo_phase": current_phase,
                    "demo_turn_index": completed_turns,
                },
            }

        if not candidate_phases:
            return {
                "events": [],
                "result": {
                    "stage": stage,
                    "assistant_reply": "当前这条会话暂时没有可继续推进的结果链路。",
                    "artifacts": artifacts,
                    "metrics": metrics,
                    "notes": notes,
                    "demo_phase": current_phase,
                    "demo_turn_index": completed_turns,
                },
            }

        phase = candidate_phases[0]
        if current_phase:
            phase = self._select_phase_for_message(
                user_message=user_message,
                current_phase=current_phase,
                phases=candidate_phases,
            )
        if phase is None:
            blocked_reply = self._resolve_blocked_reply(
                spec=spec,
                phases=phases,
                current_phase=current_phase,
                candidate_phases=candidate_phases,
            )
            return {
                "events": [],
                "result": {
                    "stage": stage,
                    "assistant_reply": blocked_reply,
                    "artifacts": artifacts,
                    "metrics": metrics,
                    "notes": notes,
                    "demo_phase": current_phase,
                    "demo_turn_index": completed_turns,
                },
            }

        phase_artifacts = self._materialize_assets(workdir=workdir, assets=phase.get("assets") or [])
        artifacts.update(phase_artifacts)
        metrics.update(dict(phase.get("metrics") or {}))

        validation_payload = phase.get("validation_report")
        if isinstance(validation_payload, dict):
            validation_path = reports_dir / "validation_report.json"
            validation_path.write_text(json.dumps(validation_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            artifacts["validation_report"] = str(validation_path)

        phase_notes = [str(item) for item in (phase.get("notes") or []) if str(item).strip()]
        notes = [*notes, *phase_notes]
        next_turn_index = completed_turns + 1
        next_phase = str(phase.get("phase_id") or f"phase-{next_turn_index}").strip()
        stage = str(phase.get("stage") or stage or "validated").strip()

        state = {
            "stage": stage,
            "notes": notes,
            "spec": {"requirement": user_message},
            "metrics": metrics,
            "artifacts": artifacts,
            "task_summary": str(phase.get("task_summary") or spec.get("task_summary") or "").strip(),
            "done_criteria": list(phase.get("done_criteria") or spec.get("done_criteria") or []),
            "demo_case": {
                "case_id": spec["case_id"],
                "title": spec.get("title", spec["case_id"]),
                "summary": spec.get("summary", ""),
                "capabilities": list(spec.get("capabilities") or []),
            },
            "demo_panels": list(phase.get("demo_panels") or spec.get("demo_panels") or []),
            "demo_phase": next_phase,
            "demo_turn_index": next_turn_index,
        }
        self._state_file(workdir).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        self._append_trace(logs_dir=logs_dir, steps=phase.get("steps") or [], stage=stage)
        self._append_step_logs(logs_dir=logs_dir, steps=phase.get("steps") or [], stage=stage)

        events = self._build_events(
            spec=spec,
            phase=phase,
            artifacts=artifacts,
            turn_index=next_turn_index,
        )
        return {
            "events": events,
            "result": {
                "stage": stage,
                "assistant_reply": str(phase.get("final_message") or "").strip(),
                "artifacts": artifacts,
                "metrics": metrics,
                "notes": notes,
                "demo_phase": next_phase,
                "demo_turn_index": next_turn_index,
            },
        }

    @staticmethod
    def _state_file(workdir: Path) -> Path:
        return workdir / "state.json"

    @classmethod
    def _load_state(cls, workdir: Path) -> dict[str, Any]:
        return _read_json(cls._state_file(workdir)) or {}

    @staticmethod
    def _reset_workspace(workdir: Path) -> None:
        for relative in ("run", "reports", "logs"):
            shutil.rmtree(workdir / relative, ignore_errors=True)
        (workdir / "state.json").unlink(missing_ok=True)

    @staticmethod
    def _message_matches(*, user_message: str, phase: dict[str, Any]) -> bool:
        low = _normalize_prompt_text(user_message)
        if not low:
            return False
        exact_prompts = tuple(
            _normalize_prompt_text(item)
            for item in (phase.get("exact_prompts") or [])
            if str(item).strip()
        )
        if exact_prompts:
            return low in exact_prompts
        reject_markers = tuple(
            str(item).strip().lower()
            for item in (phase.get("reject_markers") or [])
            if str(item).strip()
        )
        if any(marker in low for marker in reject_markers):
            return False
        markers = tuple(str(item).strip().lower() for item in (phase.get("intent_markers") or []) if str(item).strip())
        if any(marker in low for marker in markers):
            return True
        if bool(phase.get("accept_continue", True)) and any(marker in low for marker in GENERIC_CONTINUE_MARKERS):
            return True
        return False

    @staticmethod
    def _resolve_candidate_phases(
        *,
        phases: list[dict[str, Any]],
        current_phase: str,
        completed_turns: int,
    ) -> list[dict[str, Any]]:
        if not phases:
            return []
        if not current_phase:
            initial_phases = [phase for phase in phases if bool(phase.get("initial"))]
            if initial_phases:
                return [initial_phases[0]]
            return [phases[0]]
        matched: list[dict[str, Any]] = []
        for phase in phases:
            allowed_from = phase.get("allowed_from")
            if allowed_from is None:
                continue
            if isinstance(allowed_from, (list, tuple, set)):
                normalized = {str(item).strip() for item in allowed_from if str(item).strip()}
            else:
                value = str(allowed_from).strip()
                normalized = {value} if value else set()
            if current_phase in normalized:
                matched.append(phase)
        if matched:
            return matched
        if completed_turns < len(phases):
            return [phases[completed_turns]]
        return []

    @classmethod
    def _select_phase_for_message(
        cls,
        *,
        user_message: str,
        current_phase: str,
        phases: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not phases:
            return None
        if len(phases) == 1:
            phase = phases[0]
            return phase if cls._message_matches(user_message=user_message, phase=phase) else None

        scored: list[tuple[int, dict[str, Any]]] = []
        for phase in phases:
            match_score = cls._intent_match_score(user_message=user_message, phase=phase)
            if match_score > 0:
                scored.append((match_score, phase))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]

    @staticmethod
    def _intent_match_score(*, user_message: str, phase: dict[str, Any]) -> int:
        low = _normalize_prompt_text(user_message)
        if not low:
            return 0
        exact_prompts = tuple(
            _normalize_prompt_text(item)
            for item in (phase.get("exact_prompts") or [])
            if str(item).strip()
        )
        if exact_prompts:
            return 1000 if low in exact_prompts else 0
        reject_markers = tuple(
            str(item).strip().lower()
            for item in (phase.get("reject_markers") or [])
            if str(item).strip()
        )
        if any(marker in low for marker in reject_markers):
            return 0
        markers = tuple(
            str(item).strip().lower()
            for item in (phase.get("intent_markers") or [])
            if str(item).strip()
        )
        return sum(1 for marker in markers if marker in low)

    @staticmethod
    def _resolve_blocked_reply(
        *,
        spec: dict[str, Any],
        phases: list[dict[str, Any]],
        current_phase: str,
        candidate_phases: list[dict[str, Any]],
    ) -> str:
        if current_phase:
            for phase in phases:
                if str(phase.get("phase_id") or "").strip() == current_phase:
                    current_message = str(phase.get("blocked_reply") or "").strip()
                    if current_message:
                        return current_message
        if len(candidate_phases) == 1:
            return str(
                candidate_phases[0].get("blocked_reply")
                or "我先沿着当前这条会话已经完成的阶段继续推进，暂时不切换到别的结果类型。"
            ).strip()
        phase_messages = [
            str(phase.get("blocked_reply") or "").strip()
            for phase in candidate_phases
            if str(phase.get("blocked_reply") or "").strip()
        ]
        if phase_messages:
            return phase_messages[0]
        if current_phase:
            return "当前这条会话已经有前序结果了。你可以继续要求导出结构图，或者继续整理本轮对应的结果。"
        return str(spec.get("completed_reply") or "当前这条会话的结果已经全部准备好了，可以直接查看当前工作区中的文件。").strip()

    @staticmethod
    def _materialize_assets(*, workdir: Path, assets: list[dict[str, Any]]) -> dict[str, str]:
        artifact_paths: dict[str, str] = {}
        for asset in assets:
            source_path = Path(asset["source"]).expanduser().resolve()
            target_path = workdir / str(asset["target"])
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            artifact_key = str(asset.get("artifact_key") or "").strip()
            if artifact_key:
                artifact_paths[artifact_key] = str(target_path)
        return artifact_paths

    @staticmethod
    def _append_trace(*, logs_dir: Path, steps: list[dict[str, Any]], stage: str) -> None:
        trace_path = logs_dir / "debug_trace.jsonl"
        existing = []
        if trace_path.exists():
            existing = trace_path.read_text(encoding="utf-8").splitlines()
        for step in steps:
            tool_name = str(step.get("tool_name") or "").strip()
            if not tool_name:
                continue
            existing.append(
                json.dumps(
                    {
                        "action": "mcp_tool_done",
                        "payload": {
                            "tool": tool_name,
                            "stage": str(step.get("stage") or stage),
                        },
                    },
                    ensure_ascii=False,
                )
            )
        if existing:
            trace_path.write_text("\n".join(existing), encoding="utf-8")

    @staticmethod
    def _append_step_logs(*, logs_dir: Path, steps: list[dict[str, Any]], stage: str) -> None:
        for step in steps:
            tool_name = str(step.get("tool_name") or "").strip()
            if not tool_name:
                continue
            log_path = logs_dir / f"{tool_name}.log"
            lines = []
            if log_path.exists():
                lines = log_path.read_text(encoding="utf-8").splitlines()
            lines.extend(
                [
                    f"tool={tool_name}",
                    f"step={str(step.get('title') or tool_name).strip()}",
                    f"pre_stage={str(step.get('pre_stage') or 'created').strip()}",
                    f"post_stage={str(step.get('stage') or stage).strip()}",
                    "status=completed",
                    "",
                ]
            )
            log_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    @staticmethod
    def _build_events(
        *,
        spec: dict[str, Any],
        phase: dict[str, Any],
        artifacts: dict[str, str],
        turn_index: int,
    ) -> list[dict[str, Any]]:
        plan_id = f"task-{spec['case_id']}-t{turn_index}"
        plan_steps = []
        for index, step in enumerate(phase.get("steps") or [], start=1):
            plan_steps.append(
                {
                    "step_id": str(step.get("step_id") or f"step-{index}"),
                    "title": str(step.get("title") or step.get("tool_name") or f"步骤 {index}"),
                    "tool_name": str(step.get("tool_name") or ""),
                    "status": "pending",
                }
            )

        events: list[dict[str, Any]] = []
        if plan_steps:
            events.append(
                {
                    "kind": "plan_created",
                    "summary": str(phase.get("plan_summary") or spec.get("plan_summary") or "").strip(),
                    "plan_id": plan_id,
                    "plan_attempt": turn_index,
                    "plan_steps": plan_steps,
                }
            )

        for index, step in enumerate(phase.get("steps") or [], start=1):
            step_id = str(step.get("step_id") or f"step-{index}")
            tool_name = str(step.get("tool_name") or "").strip()
            title = str(step.get("title") or tool_name or f"步骤 {index}")
            assistant_before = str(step.get("assistant_before") or "").strip()
            assistant_after = str(step.get("assistant_after") or "").strip()

            if assistant_before:
                events.append({"kind": "assistant_chunk", "chunk": assistant_before})
            events.append(
                {
                    "kind": "plan_step_update",
                    "plan_id": plan_id,
                    "step_id": step_id,
                    "title": title,
                    "tool_name": tool_name,
                    "status": "in_progress",
                }
            )
            if tool_name:
                events.append(
                    {
                        "kind": "tool_start",
                        "tool_name": tool_name,
                        "stage": str(step.get("pre_stage") or "created"),
                    }
                )
                events.append(
                    {
                        "kind": "tool_end",
                        "tool_name": tool_name,
                        "stage": str(step.get("stage") or ""),
                        "ok": True,
                    }
                )
            for artifact_key in step.get("artifact_keys") or []:
                artifact_path = str(artifacts.get(str(artifact_key)) or "").strip()
                if artifact_path:
                    events.append(
                        {
                            "kind": "artifact",
                            "artifact_key": str(artifact_key),
                            "artifact_path": artifact_path,
                        }
                    )
            events.append(
                {
                    "kind": "plan_step_update",
                    "plan_id": plan_id,
                    "step_id": step_id,
                    "title": title,
                    "tool_name": tool_name,
                    "status": "completed",
                }
            )
            if assistant_after:
                events.append({"kind": "assistant_chunk", "chunk": assistant_after})
        return events


def _default_case_specs(workspace: Path) -> list[dict[str, Any]]:
    new_data_root = Path("/data/yphu/Dataset/New_Data").resolve()
    repo_demo_root = (workspace / "web" / "demo_assets").resolve()

    structure_root = new_data_root / "第25课_6.1节_MOS_Gate_Tunneling" / "MOS_Gate_Tunneling"
    electrical_root = new_data_root / "第33课_8.3节_Radiation仿真案例" / "Radiation" / "LDMOS_Alpha"
    electrical_sde_root = Path("/data/yphu/Dataset/TCAD_Lession_Material/2/2-3/DAY5_SJ_LDMOS_SDE_only").resolve()
    compact_root = new_data_root / "第34课_8.4节_可靠性HCI和NBTI" / "NMOS_180nm_HCI"
    compact_sde_root = new_data_root / "第33课_8.3节_Radiation仿真案例" / "Radiation" / "NMOS_SSE"

    structure_assets_root = repo_demo_root / "prototype_mos_gate_tunneling"
    electrical_assets_root = repo_demo_root / "prototype_ldmos_alpha"
    compact_assets_root = repo_demo_root / "prototype_nmos_180nm_hci"

    structure_prompt = (
        "我想构建一个平面 MOS gate-tunneling 结构，请直接生成可执行的 Sentaurus SDE Scheme 脚本。"
        "器件主体需要包含顶部 PolySilicon gate、超薄 gate oxide / tunnel dielectric 和底部连续 Silicon 主体，并且只保留 top 与 bot 两个接触。"
        "请把 gate 材料、绝缘层材料、绝缘层厚度、silicon 厚度、横向半宽、gate doping、channel 背景掺杂以及局部 gate-oxide mesh refinement 都显式保留下来。"
        "网格方面请同时保留全局 refinement、Silicon/oxide 界面细化和超薄绝缘层附近的局部加密。最后请先完成脚本整理，并检查语法是否正确。"
    )
    structure_visual_prompt = (
        "我想继续查看这个器件的结构结果，请导出结构图和掺杂分布图。我想重点检查 PolySilicon / oxide / Silicon 三层边界、top 与 bot 接触位置，以及超薄绝缘层附近的局部网格加密是否保持合理。"
    )
    electrical_prompt = (
        "我想构建一个横向硅 LDMOS，请先整理这个器件的结构与 SDE 代码，并完成必要检查。"
        "器件需要体现 NWell / PWell、gate、source/body 区、长漂移区、LOCOS/STI 隔离以及 drain 功率侧结构，同时保留对应的几何与掺杂信息，使输出特性分析所需区域定义保持清晰。"
        "请保留关键 drift 区、gate oxide、source/body、drain 侧结构以及接触定义，并先完成这组器件结构与 SDE 代码的整理与检查。"
    )
    electrical_results_prompt = (
        "我想继续对这个 LDMOS 进行电学仿真，请生成适配的 Sentaurus SDevice 仿真脚本，并导出输出特性与击穿结果图。"
        "我希望当前工作区中能够看到用于输出特性和击穿分析的脚本文件、结果曲线以及对应的运行日志，以便检查这条 LDMOS 电学仿真链路是否完整。"
    )
    compact_prompt = (
        "我想构建一个 180 nm 平面 NMOS，请直接生成可执行的 Sentaurus SDE Scheme 脚本。"
        "器件主体需要包含连续的 Silicon substrate、表面 gate oxide、PolySilicon gate、两侧 spacer、source/drain extension 与 source/drain 主注入区域，"
        "并保留 source、drain、gate、substrate 四个接触。"
        "结构方面请把 well implant、gate stack、STI/LOCOS 隔离、source/drain 区以及沟道关键尺寸都保留下来，使器件边界、接触定义与后续偏置语义保持一致。"
        "掺杂方面请保留 Boron 背景掺杂、PolySilicon gate 的高浓度掺杂，以及 source/drain 的 Arsenic 注入、extension 分布与结深定义。"
        "网格方面请保留全局细化、gate oxide / Silicon 界面细化、沟道局部细化以及 source/drain 结区域加密。"
        "最后请先完成这组器件结构与 SDE 代码的整理，并检查关键结构与语法是否完整。"
    )
    compact_electrical_prompt = (
        "我想继续得到这个器件的参考电学结果，请生成适配的 Sentaurus SDevice 仿真脚本，并导出可直接查看的参考 Id-Vg 和 Id-Vd 曲线。"
        "我希望当前工作区中能够看到用于转移特性和输出特性分析的脚本文件、结果曲线以及相关日志，以便检查这条 NMOS 电学仿真链路是否完整。"
    )
    compact_veriloga_prompt = (
        "我想继续构建这个器件的紧凑模型，请整理紧凑模型参数，并导出 Verilog-A 文件。"
        "请把门限电压、电流因子、亚阈值因子以及接口电阻等关键信息整理成参数卡，同时给出模型响应对比图以及可直接查看的 Verilog-A 接口文件。"
    )

    electrical_metrics = _read_json(electrical_assets_root / "ldmos_metrics.json") or {}
    compact_card = _read_json(compact_assets_root / "parameter_card.json") or {}
    compact_metrics = {
        key: compact_card.get(key)
        for key in ("VTH0", "KP", "NFACTOR", "RS", "RD")
        if key in compact_card
    }

    return [
        {
            "order": 1,
            "case_id": "structure_generation_task",
            "title": "结构生成任务",
            "summary": "围绕平面 MOS gate-tunneling 结构，展示自然语言输入、SDE 脚本整理、语法检查与结构结果导出。",
            "prompt": structure_prompt,
            "device_type": "mosfet",
            "simulation_type": "structure_generation",
            "profile": "结构代码生成",
            "capabilities": ["自然语言输入", "SDE代码生成", "结构检查"],
            "artifact_files": ["SDE 脚本", "结构图片", "掺杂分布图"],
            "reference_basis": ["New_Data · MOS_Gate_Tunneling", "平面 MOS gate-tunneling"],
            "is_featured": True,
            "completed_reply": "当前这条结构生成任务已经准备好了，可以直接查看当前工作区中的脚本、图片和日志文件。",
            "phases": [
                {
                    "phase_id": "structure_ready",
                    "initial": True,
                    "stage": "sde_checked",
                    "plan_summary": "先整理 SDE 结构脚本，并完成语法检查。",
                    "task_summary": "完成 MOS_Gate_Tunneling 原型工程的 SDE 脚本整理与语法检查。",
                    "done_criteria": ["SDE 脚本可查看", "语法检查已完成"],
                    "notes": [
                        "已整理平面 MOS 原型工程中的 SDE 结构脚本。",
                        "已完成脚本检查。",
                    ],
                    "steps": [
                        {
                            "step_id": "generate-sde",
                            "title": "生成 SDE 结构代码",
                            "tool_name": "generate_sde_code",
                            "pre_stage": "created",
                            "stage": "sde_generated",
                            "artifact_keys": ["sde_cmd"],
                            "assistant_before": "我先根据这组平面 MOS gate-tunneling 需求整理 SDE 结构脚本，重点保留 PolySilicon gate、超薄 gate oxide 和连续 Silicon 主体的边界与掺杂定义。",
                            "assistant_after": "SDE 脚本已经整理好了，关键材料、接触和局部 mesh 约束都已经写入，接下来我先把语法检查补齐。",
                        },
                        {
                            "step_id": "check-sde",
                            "title": "检查 SDE 语法",
                            "tool_name": "check_sde_syntax",
                            "pre_stage": "sde_generated",
                            "stage": "sde_checked",
                            "artifact_keys": [],
                            "assistant_after": "SDE 脚本和语法检查都已经完成了。",
                        },
                    ],
                    "final_message": "结构脚本和语法检查已经准备好了。",
                    "blocked_reply": "当前这条会话已经完成 SDE 脚本和语法检查。如果要继续，可以直接要求导出结构图和掺杂分布图。",
                    "assets": [
                        {"source": structure_root / "sde_dvs.cmd", "target": "run/sde_dvs.cmd", "artifact_key": "sde_cmd"},
                        {"source": structure_root / "n11_dvs.log", "target": "logs/n11_dvs.log"},
                        {"source": structure_root / "n11_msh.log", "target": "logs/n11_msh.log"},
                        {"source": structure_root / "preprocessor.log", "target": "logs/preprocessor.log"},
                        {"source": structure_root / "gsummary.txt", "target": "logs/gsummary.txt"},
                    ],
                },
                {
                    "phase_id": "structure_visualized",
                    "allowed_from": ("structure_ready",),
                    "stage": "svisual_sde_done",
                    "exact_prompts": (structure_visual_prompt,),
                    "intent_markers": ("图片", "结构图", "掺杂图", "导图", "导出图", "预览", "看图", "png"),
                    "blocked_reply": "当前这条会话已经完成结构脚本和语法检查。如果你要继续，我可以继续把结构图和掺杂分布图导出来。",
                    "plan_summary": "继续导出结构图片和掺杂分布图。",
                    "task_summary": "导出 MOS_Gate_Tunneling 原型工程的结构图片与掺杂分布图。",
                    "done_criteria": ["结构图片可查看", "掺杂分布图可查看"],
                    "notes": ["已导出结构图和掺杂分布图，并保留网格与边界文件。"],
                    "steps": [
                        {
                            "step_id": "export-structure",
                            "title": "导出结构图片",
                            "tool_name": "run_svisual_sde_export",
                            "pre_stage": "sde_checked",
                            "stage": "svisual_sde_done",
                            "artifact_keys": ["mesh", "bnd", "svisual_png", "svisual_doping_png"],
                            "assistant_before": "我继续把结构结果导出来，重点把几何边界、材料分层和掺杂分布都转成可直接查看的结果图。",
                            "assistant_after": "结构图片、掺杂分布图以及对应的 mesh / boundary 文件都已经整理好了，可以直接核对区域定义是否合理。",
                        }
                    ],
                    "final_message": "结构图和掺杂分布图已经准备好了，当前工作区里也保留了对应的网格和边界文件。",
                    "assets": [
                        {"source": structure_root / "n11_msh.tdr", "target": "run/n11_msh.tdr", "artifact_key": "mesh"},
                        {"source": structure_root / "n11_bnd.tdr", "target": "run/n11_bnd.tdr", "artifact_key": "bnd"},
                        {
                            "source": structure_assets_root / "mos_structure.png",
                            "target": "reports/mos_structure.png",
                            "artifact_key": "svisual_png",
                        },
                        {
                            "source": structure_assets_root / "mos_doping_profile.png",
                            "target": "reports/mos_doping_profile.png",
                            "artifact_key": "svisual_doping_png",
                        },
                        {"source": structure_assets_root / "mos_doping_profile.csv", "target": "reports/mos_doping_profile.csv"},
                        {"source": structure_root / "SVisualTcl.log", "target": "logs/SVisualTcl.log"},
                    ],
                },
            ],
        },
        {
            "order": 2,
            "case_id": "electrical_simulation_task",
            "title": "电学仿真任务",
            "summary": "围绕横向硅 LDMOS，展示 SDE 代码整理、SDevice 脚本适配、电学结果运行与曲线导出。",
            "prompt": electrical_prompt,
            "device_type": "ldmos",
            "simulation_type": "electrical_simulation",
            "profile": "电学结果整理",
            "capabilities": ["自然语言输入", "SDE代码生成", "电学仿真", "结果展示"],
            "artifact_files": ["SDE 脚本", "SDevice 脚本", "Id-Vd 曲线", "BV 曲线"],
            "reference_basis": ["DAY5_SJ_LDMOS_SDE_only", "横向硅 LDMOS"],
            "completed_reply": "当前这条电学仿真任务的脚本、曲线和日志都已经准备好了，可以直接查看当前工作区。",
            "phases": [
                {
                    "phase_id": "structure_ready",
                    "initial": True,
                    "stage": "structure_checked",
                    "plan_summary": "先整理器件结构与 SDE 代码，并完成必要检查。",
                    "task_summary": "完成横向硅 LDMOS 的器件结构与 SDE 代码整理和检查。",
                    "done_criteria": ["SDE 脚本可查看", "必要检查已完成"],
                    "notes": [
                        "已整理 LDMOS 器件结构与 SDE 代码。",
                        "已完成 LDMOS 结构检查。",
                    ],
                    "steps": [
                        {
                            "step_id": "generate-structure",
                            "title": "整理器件结构与 SDE 代码",
                            "tool_name": "generate_sde_code",
                            "pre_stage": "created",
                            "stage": "sde_generated",
                            "artifact_keys": ["sde_cmd"],
                            "assistant_before": "我先整理这组横向硅 LDMOS 的器件结构与 SDE 代码，重点保留 drift 区、gate 区、source/body 区和 drain 功率侧结构。",
                            "assistant_after": "SDE 脚本已经准备好了，关键几何、掺杂与接触定义都已经保留下来，接下来我把必要检查补齐。",
                        },
                        {
                            "step_id": "check-structure",
                            "title": "检查 SDE 语法",
                            "tool_name": "check_sde_syntax",
                            "pre_stage": "sde_generated",
                            "stage": "sde_checked",
                            "artifact_keys": [],
                            "assistant_after": "SDE 脚本和必要检查都已经完成了。",
                        },
                    ],
                    "final_message": "SDE 脚本和检查结果已经准备好了。",
                    "blocked_reply": "当前这条会话已经完成 SDE 代码整理。如果要继续，可以直接要求整理 SDevice 脚本并导出输出特性和击穿结果图。",
                    "assets": [
                        {"source": electrical_sde_root / "sde_dvs.cmd", "target": "run/sde_dvs.cmd", "artifact_key": "sde_cmd"},
                        {"source": electrical_sde_root / "sdemodel_msh.cmd", "target": "run/sdemodel_msh.cmd"},
                        {"source": electrical_sde_root / "sdemodel_msh.log", "target": "logs/sdemodel_msh.log"},
                        {"source": electrical_sde_root / "SVisualTcl.log", "target": "logs/SVisualTcl.log"},
                    ],
                },
                {
                    "phase_id": "electrical_ready",
                    "allowed_from": ("structure_ready",),
                    "stage": "svisual_done",
                    "exact_prompts": (electrical_results_prompt,),
                    "intent_markers": ("仿真", "曲线", "电学", "idvd", "bv", "击穿", "sdevice", "运行", "输出特性"),
                    "plan_summary": "继续生成 SDevice 脚本，运行电学结果链路并导出曲线。",
                    "task_summary": "完成 LDMOS_Alpha 原型工程的 SDevice 脚本、电学结果与曲线导出。",
                    "done_criteria": ["SDevice 脚本可查看", "Id-Vd / BV 曲线可查看"],
                    "notes": [
                        "已整理 LDMOS 的 SDevice 脚本。",
                        "已导出输出特性与击穿结果图。",
                    ],
                    "steps": [
                        {
                            "step_id": "generate-sdevice",
                            "title": "生成 SDevice 仿真代码",
                            "tool_name": "generate_sdevice_code",
                            "pre_stage": "sde_checked",
                            "stage": "sdevice_generated",
                            "artifact_keys": ["sdevice_cmd"],
                            "assistant_before": "我继续把 SDE 结构结果接到电学仿真链路上，先整理适配 LDMOS 输出特性和击穿分析的 SDevice 脚本。",
                            "assistant_after": "SDevice 脚本已经准备好了，偏置设置和求解入口也已经补齐，我再检查一遍语法和求解设置。",
                        },
                        {
                            "step_id": "check-sdevice",
                            "title": "检查 SDevice 语法",
                            "tool_name": "check_sdevice_syntax",
                            "pre_stage": "sdevice_generated",
                            "stage": "sdevice_checked",
                            "artifact_keys": [],
                            "assistant_after": "SDevice 脚本已经检查通过，接下来继续整理输出特性和击穿结果。",
                        },
                        {
                            "step_id": "run-sdevice",
                            "title": "运行 SDevice 仿真",
                            "tool_name": "run_sdevice",
                            "pre_stage": "sdevice_checked",
                            "stage": "sdevice_done",
                            "artifact_keys": [],
                            "assistant_after": "仿真结果已经准备好了，我继续把输出特性和击穿结果图导出来，方便直接核对器件工作区间。",
                        },
                        {
                            "step_id": "export-results",
                            "title": "导出仿真结果图",
                            "tool_name": "run_svisual_export",
                            "pre_stage": "sdevice_done",
                            "stage": "svisual_done",
                            "artifact_keys": ["plot_output", "plot_breakdown"],
                            "assistant_after": "输出特性和击穿结果图都已经整理好了，相关日志和中间结果也已经同步保留到当前工作区。",
                        },
                    ],
                    "final_message": "SDevice 脚本、电学曲线和日志都已经准备好了，当前工作区可以直接用来核对输出特性和击穿结果。",
                    "blocked_reply": "当前这条会话已经完成 SDE 代码整理。如果要继续，可以直接要求整理 SDevice 脚本并导出输出特性和击穿结果图。",
                    "assets": [
                        {"source": electrical_root / "sdevice_des.cmd", "target": "run/sdevice_des.cmd", "artifact_key": "sdevice_cmd"},
                        {"source": electrical_root / "IdVd_des.cmd", "target": "run/IdVd_des.cmd"},
                        {"source": electrical_root / "BVdss_des.cmd", "target": "run/BVdss_des.cmd"},
                        {
                            "source": electrical_assets_root / "ldmos_output_curve.png",
                            "target": "reports/ldmos_output_curve.png",
                            "artifact_key": "plot_output",
                        },
                        {
                            "source": electrical_assets_root / "ldmos_breakdown_curve.png",
                            "target": "reports/ldmos_breakdown_curve.png",
                            "artifact_key": "plot_breakdown",
                        },
                        {"source": electrical_root / "n33_des.log", "target": "logs/n33_des.log"},
                        {"source": electrical_root / "n33_des.log", "target": "logs/run_output.log_des.log"},
                        {"source": electrical_root / "n33_des.log", "target": "logs/run_breakdown.log_des.log"},
                        {"source": electrical_root / "n33_des.out", "target": "logs/n33_des.out"},
                        {"source": electrical_root / "n33_des.sta", "target": "logs/n33_des.sta"},
                        {"source": electrical_root / "n33_des.xml", "target": "logs/n33_des.xml"},
                        {"source": electrical_root / "SVisualTcl.log", "target": "logs/SVisualTcl.log"},
                    ],
                    "metrics": electrical_metrics,
                },
            ],
        },
        {
            "order": 3,
            "case_id": "compact_model_task",
            "title": "紧凑模型构建任务",
            "summary": "围绕 180 nm 平面 NMOS，展示器件结构整理、电学曲线组织、参数卡整理与 Verilog-A 导出。",
            "prompt": compact_prompt,
            "device_type": "mosfet",
            "simulation_type": "compact_model",
            "profile": "紧凑模型整理",
            "capabilities": ["自然语言输入", "SDE代码生成", "电学仿真", "Verilog-A导出"],
            "artifact_files": ["SDE 脚本", "SDevice 脚本", "Id-Vg 曲线", "Id-Vd 曲线", "参数卡", "Verilog-A 模型"],
            "reference_basis": ["New_Data · NMOS_180nm_HCI", "180 nm 平面 NMOS"],
            "completed_reply": "当前这条紧凑模型构建任务的脚本、曲线、参数卡和 Verilog-A 文件都已经准备好了，可以直接查看当前工作区。",
            "phases": [
                {
                    "phase_id": "structure_ready",
                    "initial": True,
                    "stage": "structure_checked",
                    "plan_summary": "先整理器件结构与 SDE 代码，并完成必要检查。",
                    "task_summary": "完成 NMOS_180nm_HCI 的器件结构与 SDE 代码整理和检查。",
                    "done_criteria": ["器件结构脚本可查看", "必要检查已完成"],
                    "notes": [
                        "已整理 NMOS 器件结构与 SDE 代码。",
                        "已完成器件结构检查。",
                    ],
                    "steps": [
                        {
                            "step_id": "generate-structure",
                            "title": "生成 SDE 代码",
                            "tool_name": "generate_sde_code",
                            "pre_stage": "created",
                            "stage": "sde_generated",
                            "artifact_keys": ["sde_cmd"],
                            "assistant_before": "我先整理这组 180 nm NMOS 的器件结构与 SDE 代码，重点保留 well、gate stack、STI/LOCOS 和 source/drain 相关流程。",
                            "assistant_after": "SDE 代码已经准备好了，关键器件结构、尺寸约束和 source/drain 相关语义都已经保留下来，接下来我把必要检查补齐。",
                        },
                        {
                            "step_id": "check-structure",
                            "title": "检查 SDE 语法",
                            "tool_name": "check_sde_syntax",
                            "pre_stage": "sde_generated",
                            "stage": "sde_checked",
                            "artifact_keys": [],
                            "assistant_after": "SDE 代码的必要检查都已经完成了。",
                        },
                    ],
                    "final_message": "器件结构与 SDE 代码已经准备好了，当前结果已经适合继续组织参考电学曲线。",
                    "blocked_reply": "当前这条会话已经完成器件结构整理，但现在还不能直接导出 Verilog-A。请先整理参考 Id-Vg 和 Id-Vd 曲线。",
                    "assets": [
                        {"source": compact_sde_root / "nmos_dvs.cmd", "target": "run/nmos_dvs.cmd", "artifact_key": "sde_cmd"},
                        {"source": compact_sde_root / "n3_half_msh.cmd", "target": "run/n3_half_msh.cmd"},
                        {"source": compact_sde_root / "n3_half_msh.log", "target": "logs/n3_half_msh.log"},
                        {"source": compact_sde_root / "n3_dvs.log", "target": "logs/n3_dvs.log"},
                        {"source": compact_sde_root / "SVisualTcl.log", "target": "logs/SVisualTcl.log"},
                    ],
                },
                {
                    "phase_id": "electrical_ready",
                    "allowed_from": ("structure_ready",),
                    "stage": "svisual_done",
                    "exact_prompts": (compact_electrical_prompt,),
                    "intent_markers": ("仿真", "曲线", "电学", "idvg", "idvd", "sdevice", "运行", "输出特性", "转移特性"),
                    "plan_summary": "继续生成 SDevice 脚本，并导出参考 Id-Vg / Id-Vd 曲线。",
                    "task_summary": "完成 NMOS_180nm_HCI 的 SDevice 脚本与参考电学曲线整理。",
                    "done_criteria": ["SDevice 脚本可查看", "Id-Vg 曲线可查看", "Id-Vd 曲线可查看"],
                    "notes": [
                        "已整理 NMOS 的 SDevice 脚本。",
                        "已导出参考 Id-Vg 和 Id-Vd 曲线。",
                    ],
                    "steps": [
                        {
                            "step_id": "generate-sdevice",
                            "title": "生成 SDevice 仿真代码",
                            "tool_name": "generate_sdevice_code",
                            "pre_stage": "sde_checked",
                            "stage": "sdevice_generated",
                            "artifact_keys": ["sdevice_cmd"],
                            "assistant_before": "我继续把器件结构结果接到电学链路上，先整理适配 NMOS 转移特性和输出特性的 SDevice 脚本。",
                            "assistant_after": "SDevice 脚本已经准备好了，Id-Vg 与 Id-Vd 分析入口都已经补齐，我再检查一遍语法和求解设置。",
                        },
                        {
                            "step_id": "check-sdevice",
                            "title": "检查 SDevice 语法",
                            "tool_name": "check_sdevice_syntax",
                            "pre_stage": "sdevice_generated",
                            "stage": "sdevice_checked",
                            "artifact_keys": [],
                            "assistant_after": "SDevice 脚本已经检查通过，接下来继续整理参考 Id-Vg 和 Id-Vd 曲线。",
                        },
                        {
                            "step_id": "run-sdevice",
                            "title": "运行 SDevice 仿真",
                            "tool_name": "run_sdevice",
                            "pre_stage": "sdevice_checked",
                            "stage": "sdevice_done",
                            "artifact_keys": [],
                            "assistant_after": "仿真结果已经准备好了，我继续把参考 Id-Vg 和 Id-Vd 曲线导出来。",
                        },
                        {
                            "step_id": "export-results",
                            "title": "导出仿真结果图",
                            "tool_name": "run_svisual_export",
                            "pre_stage": "sdevice_done",
                            "stage": "svisual_done",
                            "artifact_keys": ["plot_transfer", "plot_output"],
                            "assistant_after": "参考 Id-Vg 和 Id-Vd 曲线都已经整理好了。",
                        },
                    ],
                    "final_message": "SDevice 脚本和参考电学曲线已经准备好了。",
                    "blocked_reply": "当前这条会话已经完成器件结构整理。如果要继续，可以直接要求整理参考 Id-Vg 和 Id-Vd 曲线。",
                    "assets": [
                        {"source": compact_root / "sdevice_des.cmd", "target": "run/sdevice_des.cmd", "artifact_key": "sdevice_cmd"},
                        {"source": compact_root / "IdVg_des.cmd", "target": "run/IdVg_des.cmd"},
                        {"source": compact_root / "IdVd_des.cmd", "target": "run/IdVd_des.cmd"},
                        {"source": compact_root / "CV_des.cmd", "target": "run/CV_des.cmd"},
                        {
                            "source": compact_assets_root / "nmos_transfer_curve.png",
                            "target": "reports/nmos_transfer_curve.png",
                            "artifact_key": "plot_transfer",
                        },
                        {
                            "source": compact_assets_root / "nmos_output_curve.png",
                            "target": "reports/nmos_output_curve.png",
                            "artifact_key": "plot_output",
                        },
                        {"source": compact_root / "n21_des.log", "target": "logs/n21_des.log"},
                        {"source": compact_root / "n21_des.out", "target": "logs/n21_des.out"},
                        {"source": compact_root / "n21_des.sta", "target": "logs/n21_des.sta"},
                        {"source": compact_root / "SVisualTcl.log", "target": "logs/SVisualTcl.log"},
                    ],
                },
                {
                    "phase_id": "compact_model_ready",
                    "allowed_from": ("electrical_ready",),
                    "stage": "validated",
                    "exact_prompts": (compact_veriloga_prompt,),
                    "intent_markers": ("verilog", "verilog-a", "va", "紧凑模型", "参数卡", "导出va"),
                    "plan_summary": "继续整理参数卡、拟合图并导出 Verilog-A。",
                    "task_summary": "完成 NMOS_180nm_HCI 原型工程的参数卡、拟合图与 Verilog-A 导出。",
                    "done_criteria": ["参数卡可查看", "拟合对比图可查看", "Verilog-A 模型可查看"],
                    "notes": [
                        "已整理紧凑模型参数卡与拟合对比图。",
                        "已导出 Verilog-A 接口文件。",
                    ],
                    "steps": [
                        {
                            "step_id": "inspect-curves",
                            "title": "整理电学曲线",
                            "tool_name": "inspect_electrical_curves",
                            "pre_stage": "svisual_done",
                            "stage": "sdevice_done",
                            "artifact_keys": [],
                            "assistant_before": "我先把已有电学曲线重新整理一遍，确认参数提取和拟合对比使用的是同一组参考结果。",
                            "assistant_after": "曲线已经整理好了，接下来开始提取紧凑模型参数并组织参数卡。",
                        },
                        {
                            "step_id": "extract-compact",
                            "title": "提取紧凑模型参数",
                            "tool_name": "extract_compact_parameters",
                            "pre_stage": "sdevice_done",
                            "stage": "validated",
                            "artifact_keys": ["compact_model_plot", "compact_model_card", "compact_model_report"],
                            "assistant_after": "参数卡和模型响应图都已经准备好了，关键门限、电流能力、亚阈值和接口参数已经整理出来，最后我把 Verilog-A 一并导出来。",
                        },
                        {
                            "step_id": "export-veriloga",
                            "title": "导出 Verilog-A",
                            "tool_name": "export_verilog_a",
                            "pre_stage": "validated",
                            "stage": "validated",
                            "artifact_keys": ["verilog_a_model"],
                            "assistant_after": "Verilog-A 文件已经导出来了，当前工作区里已经保留了参数卡、拟合图和接口文件。",
                        },
                    ],
                    "final_message": "参数卡、拟合图和 Verilog-A 文件都已经准备好了。",
                    "blocked_reply": "当前已经有参考电学结果了。如果要继续，可以直接要求整理参数卡、拟合图并导出 Verilog-A。",
                    "assets": [
                        {
                            "source": compact_assets_root / "compact_model_overlay.png",
                            "target": "reports/compact_model_overlay.png",
                            "artifact_key": "compact_model_plot",
                        },
                        {
                            "source": compact_assets_root / "parameter_card.json",
                            "target": "reports/parameter_card.json",
                            "artifact_key": "compact_model_card",
                        },
                        {
                            "source": compact_assets_root / "compact_model_summary.md",
                            "target": "reports/compact_model_summary.md",
                            "artifact_key": "compact_model_report",
                        },
                        {
                            "source": compact_assets_root / "nmos_compact_model.va",
                            "target": "run/nmos_compact_model.va",
                            "artifact_key": "verilog_a_model",
                        },
                    ],
                    "metrics": compact_metrics,
                },
            ],
        },
    ]


def _load_prompt(prompt_path: Path, *, default: str) -> str:
    payload = _read_json(prompt_path)
    if not payload:
        return default
    question = payload.get("question") or {}
    for key in ("medium", "simple", "complex"):
        text = str(question.get(key) or "").strip()
        if text:
            return text
    return default


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _compute_transistor_electrical_metrics(*, transfer_curve: Path, output_curve: Path) -> dict[str, float]:
    transfer_rows = _read_curve(transfer_curve)
    output_rows = _read_curve(output_curve)
    if not transfer_rows or not output_rows:
        return {}

    transfer_x = [item[0] for item in transfer_rows]
    transfer_y = [item[1] for item in transfer_rows]
    output_x = [item[0] for item in output_rows]
    output_y = [item[1] for item in output_rows]

    gm_values = []
    for index, gate_voltage in enumerate(transfer_x):
        if index == 0:
            slope = (transfer_y[index + 1] - transfer_y[index]) / (transfer_x[index + 1] - gate_voltage)
        elif index == len(transfer_x) - 1:
            slope = (transfer_y[index] - transfer_y[index - 1]) / (gate_voltage - transfer_x[index - 1])
        else:
            slope = (transfer_y[index + 1] - transfer_y[index - 1]) / (transfer_x[index + 1] - transfer_x[index - 1])
        gm_values.append(slope)

    gm_max = max(gm_values)
    gm_index = gm_values.index(gm_max)
    ion = max(transfer_y)
    ioff = min(transfer_y)
    vth_index = next((idx for idx, current in enumerate(transfer_y) if current >= 1e-5), gm_index)
    threshold_voltage = transfer_x[vth_index]

    ss_points = [(x, y) for x, y in transfer_rows if 1e-7 < y < 1e-4]
    subthreshold_swing = 0.0
    if len(ss_points) >= 2:
        xs = [item[0] for item in ss_points]
        ys = [item[1] for item in ss_points]
        sum_x = sum(xs)
        sum_y = sum(math.log10(value) for value in ys)
        sum_xx = sum(value * value for value in xs)
        sum_xy = sum(x * math.log10(y) for x, y in zip(xs, ys))
        denominator = len(xs) * sum_xx - sum_x * sum_x
        if denominator:
            slope = (len(xs) * sum_xy - sum_x * sum_y) / denominator
            if slope:
                subthreshold_swing = 1000.0 / slope

    high_vd_points = [(x, y) for x, y in output_rows if x >= max(output_x) * 0.7]
    lambda_value = 0.0
    if len(high_vd_points) >= 2:
        xs = [item[0] for item in high_vd_points]
        ys = [item[1] for item in high_vd_points]
        sum_x = sum(xs)
        sum_y = sum(ys)
        sum_xx = sum(value * value for value in xs)
        sum_xy = sum(x * y for x, y in zip(xs, ys))
        denominator = len(xs) * sum_xx - sum_x * sum_x
        if denominator:
            slope = (len(xs) * sum_xy - sum_x * sum_y) / denominator
            intercept = (sum_y - slope * sum_x) / len(xs)
            if intercept:
                lambda_value = slope / intercept

    return {
        "threshold_voltage": round(threshold_voltage, 4),
        "on_current": ion,
        "off_current": ioff,
        "on_off_ratio": ion / max(ioff, 1e-30),
        "subthreshold_swing_mv_dec": round(subthreshold_swing, 2) if subthreshold_swing else 0.0,
        "gm_max": gm_max,
        "lambda": round(lambda_value, 5) if lambda_value else 0.0,
        "idsat": max(output_y),
        "drain_bias_max": max(output_x),
    }


def _compute_breakdown_voltage(curve_path: Path) -> float | None:
    rows = _read_curve(curve_path)
    if not rows:
        return None
    return round(max(abs(x_value) for x_value, _ in rows), 4)


def _read_curve(path: Path) -> list[tuple[float, float]]:
    try:
        rows = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    parsed: list[tuple[float, float]] = []
    for line in rows[1:]:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            parsed.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    return parsed
