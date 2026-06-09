from __future__ import annotations

import sys

"""主流程编排层 —— TCAD Agent 状态机。

该模块负责把"需求 -> 代码生成 -> 工具执行 -> 物理验证"串成可重入的状态机。

┌─────────────────────────────────────────────────────────────────────┐
│  状态机设计                                                         │
│                                                                     │
│  核心思路：将 TCAD 仿真流程建模为线性状态机，每个操作对应一次状态    │
│  跃迁。任何步骤失败后可从当前阶段恢复（而非重头开始），因为每次      │
│  状态变更都持久化到 state.json。                                    │
│                                                                     │
│  状态跃迁路径：                                                     │
│    created → sde_generated → sde_checked → sde_done                 │
│    → tdr_inspected → sdevice_generated → sdevice_checked            │
│    → sdevice_done → svisual_done → validated                        │
│                                                                     │
│  任一步骤可产生 *_failed 分支，由用户或 agent 决定是否重试。        │
└─────────────────────────────────────────────────────────────────────┘
"""

import json
import re
import subprocess
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from .coverage_audit import CoverageAudit, audit_generated_artifact
from .benchmark_catalog import load_education_benchmark
from .llm_engine import LLMDeckEngine
from .core import (
    DebugTracer,
    PlanStep,
    SessionSpec,
    SessionState,
    StepResult,
    TaskTodo,
    Targets,
    ValidationResult,
    preview_text,
    to_jsonable,
)
from .failure_taxonomy import classify_failure, write_failure_report
from .reference_service import ReferenceService
from .sentaurus_ops import SentaurusOps
from .shared_contracts import emit_run_manifest
from .task_spec import (
    SDEVICE_CHAIN_TOOLS,
    TaskSpec,
    infer_target_artifact,
    normalize_target_artifact,
    target_blocks_sdevice,
    target_satisfied,
)
from .task_contract import build_execution_plan, finalize_plan_if_done, replan_failed_tail, select_next_plan_step, update_plan_step
from .validate import PhysicalValidator


class TCADAgentSystem:
    """TCAD Agent 主系统（默认单会话）。

    职责：
    - 管理会话生命周期（创建 / 恢复 / 持久化）
    - 按标准 10 步流水线编排 Sentaurus 工具调用
    - 提供启发式计划器，将自然语言映射为操作序列
    - 统一输出格式，供 CLI / MCP / 交互模式消费
    """

    DEFAULT_SESSION = "default"

    # 主 Agent 直接输出并执行 MCP 工具名，不再维护固定 operation->tool 映射链。
    MCP_TOOL_NOTES = {
        "create_session": "创建或重置默认会话，并解析用户需求",
        "show_state": "查看当前会话状态、产物路径与指标",
        "describe_tools": "查看 MCP 工具目录及启用状态",
        "run_bash": "执行 Bash 命令（如 ls/cat/head 等）并返回输出",
        "generate_sde_code": "调用 LLM 生成 SDE 结构代码",
        "check_sde_syntax": "运行 sde -S 做 SDE 语法检查",
        "run_sde": "运行 sde -e -l 编译并生成 mesh/tdr",
        "run_svisual_sde_export": "运行 svisual TCL 导出 SDE/TDR 结构 PNG",
        "inspect_tdr": "运行 tdx -info 检查 TDR 结构与材料信息",
        "tdx_convert": "运行 tdx 转换命令（tdr/tif/dfise/plx/ivl 等）",
        "tdx_tclcmd": "运行 tdx -tclcmd 执行单条 Tcl 命令",
        "generate_sdevice_code": "调用 LLM 生成 SDevice 仿真代码",
        "check_sdevice_syntax": "运行 sdevice -P 做 SDevice 预检查",
        "run_sdevice": "运行 sdevice --exit-on-failure 执行电学仿真",
        "run_svisual_export": "运行 svisual TCL 导出 SDevice/PLT 曲线 PNG/文本",
        "run_svisual_tcl_script": "运行自定义 svisual Tcl 脚本（batchx）",
        "run_svisual_cutline_export": "运行 svisual cutline 并导出 CSV/PNG",
        "run_inspect_script": "运行 inspect 脚本并提取曲线特征/指标",
        "validate_results": "执行结构、曲线与指标联合验证",
    }
    SKILL_NOTES = {
        "main_agent": "主 Agent 系统提示词与职责约束",
        "planner": "主 Agent 的操作规划策略与规则",
        "sde_codegen": "SDE 代码生成提示词与约束",
        "sdevice_codegen": "SDevice 代码生成提示词与约束",
    }
    MCP_TOOL_ORDER = [
        "create_session",
        "show_state",
        "describe_tools",
        "run_bash",
        "generate_sde_code",
        "check_sde_syntax",
        "run_sde",
        "run_svisual_sde_export",
        "inspect_tdr",
        "tdx_convert",
        "tdx_tclcmd",
        "generate_sdevice_code",
        "check_sdevice_syntax",
        "run_sdevice",
        "run_svisual_export",
        "run_svisual_tcl_script",
        "run_svisual_cutline_export",
        "run_inspect_script",
        "validate_results",
    ]
    SKILL_ORDER = [
        "main_agent",
        "planner",
        "sde_codegen",
        "sdevice_codegen",
    ]
    IMAGE_PATH_RE = re.compile(r"(?P<path>(?:/|\.{1,2}/)[^\s'\"，。；：！？、]+?\.(?:png|jpg|jpeg|webp))", re.IGNORECASE)
    UPLOADED_ASSET_PREFIX = "uploaded_asset::"

    # ━━━━━━━━━━━━━━━━ 初始化与目录管理 ━━━━━━━━━━━━━━━━

    def __init__(self, workspace: Path, runtime_root: Path | None = None):
        """初始化运行目录、追踪器、生成器、执行器、验证器。

        runtime_root 允许在网关模式下为每个实例绑定独立运行目录。
        """
        self.workspace = workspace
        self.runtime_root = runtime_root.resolve() if runtime_root is not None else workspace / "runtime" / self.DEFAULT_SESSION
        self.runtime_root.mkdir(parents=True, exist_ok=True)

        self.tracer = DebugTracer(workspace)
        self.tracer.bind_session(self.runtime_root, self.DEFAULT_SESSION)

        self.llm = LLMDeckEngine(workspace, tracer=self.tracer)
        self.ops = SentaurusOps(tracer=self.tracer)
        self.validator = PhysicalValidator(tracer=self.tracer)
        self.reference_service = ReferenceService(workspace)
        # 内部 MCP 工具总线：主 Agent 内部也按 MCP 工具名调用能力，
        # 与外部 stdio MCP 共享同一套工具语义。
        from mcp.tool_service import MCPToolService

        self.mcp_tools = MCPToolService(workspace, agent=self, tracer=self.tracer)

        self.tracer.event(
            "MainAgent",
            "init",
            {"workspace": str(workspace)},
            session_id=self.DEFAULT_SESSION,
        )

    @property
    def state_file(self) -> Path:
        """默认会话状态文件路径。"""
        return self.runtime_root / "state.json"

    def _ensure_runtime_layout(self) -> None:
        """确保 run/logs/reports 目录存在。"""
        for sub in ["run", "logs", "reports"]:
            (self.runtime_root / sub).mkdir(parents=True, exist_ok=True)

    def _ensure_inputs_dir(self) -> Path:
        inputs_dir = self.runtime_root / "inputs"
        inputs_dir.mkdir(parents=True, exist_ok=True)
        return inputs_dir

    def _clear_runtime_outputs(self) -> None:
        """清空默认会话运行产物，但保留目录结构。

        每次 create_session 时调用，避免上次残留文件干扰新会话。
        """
        self._ensure_runtime_layout()
        for sub in ["run", "logs", "reports"]:
            d = self.runtime_root / sub
            for child in d.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)

    # ━━━━━━━━━━━━━━━━ 状态持久化与恢复 ━━━━━━━━━━━━━━━━

    def _save_state(self, state: SessionState) -> None:
        """持久化会话状态到 state.json。

        每次操作执行完毕后立即写入，保证断点续跑能力。
        """
        self._ensure_runtime_layout()
        payload = json.dumps(to_jsonable(state), ensure_ascii=False, indent=2)
        self.state_file.write_text(payload, encoding="utf-8")
        try:
            emit_run_manifest(self.runtime_root, state)
        except Exception as exc:  # pragma: no cover - manifest 不应影响主状态持久化
            self.tracer.event(
                "MainAgent",
                "manifest_emit_failed",
                {"error": str(exc), "runtime_root": str(self.runtime_root)},
                session_id=state.session_id,
            )

    def _load_state(self) -> SessionState:
        """从 state.json 读取并重建会话状态。

        将 JSON 扁平结构还原为 SessionState 数据类，
        包括嵌套的 Targets 对象重建。
        """
        if not self.state_file.exists():
            raise FileNotFoundError("No active default session. Use create_session first.")
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        spec_data = data["spec"]
        targets = spec_data.get("targets", {})
        spec = SessionSpec(
            requirement=spec_data["requirement"],
            device_type=spec_data.get("device_type", "unspecified"),
            simulation_type=spec_data.get("simulation_type", "unspecified"),
            target_artifact=normalize_target_artifact(spec_data.get("target_artifact", "unspecified")),
            parameters=spec_data.get("parameters", {}),
            targets=Targets(
                ion_min=targets.get("ion_min"),
                ioff_max=targets.get("ioff_max"),
                ss_max_mv_dec=targets.get("ss_max_mv_dec"),
            ),
            task_spec=TaskSpec.from_payload(spec_data.get("task_spec")),
        )

        return SessionState(
            session_id=data.get("session_id", self.DEFAULT_SESSION),
            session_dir=Path(data.get("session_dir", str(self.runtime_root))),
            spec=spec,
            stage=data.get("stage", "created"),
            artifacts=data.get("artifacts", {}),
            metrics=data.get("metrics", {}),
            notes=data.get("notes", []),
            task_summary=str(data.get("task_summary", "") or ""),
            done_criteria=[str(item) for item in (data.get("done_criteria", []) or []) if str(item)],
            todos=[
                TaskTodo(
                    content=str(item.get("content", "") or ""),
                    status=str(item.get("status", "pending") or "pending"),
                )
                for item in (data.get("todos", []) or [])
                if isinstance(item, dict) and str(item.get("content", "") or "").strip()
            ],
            current_step=str(data.get("current_step", "") or ""),
            blocker=str(data.get("blocker", "") or ""),
            next_step_hint=str(data.get("next_step_hint", "") or ""),
            plan_steps=[
                PlanStep(
                    step_id=str(item.get("step_id", "") or ""),
                    title=str(item.get("title", "") or ""),
                    tool_name=str(item.get("tool_name", "") or ""),
                    tool_args=dict(item.get("tool_args") or {}),
                    status=str(item.get("status", "pending") or "pending"),
                    expected_artifacts=[str(x) for x in (item.get("expected_artifacts", []) or []) if str(x)],
                    notes=[str(x) for x in (item.get("notes", []) or []) if str(x)],
                    attempt=int(item.get("attempt", 1) or 1),
                    narration_pre=str(item.get("narration_pre", "") or ""),
                    narration_post_success=str(item.get("narration_post_success", "") or ""),
                    narration_post_failure=str(item.get("narration_post_failure", "") or ""),
                )
                for item in (data.get("plan_steps", []) or [])
                if isinstance(item, dict) and str(item.get("step_id", "") or "").strip()
            ],
            plan_id=str(data.get("plan_id", "") or ""),
            plan_attempt=int(data.get("plan_attempt", 0) or 0),
        )

    def _dump(self, state: SessionState, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """统一输出格式（用于 CLI/MCP 返回）。"""
        out = to_jsonable(state)
        if extra:
            out.update(to_jsonable(extra))
        return out

    @staticmethod
    def _format_targets(state: SessionState) -> str:
        t = state.spec.targets
        parts = []
        if t.ion_min is not None:
            parts.append(f"Ion >= {t.ion_min:g}")
        if t.ioff_max is not None:
            parts.append(f"Ioff <= {t.ioff_max:g}")
        if t.ss_max_mv_dec is not None:
            parts.append(f"SS <= {t.ss_max_mv_dec:g} mV/dec")
        return ", ".join(parts) if parts else "(未指定)"

    @staticmethod
    def _format_parameters(state: SessionState) -> str:
        if not state.spec.parameters:
            return "(未指定)"
        return ", ".join(f"{k}={state.spec.parameters[k]}" for k in sorted(state.spec.parameters))

    @staticmethod
    def _format_task_spec(state: SessionState) -> str:
        if not state.spec.task_spec.has_content():
            return "(未指定)"
        return "\n".join(state.spec.task_spec.as_lines())

    def _build_codegen_requirement(self, state: SessionState, phase: str) -> str:
        """构造给 SDE/SDevice 生成器的中间语义描述。

        目标：避免把用户输入原句直接“硬转发”给脆弱脚本生成器，
        先由主 Agent 汇总成稳定的结构化约束再下发。
        """
        raw_req = (state.spec.requirement or "").strip()
        last_instruction = (state.artifacts.get("last_user_instruction", "") or "").strip()
        lines = [
            f"任务阶段: {phase}",
            "【原始需求】",
            raw_req or "(空)",
        ]
        if last_instruction and last_instruction != raw_req:
            lines.extend(["", "【本轮补充指令】", last_instruction])
        lines.extend(
            [
                "",
                "【结构化规格】",
                f"device_type={state.spec.device_type}",
                f"simulation_type={state.spec.simulation_type}",
                f"target_artifact={state.spec.target_artifact}",
                f"parameters={self._format_parameters(state)}",
                f"targets={self._format_targets(state)}",
                f"current_stage={state.stage}",
                "task_spec:",
                self._format_task_spec(state),
            ]
        )
        if phase == "generate_sde":
            lines.extend(
                [
                    "",
                    "【阶段过滤规则】",
                    "仅保留 SDE 所需信息：几何、材料、掺杂、接触、网格。",
                    "忽略与 SDE 代码无关的执行/汇报语句（例如“告诉我路径”“导出图片位置”“给出指标”）。",
                ]
            )
        elif phase == "generate_sdevice":
            lines.extend(
                [
                    "",
                    "【阶段过滤规则】",
                    "仅保留 SDevice 所需信息：Electrode、Physics、Math、Solve、扫描与输出。",
                    "接触名必须严格引用现有网格中的名称（大小写敏感）。",
                    "忽略与 SDevice deck 无关的结构绘图与路径描述语句。",
                ]
            )
        return "\n".join(lines)

    def _llm_parse_requirement(self, requirement: str) -> SessionSpec | None:
        """用 LLM 解析用户需求为结构化规格。"""
        system_prompt = (
            self.llm.skills.load("main_agent", "")
            + "\n\n你是 TCAD 需求结构化解析器。"
            "请把用户需求解析为 JSON。"
            "仅输出 JSON，不要输出解释文字。"
            '格式: {"device_type":"...","simulation_type":"...","target_artifact":"...","parameters":{},"targets":{"ion_min":null,"ioff_max":null,"ss_max_mv_dec":null},"task_spec":{"geometry":[],"materials":[],"contacts":[],"doping":[],"mesh":[],"simulation":[],"outputs":[],"constraints":[]}}'
        )
        user_prompt = (
            f"用户需求:\n{requirement}\n\n"
            "要求:\n"
            "1) device_type、simulation_type 用用户语义最贴近的短词；无法确定可填 \"unspecified\"。\n"
            "2) target_artifact 只能从以下值中选择最贴近的一项：structure, structure_png, tdr_info, sdevice_cmd, iv_curve, validation_report, full_chain, text_answer, state_view, tool_list, unspecified。\n"
            "3) parameters 仅保留可明确提取的数值键值。\n"
            "4) targets 中未知项填 null。\n"
            "5) task_spec 要把用户需求拆成声明式约束列表；每个 section 只放与该 section 直接相关的条目。\n"
            "6) 必须返回合法 JSON。"
        )
        try:
            raw = self.llm.chat_main(
                [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                verbose=False,
            )
        except Exception as exc:
            self.tracer.event(
                "MainAgent",
                "llm_requirement_parse_error",
                {"error": str(exc)},
                session_id=self.DEFAULT_SESSION,
            )
            return None

        payload = self._extract_json_block(raw)
        if not payload:
            self.tracer.event(
                "MainAgent",
                "llm_requirement_parse_invalid_json",
                {"raw_preview": preview_text(raw, 1200)},
                session_id=self.DEFAULT_SESSION,
            )
            return None

        try:
            device_type = str(payload.get("device_type", "")).strip().lower() or "unspecified"
            simulation_type = str(payload.get("simulation_type", "")).strip() or "unspecified"
            target_artifact = normalize_target_artifact(str(payload.get("target_artifact", "unspecified")))
            parameters_raw = payload.get("parameters", {}) or {}
            parameters: dict[str, float] = {}
            if isinstance(parameters_raw, dict):
                for k, v in parameters_raw.items():
                    try:
                        parameters[str(k)] = float(v)
                    except Exception:
                        continue

            targets_raw = payload.get("targets", {}) or {}
            ion_min = targets_raw.get("ion_min") if isinstance(targets_raw, dict) else None
            ioff_max = targets_raw.get("ioff_max") if isinstance(targets_raw, dict) else None
            ss_max = targets_raw.get("ss_max_mv_dec") if isinstance(targets_raw, dict) else None
            targets = Targets(
                ion_min=float(ion_min) if ion_min is not None else None,
                ioff_max=float(ioff_max) if ioff_max is not None else None,
                ss_max_mv_dec=float(ss_max) if ss_max is not None else None,
            )
            return SessionSpec(
                requirement=requirement.strip(),
                device_type=device_type,
                simulation_type=simulation_type,
                target_artifact=target_artifact,
                parameters=parameters,
                targets=targets,
                task_spec=TaskSpec.from_payload(payload.get("task_spec")),
            )
        except Exception as exc:
            self.tracer.event(
                "MainAgent",
                "llm_requirement_parse_invalid_payload",
                {"error": str(exc), "payload_preview": preview_text(json.dumps(payload, ensure_ascii=False), 1200)},
                session_id=self.DEFAULT_SESSION,
            )
            return None

    def _llm_compose_codegen_brief(self, state: SessionState, phase: str) -> str | None:
        """让主 Agent 先生成“可执行工程简报”，再下发给 SDE/SDevice 子模型。"""
        system_prompt = (
            self.llm.skills.load("main_agent", "")
            + "\n\n"
            + self.llm.skills.load("planner", "")
            + "\n\n你是 TCAD 主 Agent。请把用户需求重写为可执行工程简报，供子模型生成 deck。"
            "输出纯文本，不要 JSON，不要代码块。"
        )
        user_prompt = (
            f"phase={phase}\n"
            f"stage={state.stage}\n"
            f"原始需求:\n{state.spec.requirement}\n\n"
            f"本轮补充:\n{state.artifacts.get('last_user_instruction', '')}\n\n"
            f"device_type={state.spec.device_type}\n"
            f"simulation_type={state.spec.simulation_type}\n"
            f"target_artifact={state.spec.target_artifact}\n"
            f"parameters={self._format_parameters(state)}\n"
            f"targets={self._format_targets(state)}\n\n"
            f"task_spec:\n{self._format_task_spec(state)}\n\n"
            "请输出工程简报，至少覆盖：\n"
            "1) 目标器件与维度/材料/掺杂/接触约束。\n"
            "2) 需要的模型、偏置扫描与输出文件。\n"
            "3) 不确定信息明确写“需模型自行补全且保持物理合理”。\n"
            "4) 必须避免主观臆测和与用户冲突的设定。"
        )
        if phase == "generate_sde":
            user_prompt += (
                "\n5) 仅输出 SDE 相关约束：几何/材料/掺杂/接触/mesh。"
                "\n6) 忽略执行层面的汇报性语句（例如“告诉我png路径”“给指标”）。"
                "\n7) 不要混入 SDevice/Plot/CurrentPlot/Quasistationary 要求。"
            )
        elif phase == "generate_sdevice":
            user_prompt += (
                "\n5) 仅输出 SDevice 相关约束：Electrode/Physics/Math/Solve/扫描/CurrentPlot。"
                "\n6) 接触名必须声明为“与网格接触名逐字一致（大小写敏感）”。"
                "\n7) 不要混入 SDE 结构构建、svisual 路径汇报等内容。"
            )
        try:
            txt = self.llm.chat_main(
                [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                verbose=False,
            ).strip()
            if not txt:
                return None
            return preview_text(txt, 8000)
        except Exception as exc:
            self.tracer.event(
                "MainAgent",
                "llm_compose_codegen_brief_error",
                {"phase": phase, "error": str(exc)},
                session_id=state.session_id,
            )
            return None

    def _llm_route_intent(self, instruction: str, state: SessionState | None) -> tuple[str, str]:
        """用 LLM 路由本轮意图：execute 或 chat。"""
        stage = state.stage if state else "no_session"
        system_prompt = (
            self.llm.skills.load("main_agent", "")
            + "\n\n你是 TCAD 请求路由器。判断本轮是要执行工具还是纯语言回答。"
            "仅输出 JSON，不要输出其它文字。"
            '格式: {"mode":"execute|chat","reason":"..."}'
        )
        user_prompt = (
            f"用户输入:\n{instruction}\n\n"
            f"当前stage={stage}\n"
            "请结合用户目标与当前状态自主判断：\n"
            "- 若需要实际操作工具，返回 mode=execute\n"
            "- 若只需文字交流，返回 mode=chat\n"
            "- 只要用户目标包含“生成/运行/仿真/导出/提取结果”这类可执行诉求，优先 mode=execute\n"
            "- 若用户询问当前会话产物（图片/文件/日志/目录）内容，也返回 mode=execute\n"
            "- 只有在用户明确是概念问答、解释、讨论时才返回 mode=chat"
        )
        try:
            raw = self.llm.chat_main(
                [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                verbose=False,
            )
            payload = self._extract_json_block(raw)
            if isinstance(payload, dict):
                mode = str(payload.get("mode", "")).strip().lower()
                reason = str(payload.get("reason", "llm_router")).strip() or "llm_router"
                if mode in {"execute", "chat"}:
                    return mode, reason
        except Exception:
            pass
        return ("execute", "router_unavailable")

    def _extract_image_path_candidates(self, instruction: str) -> list[str]:
        matches = [m.group("path") for m in self.IMAGE_PATH_RE.finditer(instruction or "")]
        out: list[str] = []
        seen: set[str] = set()
        for item in matches:
            if item not in seen:
                out.append(item)
                seen.add(item)
        return out

    @staticmethod
    def _is_explicit_image_read_request(instruction: str) -> bool:
        """只在明确“读图/解释图内容”时触发直接多模态短路。"""
        low = (instruction or "").lower()
        if not low.strip():
            return False
        phrases = (
            "看图",
            "读图",
            "识图",
            "分析图片",
            "分析这张图",
            "分析该图",
            "查看图片",
            "查看这张图",
            "解释图片",
            "解释这张图",
            "根据图片",
            "根据这张图",
            "图里有什么",
            "图中有什么",
            "这是什么结构",
            "这是一个什么结构",
            "这张图是什么",
            "图片里有什么",
            "结构图里有什么",
            "请说明这张图",
            "请解释这张图",
        )
        return any(p in low for p in phrases)

    def _resolve_image_path_from_instruction(self, instruction: str, state: SessionState) -> Path | None:
        if not self._is_explicit_image_read_request(instruction):
            return None

        instruction_lower = (instruction or "").lower()
        for key, stored_path, _role, _registered_at in self._uploaded_asset_entries(state):
            file_name = key[len(self.UPLOADED_ASSET_PREFIX) :]
            if not file_name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                continue
            if file_name.lower() not in instruction_lower:
                continue
            p = Path(stored_path).expanduser()
            if p.exists() and p.is_file():
                return p

        candidates = self._extract_image_path_candidates(instruction)
        for raw in candidates:
            p = Path(raw).expanduser()
            if not p.is_absolute():
                p = (state.session_dir / p).resolve()
            if p.exists() and p.is_file():
                return p

        for key in ("svisual_sde_png", "svisual_png"):
            raw = str(state.artifacts.get(key, "")).strip()
            if raw:
                p = Path(raw).expanduser()
                if p.exists() and p.is_file():
                    return p

        reports_dir = state.session_dir / "reports"
        if reports_dir.exists():
            pngs = sorted(reports_dir.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
            if pngs:
                return pngs[0]
        return None

    def _direct_multimodal_image_reply(self, instruction: str, state: SessionState) -> dict[str, Any] | None:
        image_path = self._resolve_image_path_from_instruction(instruction, state)
        if image_path is None:
            return None
        try:
            answer = self.llm.chat_with_image(question=instruction.strip(), image_path=image_path).strip()
        except Exception as exc:
            self.tracer.event(
                "MainAgent",
                "direct_multimodal_image_reply_error",
                {"error": str(exc), "image_path": str(image_path)},
                session_id=state.session_id,
            )
            return None
        if not answer:
            return None

        state.artifacts["last_image_file"] = str(image_path)
        state.notes.append("Direct multimodal image reply done.")
        self._save_state(state)
        return {
            "session_id": state.session_id,
            "stage": state.stage,
            "done": True,
            "next_operation": None,
            "next_tool": None,
            "reason": "direct_multimodal_image_reply",
            "source": "multimodal_direct",
            "assistant_reply": preview_text(answer, 8000),
            "assistant_pre": "",
            "assistant_post_success": "",
            "assistant_post_failure": "",
        }


    def _write_coverage_audit(
        self,
        state: SessionState,
        *,
        phase: str,
        code_text: str,
        output_name: str,
        tdr_info: str = "",
    ) -> CoverageAudit | None:
        if not state.spec.task_spec.has_content():
            return None
        try:
            audit = audit_generated_artifact(
                lambda messages: self.llm.chat_main(messages, verbose=False),
                phase=phase,
                device_type=state.spec.device_type,
                target_artifact=state.spec.target_artifact,
                task_spec=state.spec.task_spec,
                code_text=code_text,
                tdr_info=tdr_info,
            )
        except Exception as exc:
            self.tracer.event(
                "MainAgent",
                "coverage_audit_error",
                {"phase": phase, "error": str(exc)},
                session_id=state.session_id,
            )
            return None

        rep = state.session_dir / "reports" / output_name
        rep.write_text(json.dumps(to_jsonable(audit), ensure_ascii=False, indent=2), encoding="utf-8")
        state.artifacts[f"{phase}_coverage_audit"] = str(rep)
        state.notes.append(f"{phase} coverage audit: {'pass' if audit.success else 'warn'}")
        self._save_state(state)
        return audit

    def _record_failure(
        self,
        state: SessionState,
        *,
        stage: str,
        message: str,
        logs: dict[str, str] | None = None,
        validation: dict[str, Any] | None = None,
        prefix: str = "failure",
    ) -> None:
        record = classify_failure(stage=stage, message=message, logs=logs, validation=validation)
        report = write_failure_report(state.session_dir, record, prefix=prefix)
        state.artifacts["last_failure_report"] = str(report)
        state.artifacts["last_failure_class"] = record.failure_class
        state.artifacts["last_rollback_stage"] = record.rollback_stage
        state.notes.append(f"failure_class={record.failure_class}, rollback={record.rollback_stage}")
        self._save_state(state)

    # ━━━━━━━━━━━━━━━━ 会话创建 ━━━━━━━━━━━━━━━━━━━━━━━

    def create_session(self, requirement: str) -> dict[str, Any]:
        """创建/重置默认会话，并解析用户需求。

        tracer 事件序列遵循 receive → compose → forward 三段式：
        1. receive_user_prompt — 记录原始用户输入（审计用途）
        2. compose_prompt      — 标注当前要做什么操作
        3. forward_prompt      — 标注下游由谁执行（RequirementParser）
        这套模式在所有 generate/run 方法中保持一致。
        """
        # ① 记录原始用户输入
        self.tracer.event(
            "MainAgent",
            "receive_user_prompt",
            {"user_prompt": preview_text(requirement)},
            session_id=self.DEFAULT_SESSION,
        )
        # ② 标注操作意图
        self.tracer.event(
            "MainAgent",
            "compose_prompt",
            {
                "operation": "create_session",
                "prompt": "将用户自然语言需求解析为结构化规格，并初始化默认会话。",
            },
            session_id=self.DEFAULT_SESSION,
        )
        # ③ 转发给需求解析器
        self.tracer.event(
            "MainAgent",
            "forward_prompt",
            {
                "to_agent": "RequirementParser",
                "prompt": "提取器件类型、仿真类型、参数与目标。",
            },
            session_id=self.DEFAULT_SESSION,
        )

        spec_llm = self._llm_parse_requirement(requirement)
        spec = spec_llm or SessionSpec(
            requirement=requirement.strip(),
            device_type="unspecified",
            simulation_type="unspecified",
            target_artifact="unspecified",
            parameters={},
            targets=Targets(),
            task_spec=TaskSpec(),
        )
        spec.target_artifact = infer_target_artifact(
            requirement=spec.requirement,
            simulation_type=spec.simulation_type,
            current_target=spec.target_artifact,
        )
        self._clear_runtime_outputs()
        state = SessionState(
            session_id=self.DEFAULT_SESSION,
            session_dir=self.runtime_root,
            spec=spec,
            stage="created",
            artifacts={
                "project_root": str(self.runtime_root),
                "debug_trace": str(self.runtime_root / "logs" / "debug_trace.jsonl"),
            },
            metrics={},
            notes=[
                "Default single session initialized.",
                "Requirement parsing source: llm" if spec_llm else "Requirement parsing source: default_spec_fallback",
            ],
        )
        self._save_state(state)
        return self._dump(state)

    @classmethod
    def _uploaded_asset_key(cls, file_name: str) -> str:
        return f"{cls.UPLOADED_ASSET_PREFIX}{Path(file_name).name}"

    @staticmethod
    def _uploaded_asset_meta_key(asset_key: str, suffix: str) -> str:
        return f"{asset_key}::{suffix}"

    @staticmethod
    def _uploaded_asset_alias_owner_key(alias: str) -> str:
        return f"{alias}::source_asset"

    @staticmethod
    def _looks_like_sdevice(file_name: str) -> bool:
        lower = file_name.lower()
        return any(
            marker in lower
            for marker in (
                "sdevice",
                "_des.cmd",
                "_des.scm",
                "des.cmd",
                "des.scm",
            )
        ) or lower.endswith(".des")

    @staticmethod
    def _resolve_asset_role(file_name: str, requested_role: str) -> str:
        if requested_role and requested_role != "auto":
            if requested_role == "sde_cmd" and TCADAgent._looks_like_sdevice(file_name):
                return "sdevice_cmd"
            return requested_role
        lower = file_name.lower()
        if lower.endswith(".plt"):
            return "plot"
        if lower.endswith(".tdr"):
            return "mesh" if ("msh" in lower or "mesh" in lower) else "tdr"
        if TCADAgent._looks_like_sdevice(file_name):
            return "sdevice_cmd"
        if lower.endswith(".cmd") or lower.endswith(".scm"):
            return "sdevice_cmd" if TCADAgent._looks_like_sdevice(file_name) else "sde_cmd"
        return "input"

    @staticmethod
    def _asset_role_aliases(asset_role: str) -> list[str]:
        alias_map = {
            "sde_cmd": ["sde_cmd"],
            "sdevice_cmd": ["sdevice_cmd"],
            "mesh": ["mesh", "tdr"],
            "tdr": ["tdr"],
            "plot": ["plot"],
        }
        return alias_map.get(asset_role, [])

    def _uploaded_asset_entries(self, state: SessionState) -> list[tuple[str, str, str, float]]:
        entries: list[tuple[str, str, str, float]] = []
        for key, stored_path in state.artifacts.items():
            if not key.startswith(self.UPLOADED_ASSET_PREFIX):
                continue
            if key.endswith("::role") or key.endswith("::registered_at"):
                continue
            role = str(state.artifacts.get(self._uploaded_asset_meta_key(key, "role"), "input"))
            registered_at = float(state.artifacts.get(self._uploaded_asset_meta_key(key, "registered_at"), 0.0) or 0.0)
            entries.append((key, str(stored_path), role, registered_at))
        return entries

    def _latest_uploaded_asset_for_role(
        self,
        state: SessionState,
        asset_role: str,
        *,
        exclude_asset_key: str = "",
    ) -> tuple[str, Path] | None:
        candidates = [
            (asset_key, Path(stored_path), registered_at)
            for asset_key, stored_path, role, registered_at in self._uploaded_asset_entries(state)
            if role == asset_role and asset_key != exclude_asset_key
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[2], reverse=True)
        asset_key, stored_path, _ = candidates[0]
        return asset_key, stored_path

    def _runtime_alias_path(self, state: SessionState, alias: str) -> Path | None:
        run_dir = state.session_dir / "run"
        if alias == "sde_cmd":
            return run_dir / "sde_dvs.cmd"
        if alias == "sdevice_cmd":
            return run_dir / "sdevice_des.cmd"
        return None

    def _activate_asset_aliases(self, state: SessionState, *, asset_key: str, asset_role: str, stored_path: Path) -> None:
        for alias in self._asset_role_aliases(asset_role):
            alias_path = self._runtime_alias_path(state, alias)
            if alias_path is not None:
                alias_path.parent.mkdir(parents=True, exist_ok=True)
                if stored_path != alias_path:
                    shutil.copy2(stored_path, alias_path)
                state.artifacts[alias] = str(alias_path)
            else:
                state.artifacts[alias] = str(stored_path)
            state.artifacts[self._uploaded_asset_alias_owner_key(alias)] = asset_key

    def register_session_asset(self, source_path: str, file_name: str = "", role: str = "auto") -> dict[str, Any]:
        state = self._load_state()
        source = Path(source_path).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(f"Asset source not found: {source}")

        resolved_name = Path(file_name or source.name).name
        asset_role = self._resolve_asset_role(resolved_name, role)
        dest = self._ensure_inputs_dir() / resolved_name

        if source != dest:
            shutil.copy2(source, dest)

        asset_key = self._uploaded_asset_key(resolved_name)
        state.artifacts[asset_key] = str(dest)
        state.artifacts[self._uploaded_asset_meta_key(asset_key, "role")] = asset_role
        state.artifacts[self._uploaded_asset_meta_key(asset_key, "registered_at")] = f"{time.time():.6f}"
        self._activate_asset_aliases(state, asset_key=asset_key, asset_role=asset_role, stored_path=dest)

        state.notes.append(f"Uploaded asset registered: {resolved_name} ({asset_role})")
        self._save_state(state)
        return self._dump(
            state,
            {
                "asset": {
                    "file_name": resolved_name,
                    "role": asset_role,
                    "stored_path": str(dest),
                }
            },
        )

    def list_session_assets(self) -> dict[str, Any]:
        state = self._load_state()
        assets: list[dict[str, Any]] = []
        for key, stored_path, role, _registered_at in self._uploaded_asset_entries(state):
            file_name = key[len(self.UPLOADED_ASSET_PREFIX) :]
            active_keys = [
                alias
                for alias in self._asset_role_aliases(role)
                if state.artifacts.get(self._uploaded_asset_alias_owner_key(alias)) == key
            ]
            assets.append(
                {
                    "file_name": file_name,
                    "role": role,
                    "stored_path": stored_path,
                    "active_keys": sorted(active_keys),
                }
            )
        return {
            "session_id": state.session_id,
            "stage": state.stage,
            "assets": assets,
        }

    def delete_session_asset(self, file_name: str) -> dict[str, Any]:
        state = self._load_state()
        resolved_name = Path(file_name).name
        asset_key = self._uploaded_asset_key(resolved_name)
        stored_path = state.artifacts.pop(asset_key, "")
        asset_role = state.artifacts.pop(self._uploaded_asset_meta_key(asset_key, "role"), "")
        state.artifacts.pop(self._uploaded_asset_meta_key(asset_key, "registered_at"), None)

        deleted = False
        if stored_path:
            for alias in self._asset_role_aliases(asset_role):
                owner_key = self._uploaded_asset_alias_owner_key(alias)
                if state.artifacts.get(owner_key) == asset_key or state.artifacts.get(alias) == stored_path:
                    replacement = self._latest_uploaded_asset_for_role(state, asset_role, exclude_asset_key=asset_key)
                    if replacement is None:
                        state.artifacts.pop(alias, None)
                        state.artifacts.pop(owner_key, None)
                        alias_path = self._runtime_alias_path(state, alias)
                        if alias_path is not None:
                            alias_path.unlink(missing_ok=True)
                    else:
                        replacement_key, replacement_path = replacement
                        self._activate_asset_aliases(
                            state,
                            asset_key=replacement_key,
                            asset_role=asset_role,
                            stored_path=replacement_path,
                        )
            asset_path = Path(stored_path)
            try:
                resolved_path = asset_path.resolve()
            except FileNotFoundError:
                resolved_path = asset_path
            if resolved_path.exists() and (resolved_path == self.runtime_root or self.runtime_root in resolved_path.parents):
                resolved_path.unlink(missing_ok=True)
            deleted = True

        state.notes.append(f"Uploaded asset removed: {resolved_name}")
        self._save_state(state)
        return self._dump(
            state,
            {
                "deleted": deleted,
                "file_name": resolved_name,
                "role": asset_role,
            },
        )

    @staticmethod
    def _emit_runtime_event(event_sink: Callable[[dict[str, Any]], None] | None, payload: dict[str, Any]) -> None:
        if event_sink is None:
            return
        event_sink(to_jsonable(payload))

    def _emit_plan_created(self, state: SessionState, event_sink: Callable[[dict[str, Any]], None] | None) -> None:
        self._emit_runtime_event(
            event_sink,
            {
                "kind": "plan_created",
                "plan_id": state.plan_id,
                "plan_attempt": state.plan_attempt,
                "summary": state.task_summary,
                "done_criteria": state.done_criteria,
                "current_step": state.current_step,
                "plan_steps": [
                    {
                        "step_id": step.step_id,
                        "title": step.title,
                        "tool_name": step.tool_name,
                        "status": step.status,
                        "attempt": step.attempt,
                    }
                    for step in state.plan_steps
                ],
            },
        )

    def _emit_plan_step_update(
        self,
        state: SessionState,
        step: PlanStep,
        event_sink: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        self._emit_runtime_event(
            event_sink,
            {
                "kind": "plan_step_update",
                "plan_id": state.plan_id,
                "plan_attempt": state.plan_attempt,
                "step_id": step.step_id,
                "title": step.title,
                "tool_name": step.tool_name,
                "status": step.status,
                "notes": step.notes[-1:] if step.notes else [],
            },
        )

    def _emit_plan_replanned(
        self,
        state: SessionState,
        event_sink: Callable[[dict[str, Any]], None] | None,
        *,
        failed_step_id: str,
    ) -> None:
        self._emit_runtime_event(
            event_sink,
            {
                "kind": "plan_replanned",
                "plan_id": state.plan_id,
                "plan_attempt": state.plan_attempt,
                "failed_step_id": failed_step_id,
                "current_step": state.current_step,
                "plan_steps": [
                    {
                        "step_id": step.step_id,
                        "title": step.title,
                        "tool_name": step.tool_name,
                        "status": step.status,
                        "attempt": step.attempt,
                    }
                    for step in state.plan_steps
                ],
            },
        )

    def _emit_plan_completed(self, state: SessionState, event_sink: Callable[[dict[str, Any]], None] | None) -> None:
        self._emit_runtime_event(
            event_sink,
            {
                "kind": "plan_completed",
                "plan_id": state.plan_id,
                "plan_attempt": state.plan_attempt,
                "stage": state.stage,
                "current_step": state.current_step,
            },
        )

    # ━━━━━━━━━━━━━━━━ 步骤结果回填 ━━━━━━━━━━━━━━━━━━━

    def _apply_step(self, state: SessionState, result: StepResult) -> SessionState:
        """将单步执行结果回填到状态。

        统一处理 stage 跃迁、日志归档、产物路径记录，
        确保验证器和调试工具能通过 artifacts 字典找到所有文件。
        """
        state.stage = result.stage
        state.notes.append(result.message)
        state.artifacts.update(result.artifacts)
        if result.logs:
            # 日志键加 "log_" 前缀存入 artifacts，便于验证器与调试工具统一检索
            for k, v in result.logs.items():
                state.artifacts[f"log_{k}"] = v
            if "sdevice" in result.logs:
                # sdevice 日志额外存两个别名：验证器依赖 "sdevice_log"，
                # 错误恢复逻辑依赖 "last_log_hint"
                state.artifacts["sdevice_log"] = result.logs["sdevice"]
                state.artifacts["last_log_hint"] = result.logs["sdevice"]
        self._save_state(state)
        if not result.success or result.stage.endswith("failed"):
            self._record_failure(
                state,
                stage=result.stage,
                message=result.message,
                logs=result.logs,
                prefix="step_failure",
            )
        return state

    def _record_aux_step(self, state: SessionState, result: StepResult) -> SessionState:
        """记录辅助工具结果，但不改变主流程 stage。

        用于参数导出、格式转换、日志分析等旁路工具，
        避免把主流程 stage 覆盖成工具专用状态。
        """
        state.notes.append(result.message)
        state.artifacts.update(result.artifacts)
        if result.logs:
            for k, v in result.logs.items():
                state.artifacts[f"log_{k}"] = v
        self._save_state(state)
        if not result.success or result.stage.endswith("failed"):
            self._record_failure(
                state,
                stage=result.stage,
                message=result.message,
                logs=result.logs,
                prefix="aux_failure",
            )
        return state

    # ━━━━━━━━━━━━━━━━ SDE 阶段操作 ━━━━━━━━━━━━━━━━━━━

    def generate_sde(self) -> dict[str, Any]:
        """调用 LLM 生成 SDE deck（器件几何与网格定义脚本）。"""
        state = self._load_state()
        self.tracer.event("MainAgent", "compose_prompt", {"operation": "generate_sde"}, session_id=state.session_id)
        self.tracer.event(
            "MainAgent",
            "forward_prompt",
            {"to_agent": "SDECodegenAgent", "prompt": "根据需求生成SDE deck并做真实检查循环。"},
            session_id=state.session_id,
        )

        run_dir = state.session_dir / "run"
        logs_dir = state.session_dir / "logs"
        reports_dir = state.session_dir / "reports"
        run_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(parents=True, exist_ok=True)

        reference_bundle = self.reference_service.prepare_reference_bundle(state=state, phase="generate_sde")
        reference_artifacts = self.reference_service.persist_reference_bundle(
            session_dir=state.session_dir,
            bundle=reference_bundle,
        )
        state.artifacts.update(reference_artifacts)
        state.notes.append(reference_bundle.summary_note)
        self.tracer.event(
            "MainAgent",
            "reference_selected",
            {
                "phase": "sde",
                "selected_sde_references": len(reference_bundle.selected_sde_references),
                "selected_sdevice_references": len(reference_bundle.selected_sdevice_references),
                "selected_function_references": len(reference_bundle.selected_function_references),
            },
            session_id=state.session_id,
        )
        self._save_state(state)

        codegen_requirement = self._llm_compose_codegen_brief(state, phase="generate_sde") or self._build_codegen_requirement(
            state, phase="generate_sde"
        )
        sde_brief_file = logs_dir / "main_agent_sde_brief.txt"
        sde_brief_file.write_text(codegen_requirement, encoding="utf-8")
        out = self.llm.generate_sde(
            session_id=state.session_id,
            requirement=codegen_requirement,
            reference_context=reference_bundle.reference_context,
            device_type=state.spec.device_type,
            simulation_type=state.spec.simulation_type,
            parameters=state.spec.parameters,
            run_dir=run_dir,
            logs_dir=logs_dir,
        )
        if not out.get("ok", False):
            error_msg = out.get("error", "unknown error")
            failure_stage = str(out.get("failure_stage") or "sde_generation_failed")
            failure_summary = str(out.get("failure_summary") or error_msg).strip() or "SDE 生成失败。"
            debug_artifacts = dict(out.get("debug_artifacts") or {})
            state.stage = failure_stage
            state.artifacts.update(debug_artifacts)
            if debug_artifacts.get("sde_run_log"):
                state.artifacts["last_log_hint"] = str(debug_artifacts["sde_run_log"])
            elif debug_artifacts.get("sde_check_log"):
                state.artifacts["last_log_hint"] = str(debug_artifacts["sde_check_log"])
            state.notes.append(failure_summary)
            self._save_state(state)
            self._record_failure(
                state,
                stage=failure_stage,
                message=error_msg,
                logs={
                    "sde_syntax": str(debug_artifacts.get("sde_check_log") or ""),
                    "sde": str(debug_artifacts.get("sde_run_log") or ""),
                },
                prefix="sde_failure",
            )
            import sys
            print(f"\n[✗] {failure_summary}", file=sys.stderr, flush=True)
            print(f"    错误摘要:\n{error_msg[:800]}", file=sys.stderr, flush=True)
            return self._dump(state, {"generation": out})

        state.stage = "sde_generated"
        state.artifacts["sde_cmd"] = str(run_dir / "sde_dvs.cmd")
        state.artifacts["main_agent_sde_brief"] = str(sde_brief_file)
        state.artifacts.update(out.get("debug_artifacts", {}))
        state.notes.append("SDE deck generated by LLM.")
        self._save_state(state)
        code = (run_dir / "sde_dvs.cmd").read_text(encoding="utf-8", errors="ignore")
        audit = self._write_coverage_audit(
            state,
            phase="sde",
            code_text=code,
            output_name="sde_coverage_audit.json",
        )
        return self._dump(state, {"generation": out, "coverage_audit": to_jsonable(audit) if audit else None})

    def check_sde(self) -> dict[str, Any]:
        """执行 `sde -S` 语法检查，快速发现拼写与结构错误。"""
        state = self._load_state()
        self.tracer.event("MainAgent", "forward_prompt", {"to_agent": "SimulationAgent", "prompt": "执行 sde -S 语法检查。"}, session_id=state.session_id)
        res = self.ops.check_sde(state)
        state = self._apply_step(state, res)
        return self._dump(state, {"run_result": to_jsonable(res)})

    def run_sde(self) -> dict[str, Any]:
        """执行 `sde -e -l` 生成 mesh 与边界文件。"""
        state = self._load_state()
        self.tracer.event("MainAgent", "forward_prompt", {"to_agent": "SimulationAgent", "prompt": "执行 sde -e -l，生成 mesh。"}, session_id=state.session_id)
        res = self.ops.run_sde(state)
        state = self._apply_step(state, res)
        return self._dump(state, {"run_result": to_jsonable(res)})

    # ━━━━━━━━━━━━━━━━ TDR 检查 ━━━━━━━━━━━━━━━━━━━━━━━

    def inspect_tdr(self, tdr_filename: str = "sde_result_msh.tdr") -> dict[str, Any]:
        """执行 `tdx -info` 提取结构摘要（维度、顶点数、材料区域等）。"""
        state = self._load_state()
        self.tracer.event("MainAgent", "forward_prompt", {"to_agent": "SimulationAgent", "prompt": f"执行 tdx -info {tdr_filename}"}, session_id=state.session_id)
        res = self.ops.inspect_tdr(state, tdr_filename=tdr_filename)
        state = self._apply_step(state, res)
        return self._dump(state, {"run_result": to_jsonable(res), "tdr_summary": res.details.get("summary", {})})

    # ━━━━━━━━━━━━━━━━ SDevice 阶段操作 ━━━━━━━━━━━━━━━━

    def generate_sdevice(self) -> dict[str, Any]:
        """基于需求和 SDE deck 生成 SDevice deck（电学仿真控制脚本）。"""
        state = self._load_state()
        self.tracer.event("MainAgent", "compose_prompt", {"operation": "generate_sdevice"}, session_id=state.session_id)
        self.tracer.event(
            "MainAgent",
            "forward_prompt",
            {"to_agent": "SDeviceCodegenAgent", "prompt": "基于SDE deck与mesh约束生成SDevice cmd。"},
            session_id=state.session_id,
        )

        run_dir = state.session_dir / "run"
        logs_dir = state.session_dir / "logs"
        sde_code = (run_dir / "sde_dvs.cmd").read_text(encoding="utf-8", errors="ignore") if (run_dir / "sde_dvs.cmd").exists() else ""

        reference_bundle = self.reference_service.prepare_reference_bundle(state=state, phase="generate_sdevice")
        reference_artifacts = self.reference_service.persist_reference_bundle(
            session_dir=state.session_dir,
            bundle=reference_bundle,
        )
        state.artifacts.update(reference_artifacts)
        if reference_bundle.selected_sdevice_references:
            state.notes.append(f"已选中 {len(reference_bundle.selected_sdevice_references)} 个 full-flow / SDevice 参考")
            self._save_state(state)

        codegen_requirement = self._llm_compose_codegen_brief(state, phase="generate_sdevice") or self._build_codegen_requirement(
            state, phase="generate_sdevice"
        )
        sdevice_brief_file = logs_dir / "main_agent_sdevice_brief.txt"
        sdevice_brief_file.write_text(codegen_requirement, encoding="utf-8")
        out = self.llm.generate_sdevice(
            session_id=state.session_id,
            requirement=codegen_requirement,
            reference_context=reference_bundle.reference_context,
            device_type=state.spec.device_type,
            simulation_type=state.spec.simulation_type,
            parameters=state.spec.parameters,
            sde_code=sde_code,
            run_dir=run_dir,
            logs_dir=logs_dir,
        )
        if not out.get("ok", False):
            error_msg = out.get("error", "unknown error")
            state.stage = "sdevice_generation_failed"
            state.notes.append(f"SDevice generation failed: {error_msg[:200]}")
            self._save_state(state)
            self._record_failure(
                state,
                stage=state.stage,
                message=error_msg,
                logs={k: v for k, v in out.get("debug_artifacts", {}).items() if "log" in k},
                prefix="sdevice_failure",
            )
            import sys
            print(f"\n[✗] SDevice 生成失败，共尝试 {out.get('attempt', '?')} 次", file=sys.stderr, flush=True)
            print(f"    错误摘要:\n{error_msg[:800]}", file=sys.stderr, flush=True)
            return self._dump(state, {"generation": out})

        state.stage = "sdevice_generated"
        state.artifacts["sdevice_cmd"] = str(run_dir / "sdevice_des.cmd")
        state.artifacts["main_agent_sdevice_brief"] = str(sdevice_brief_file)
        state.artifacts.update(out.get("debug_artifacts", {}))
        state.notes.append("SDevice deck generated by LLM.")
        self._save_state(state)
        code = (run_dir / "sdevice_des.cmd").read_text(encoding="utf-8", errors="ignore")
        tdr_info = ""
        tdr_report = Path(state.artifacts.get("tdr_info_report", ""))
        if tdr_report.exists():
            tdr_info = tdr_report.read_text(encoding="utf-8", errors="ignore")
        audit = self._write_coverage_audit(
            state,
            phase="sdevice",
            code_text=code,
            output_name="sdevice_coverage_audit.json",
            tdr_info=tdr_info,
        )
        return self._dump(state, {"generation": out, "coverage_audit": to_jsonable(audit) if audit else None})

    def check_sdevice(self) -> dict[str, Any]:
        """执行 `sdevice -P` 预检查，在正式仿真前发现参数与语法问题。"""
        state = self._load_state()
        self.tracer.event("MainAgent", "forward_prompt", {"to_agent": "SimulationAgent", "prompt": "执行 sdevice -P 预检查。"}, session_id=state.session_id)
        res = self.ops.check_sdevice(state)
        state = self._apply_step(state, res)
        return self._dump(state, {"run_result": to_jsonable(res)})

    def run_sdevice(self) -> dict[str, Any]:
        """执行 `sdevice --exit-on-failure` 真实电学仿真。"""
        state = self._load_state()
        self.tracer.event("MainAgent", "forward_prompt", {"to_agent": "SimulationAgent", "prompt": "执行 sdevice --exit-on-failure 真实仿真。"}, session_id=state.session_id)
        res = self.ops.run_sdevice(state)
        state = self._apply_step(state, res)
        return self._dump(state, {"run_result": to_jsonable(res)})

    # ━━━━━━━━━━━━━━━━ SVisual 导出 ━━━━━━━━━━━━━━━━━━━

    def run_svisual(self, source_file: str = "", mode: str = "plt") -> dict[str, Any]:
        """导出 SDevice/PLT 曲线（PNG + 曲线文本）。"""
        state = self._load_state()
        if not source_file:
            source_file = state.artifacts.get("plot", "")
        if not source_file:
            res = StepResult(
                False,
                "svisual_failed",
                "source_file is required for run_svisual_export (.plt).",
            )
            state = self._apply_step(state, res)
            return self._dump(state, {"run_result": to_jsonable(res)})
        self.tracer.event(
            "MainAgent",
            "forward_prompt",
            {"to_agent": "SimulationAgent", "prompt": f"执行 svisual 曲线导出 source={source_file or '<missing>'}, mode={mode}"},
            session_id=state.session_id,
        )
        res = self.ops.run_svisual(state, source_file=source_file, mode=mode)
        state = self._apply_step(state, res)
        return self._dump(state, {"run_result": to_jsonable(res)})

    def run_svisual_sde(self, source_file: str = "", mode: str = "tdr") -> dict[str, Any]:
        """导出 SDE/TDR 结构图（PNG）。"""
        state = self._load_state()
        if not source_file:
            # 结构图优先使用 SDE mesh；若缺失再回退到通用 tdr。
            source_file = state.artifacts.get("mesh", "") or state.artifacts.get("tdr", "")
        if not source_file:
            res = StepResult(
                False,
                "svisual_sde_failed",
                "source_file is required for run_svisual_sde_export (.tdr/.msh.tdr).",
            )
            state = self._apply_step(state, res)
            return self._dump(state, {"run_result": to_jsonable(res)})
        self.tracer.event(
            "MainAgent",
            "forward_prompt",
            {"to_agent": "SimulationAgent", "prompt": f"执行 svisual 结构导出 source={source_file or '<missing>'}, mode={mode}"},
            session_id=state.session_id,
        )
        res = self.ops.run_svisual(state, source_file=source_file, mode=mode)
        if res.success:
            # 结构导出与曲线导出的键分离，避免后续 run_svisual 覆盖同名产物键。
            if res.artifacts.get("svisual_png"):
                res.artifacts["svisual_sde_png"] = res.artifacts["svisual_png"]
            if res.artifacts.get("svisual_source"):
                res.artifacts["svisual_sde_source"] = res.artifacts["svisual_source"]
            if res.artifacts.get("svisual_script"):
                res.artifacts["svisual_sde_script"] = res.artifacts["svisual_script"]
            if "svisual" in res.logs:
                res.logs["svisual_sde"] = res.logs["svisual"]
            res.stage = "svisual_sde_done"
            res.message = "SVisual SDE structure export done."
        else:
            res.stage = "svisual_sde_failed"
            res.message = f"SVisual SDE structure export failed: {res.message}"
        state = self._apply_step(state, res)
        return self._dump(state, {"run_result": to_jsonable(res)})

    # ━━━━━━━━━━━━━━━━ 扩展工具（不改变主流程 stage） ━━━━━━━━━━━━━━━━━━━

    def check_and_run_sde(self) -> dict[str, Any]:
        """执行 `sde -Sl`（语法检查 + 执行）。"""
        state = self._load_state()
        self.tracer.event("MainAgent", "forward_prompt", {"to_agent": "SimulationAgent", "prompt": "执行 sde -Sl 语法检查并运行。"}, session_id=state.session_id)
        res = self.ops.check_and_run_sde(state)
        state = self._record_aux_step(state, res)
        return self._dump(state, {"run_result": to_jsonable(res), "aux_stage": res.stage})

    def dump_sdevice_parameters(self, target: str = "Si", output_file: str = "") -> dict[str, Any]:
        """导出 SDevice 参数（sdevice -P）。"""
        state = self._load_state()
        res = self.ops.dump_sdevice_parameters(state, target=target, output_file=output_file)
        state = self._record_aux_step(state, res)
        return self._dump(state, {"run_result": to_jsonable(res), "aux_stage": res.stage})

    def dump_sdevice_library(self, target: str = "Si", output_file: str = "") -> dict[str, Any]:
        """导出 SDevice 参数库（sdevice -L）。"""
        state = self._load_state()
        res = self.ops.dump_sdevice_library(state, target=target, output_file=output_file)
        state = self._record_aux_step(state, res)
        return self._dump(state, {"run_result": to_jsonable(res), "aux_stage": res.stage})

    def list_sdevice_parameter_names(self) -> dict[str, Any]:
        """列出 SDevice 可扫描参数名（--parameter-names）。"""
        state = self._load_state()
        res = self.ops.list_sdevice_parameter_names(state)
        state = self._record_aux_step(state, res)
        return self._dump(state, {"run_result": to_jsonable(res), "aux_stage": res.stage})

    def list_sdevice_field_names(self) -> dict[str, Any]:
        """列出 SDevice 字段名（--field-names）。"""
        state = self._load_state()
        res = self.ops.list_sdevice_field_names(state)
        state = self._record_aux_step(state, res)
        return self._dump(state, {"run_result": to_jsonable(res), "aux_stage": res.stage})

    def list_sdevice_versions(self) -> dict[str, Any]:
        """列出 SDevice 已安装版本（-versions）。"""
        state = self._load_state()
        res = self.ops.list_sdevice_versions(state)
        state = self._record_aux_step(state, res)
        return self._dump(state, {"run_result": to_jsonable(res), "aux_stage": res.stage})

    def tdx_convert(
        self,
        command: str,
        source_file: str,
        dest_file: str = "",
        options: list[str] | None = None,
    ) -> dict[str, Any]:
        """运行 tdx 转换命令。"""
        state = self._load_state()
        res = self.ops.tdx_convert(state, command=command, source_file=source_file, dest_file=dest_file, options=options)
        state = self._record_aux_step(state, res)
        return self._dump(state, {"run_result": to_jsonable(res), "aux_stage": res.stage})

    def tdx_change_coordinate_system(
        self,
        source_file: str,
        dest_file: str = "",
        target: str = "sprocess",
    ) -> dict[str, Any]:
        """运行 tdx --tdr-change-cs 坐标系转换。"""
        state = self._load_state()
        res = self.ops.tdx_change_coordinate_system(state, source_file=source_file, dest_file=dest_file, target=target)
        state = self._record_aux_step(state, res)
        return self._dump(state, {"run_result": to_jsonable(res), "aux_stage": res.stage})

    def tdx_mirror_tdr(
        self,
        source_file: str,
        axis: str = "xmin",
        dest_file: str = "",
        rename_rule: str = "",
    ) -> dict[str, Any]:
        """运行 tdx --mirr-tdr 做镜像。"""
        state = self._load_state()
        res = self.ops.tdx_mirror_tdr(
            state,
            source_file=source_file,
            axis=axis,
            dest_file=dest_file,
            rename_rule=rename_rule,
        )
        state = self._record_aux_step(state, res)
        return self._dump(state, {"run_result": to_jsonable(res), "aux_stage": res.stage})

    def tdx_tclcmd(self, tcl_command: str) -> dict[str, Any]:
        """运行 tdx -tclcmd 单条 Tcl 命令。"""
        state = self._load_state()
        res = self.ops.tdx_run_tclcmd(state, tcl_command=tcl_command)
        state = self._record_aux_step(state, res)
        return self._dump(state, {"run_result": to_jsonable(res), "aux_stage": res.stage})

    def run_svisual_tcl_script(
        self,
        script_content: str = "",
        script_file: str = "",
        expected_outputs: list[str] | None = None,
    ) -> dict[str, Any]:
        """运行自定义 svisual Tcl 脚本。"""
        state = self._load_state()
        res = self.ops.run_svisual_tcl_script(
            state,
            script_content=script_content,
            script_file=script_file,
            expected_outputs=expected_outputs,
        )
        state = self._record_aux_step(state, res)
        return self._dump(state, {"run_result": to_jsonable(res), "aux_stage": res.stage})

    def run_svisual_cutline_export(
        self,
        source_file: str,
        axis: str = "x",
        at: float = 0.0,
        variables: list[str] | None = None,
    ) -> dict[str, Any]:
        """运行 svisual cutline 导出。"""
        state = self._load_state()
        res = self.ops.run_svisual_cutline_export(
            state,
            source_file=source_file,
            axis=axis,
            at=at,
            variables=variables,
        )
        state = self._record_aux_step(state, res)
        return self._dump(state, {"run_result": to_jsonable(res), "aux_stage": res.stage})

    def run_inspect_script(
        self,
        script_content: str = "",
        script_file: str = "",
        input_files: list[str] | None = None,
        expected_outputs: list[str] | None = None,
        batch: bool = True,
    ) -> dict[str, Any]:
        """运行 Inspect 脚本（批处理提参/曲线分析）。"""
        state = self._load_state()
        res = self.ops.run_inspect_script(
            state,
            script_content=script_content,
            script_file=script_file,
            input_files=input_files,
            expected_outputs=expected_outputs,
            batch=batch,
        )
        state = self._record_aux_step(state, res)
        return self._dump(state, {"run_result": to_jsonable(res), "aux_stage": res.stage})

    def run_boxmethod(
        self,
        grid_file: str,
        algorithm: str = "CVPL_AverageBoxMethod",
        num_threads: int = 1,
    ) -> dict[str, Any]:
        """运行 boxmethod 网格质量分析。"""
        state = self._load_state()
        res = self.ops.run_boxmethod(state, grid_file=grid_file, algorithm=algorithm, num_threads=num_threads)
        state = self._record_aux_step(state, res)
        return self._dump(state, {"run_result": to_jsonable(res), "aux_stage": res.stage})

    def run_logbrowser(
        self,
        xml_log_file: str,
        info_level: int = 1,
        batch: bool = True,
    ) -> dict[str, Any]:
        """运行 logbrowser 分析 XML 日志。"""
        state = self._load_state()
        res = self.ops.run_logbrowser(state, xml_log_file=xml_log_file, info_level=info_level, batch=batch)
        state = self._record_aux_step(state, res)
        return self._dump(state, {"run_result": to_jsonable(res), "aux_stage": res.stage})

    # ━━━━━━━━━━━━━━━━ 物理验证 ━━━━━━━━━━━━━━━━━━━━━━━

    def validate(self) -> dict[str, Any]:
        """执行结构/曲线/指标联合验证。

        验证三个维度：
        1. 结构完整性 — mesh 文件是否存在且非空
        2. 曲线合理性 — IV 曲线是否单调、无异常跳变
        3. 指标达标   — Ion/Ioff/SS 是否满足用户目标
        """
        state = self._load_state()
        self.tracer.event("MainAgent", "forward_prompt", {"to_agent": "ValidationAgent", "prompt": "执行物理级验证：结构+曲线+指标。"}, session_id=state.session_id)
        out = self.validator.validate(state)
        state.metrics.update(out.metrics)
        state.stage = "validated" if out.success else "validation_failed"
        state.notes.append(out.message)
        rep = state.session_dir / "reports" / "validation.json"
        rep.write_text(json.dumps(to_jsonable(out), ensure_ascii=False, indent=2), encoding="utf-8")
        state.artifacts["validation_report"] = str(rep)
        self._save_state(state)
        if not out.success:
            self._record_failure(
                state,
                stage=state.stage,
                message=out.message,
                logs={"validation_report": str(rep), "sdevice": state.artifacts.get("sdevice_log", "")},
                validation=to_jsonable(out),
                prefix="validation_failure",
            )

        # 打印验证摘要到 stderr，无论成功失败都显示关键指标和失败项
        import sys
        if out.success:
            metrics_str = ", ".join(f"{k}={v:.3g}" for k, v in out.metrics.items())
            print(f"[✓] 验证通过 | {metrics_str}", file=sys.stderr, flush=True)
        else:
            failed_checks = [k for k, v in out.checks.items() if not v]
            print(f"[✗] 验证失败 | 未通过项: {failed_checks}", file=sys.stderr, flush=True)
            metrics_str = ", ".join(f"{k}={v:.3g}" for k, v in out.metrics.items())
            if metrics_str:
                print(f"    指标: {metrics_str}", file=sys.stderr, flush=True)
            print(f"    报告: {rep}", file=sys.stderr, flush=True)

        return self._dump(state, {"validation": to_jsonable(out)})

    # ━━━━━━━━━━━━━━━━ 状态查询 ━━━━━━━━━━━━━━━━━━━━━━━

    def show_state(self) -> dict[str, Any]:
        """返回当前状态快照。"""
        return self._dump(self._load_state())

    def run_bash(self, command: str, cwd: str = "", timeout_s: int = 30) -> dict[str, Any]:
        """执行 Bash 命令并记录输出。

        说明：
        - 这是通用诊断/文件查看工具，供主 Agent 在需要时执行 `ls/cat/head/...`。
        - 若 cwd 为空，默认在 `runtime/default` 下执行。
        """
        state = self._load_state()
        workdir = Path(cwd).expanduser() if cwd else state.session_dir
        if not workdir.is_absolute():
            workdir = (self.workspace / workdir).resolve()
        if not workdir.exists() or not workdir.is_dir():
            return self._dump(
                state,
                {
                    "run_result": {
                        "success": False,
                        "stage": state.stage,
                        "message": f"Invalid cwd: {workdir}",
                    }
                },
            )

        logs_dir = state.session_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = int(time.time())
        log_file = logs_dir / f"bash_{stamp}.log"
        try:
            proc = subprocess.run(
                ["bash", "-lc", command],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
            rc = proc.returncode
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            rc = 124
            stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            stderr += f"\n[timeout] command exceeded {timeout_s}s"

        merged = stdout + (("\n" + stderr) if stderr else "")
        log_file.write_text(merged, encoding="utf-8", errors="ignore")
        state.artifacts["last_bash_log"] = str(log_file)
        state.notes.append(f"run_bash executed: rc={rc}")
        self._save_state(state)

        return self._dump(
            state,
            {
                "run_result": {
                    "success": rc == 0,
                    "stage": state.stage,
                    "message": "bash command done" if rc == 0 else f"bash command failed: rc={rc}",
                    "details": {"return_code": rc, "cwd": str(workdir)},
                    "logs": {"bash": str(log_file)},
                },
                "bash": {
                    "command": command,
                    "cwd": str(workdir),
                    "return_code": rc,
                    "stdout": preview_text(stdout, 8000),
                    "stderr": preview_text(stderr, 4000),
                    "log_file": str(log_file),
                },
            },
        )

    def show_capabilities(self) -> dict[str, Any]:
        """返回系统可用能力：MCP 工具与 Skills。"""
        mcp_tools = self.mcp_tools.list_tool_names()
        skills = self.llm.skills.list_names()
        benchmark = load_education_benchmark(self.workspace)
        ordered_mcp = [n for n in self.MCP_TOOL_ORDER if n in mcp_tools]
        ordered_mcp.extend(sorted(n for n in mcp_tools if n not in ordered_mcp))
        ordered_skills = [n for n in self.SKILL_ORDER if n in skills]
        ordered_skills.extend(sorted(n for n in skills if n not in ordered_skills))
        return {
            f"mcp_tools[{len(ordered_mcp)}]": [
                f"{name}：{self.MCP_TOOL_NOTES.get(name, '')}".rstrip("：") for name in ordered_mcp
            ],
            f"skills[{len(ordered_skills)}]": [
                f"{name}：{self.SKILL_NOTES.get(name, '')}".rstrip("：") for name in ordered_skills
            ],
            "llm": {
                "model_main": self.llm.model_main,
                "model_sde": self.llm.model_sde,
                "model_sdevice": self.llm.model_sdevice,
                "base_url": self.llm.cfg.base_url,
                "temperature": self.llm.cfg.temperature,
            },
            "education_benchmark": {
                "lessons_task_count": benchmark.get("lessons", {}).get("task_count", 0),
                "tutorial_task_count": benchmark.get("tutorial", {}).get("task_count", 0),
                "manual_full_suite_v2_task_count": benchmark.get("manual_full_suite_v2", {}).get("task_count", 0),
                "archive_root": benchmark.get("archive_root", ""),
            },
        }

    # ━━━━━━━━━━━━━━━━ 启发式计划器 ━━━━━━━━━━━━━━━━━━━━

    def plan_operations(self, instruction: str) -> dict[str, Any]:
        """根据自然语言指令生成操作计划。

        使用主模型直接规划，不走关键词/阶段硬编码兜底。
        """
        if not self.state_file.exists():
            self.create_session(instruction)
        state = self._load_state()
        llm_ops, llm_reason = self._llm_plan(state, instruction)
        ops = llm_ops
        reason = llm_reason or ("llm" if llm_ops else "llm_empty")
        self.tracer.event(
            "MainAgent",
            "tool_plan",
            {
                "instruction": preview_text(instruction),
                "planned_operations": ops,
                "reason": reason,
                "stage": state.stage,
            },
            session_id=state.session_id,
        )
        return {"session_id": state.session_id, "stage": state.stage, "planned_operations": ops, "reason": reason}

    @staticmethod
    def _extract_json_block(text: str) -> dict[str, Any] | None:
        """从 LLM 文本中提取首个 JSON 对象。"""
        raw = text.strip()
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            payload = json.loads(raw[start : end + 1])
            if isinstance(payload, dict):
                return payload
        except Exception:
            return None
        return None

    def _normalize_ops(self, state: SessionState, ops: list[Any]) -> list[dict[str, Any]]:
        """过滤并去重，仅保留真实可用 MCP 工具名与可选参数。"""
        tools = set(self.mcp_tools.list_tool_names())
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for op in ops:
            name = ""
            args: dict[str, Any] = {}
            if isinstance(op, dict):
                candidate = op.get("tool")
                if candidate is None:
                    candidate = op.get("operation")
                if candidate is None:
                    candidate = op.get("next_tool")
                name = str(candidate or "").strip()
                args_raw = op.get("args", {})
                if isinstance(args_raw, dict):
                    args = args_raw
            else:
                name = str(op).strip()
            if name in tools and name not in seen:
                normalized.append({"tool": name, "args": args})
                seen.add(name)
        return normalized

    def _llm_plan(
        self,
        state: SessionState,
        instruction: str,
        executed_history: list[str] | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        """用 LLM 生成工具调用计划，输出 MCP 工具名序列。"""
        tool_names = self.mcp_tools.list_tool_names()
        tool_guide = "\n".join(f"- {n}: {self.MCP_TOOL_NOTES.get(n, '')}" for n in tool_names)
        system_prompt = (
            self.llm.skills.load("main_agent", "")
            + "\n\n"
            + self.llm.skills.load("planner", "")
            + "\n\n"
            + (
                "你是 TCAD 主 Agent 规划器。"
                "请根据用户指令与当前 stage 规划下一批操作。"
                "你只能输出一个 JSON 对象，不要输出其它文字。"
                "JSON 格式: {\"operations\": [\"tool1\", {\"tool\":\"tool2\",\"args\":{}}], \"reason\": \"...\"}\n"
                "operations 只能使用以下 MCP 工具名。\n"
                "工具说明：\n"
                + tool_guide
            )
        )
        user_prompt = (
            f"用户指令:\n{instruction}\n\n"
            f"当前stage: {state.stage}\n"
            f"当前已知产物键: {sorted(state.artifacts.keys())}\n"
            f"最近备注(末3条): {state.notes[-3:]}\n\n"
            f"本轮已执行工具: {executed_history or []}\n\n"
            "要求:\n"
            "1) 基于当前状态与用户目标，自主规划最小必要步骤。\n"
            "2) 不要机械走固定全流程；必要时可返回空操作。\n"
            "3) 若已有产物可复用（如已有 mesh/tdr/sdevice/plot），优先复用，不要重复生成上游步骤。\n"
            "4) 仅在用户明确要求重建结构时，才重新生成 SDE。\n"
            "5) 如果当前目标是从已有结构继续仿真，优先规划 inspect_tdr/generate_sdevice_code/check_sdevice_syntax/run_sdevice/run_svisual_export/validate_results 链路。\n"
            "6) show_state 仅用于用户显式要求查看状态，或一次性诊断；不要把 show_state 作为主流程重复步骤。\n"
            "7) 结构图 PNG 场景只用 run_svisual_sde_export；电学曲线 PNG 场景只用 run_svisual_export。\n"
            "8) 若用户是在询问某张本地图片/结构图内容，优先由主模型直接多模态回答，不要额外规划图片问答工具。\n"
            "9) 若用户要求列目录/读文件，可用 run_bash，并在 args.command 给出命令。\n"
            "10) 返回 JSON 严格合法。"
        )
        self.tracer.event(
            "MainAgent",
            "compose_prompt",
            {"operation": "plan_operations", "instruction": preview_text(instruction), "stage": state.stage},
            session_id=state.session_id,
        )
        try:
            raw = self.llm.chat_main(
                [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                verbose=False,
            )
        except Exception as exc:
            self.tracer.event(
                "MainAgent",
                "plan_llm_error",
                {"error": str(exc)},
                session_id=state.session_id,
            )
            return [], ""

        payload = self._extract_json_block(raw)
        if not payload:
            self.tracer.event(
                "MainAgent",
                "plan_llm_invalid_json",
                {"raw_preview": preview_text(raw, 1200)},
                session_id=state.session_id,
            )
            return [], ""
        ops_raw = payload.get("operations", [])
        if not isinstance(ops_raw, list):
            return [], ""
        normalized = self._normalize_ops(state, ops_raw)
        return normalized, str(payload.get("reason", "llm"))

    def _pick_next_pending(self, state: SessionState, ops: list[dict[str, Any]]) -> dict[str, Any] | None:
        """从候选计划中取下一步（包含 tool + args）。"""
        return ops[0] if ops else None

    def _language_only_reply(self, instruction: str, state: SessionState | None = None) -> str:
        """纯语言回答模式（不调用工具）。"""
        stage = state.stage if state else "no_session"
        system_prompt = (
            self.llm.skills.load("main_agent", "")
            + "\n\n你是 TCAD 顾问助手。当前轮次不调用工具，只做简洁、可执行的中文回答。"
        )
        user_prompt = (
            f"用户输入:\\n{instruction}\\n\\n"
            f"当前 stage: {stage}\\n"
            "请直接回答用户问题；如果信息不足，明确指出缺失项。"
            "回答风格要求：先结论，再给最多 3 条下一步建议；不要输出 JSON。"
        )
        try:
            txt = self.llm.chat_main(
                [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                verbose=False,
            ).strip()
            return preview_text(txt, 1200)
        except Exception:
            return "当前不需要调用工具。我可以先用语言分析需求；若你要执行仿真，请给出器件结构、材料、掺杂和目标曲线。"

    def _execution_reply(self, instruction: str, state: SessionState, executed: list[str], last: dict[str, Any]) -> str:
        """根据本轮执行结果生成面向用户的简洁回复。"""
        if not executed:
            return (
                f"本轮未执行任何工具。当前阶段为 `{state.stage}`。\n"
                "如果你希望直接开始新任务，请先输入 `/clean`，然后重新描述需求；"
                "如果希望续跑当前任务，请在指令中明确“继续”。"
            )
        keys = [
            "sde_cmd",
            "mesh",
            "tdr_info_report",
            "sdevice_cmd",
            "plot",
            "svisual_png",
            "svisual_curve_txt",
            "last_bash_log",
            "validation_report",
        ]
        artifacts = {k: state.artifacts.get(k) for k in keys if state.artifacts.get(k)}
        metrics = state.metrics or {}
        notes = state.notes[-4:]
        # 固定模板汇报：避免执行后又出现空泛/偏题回复。
        stage = state.stage
        if stage.endswith("failed"):
            last_note = notes[-1] if notes else stage
            lines = [f"本轮执行未完全成功：{last_note}"]
            if artifacts:
                lines.append("已生成的关键文件：")
                for k, v in list(artifacts.items())[:5]:
                    lines.append(f"- {k}: {v}")
            lines.append("建议下一步：先查看失败日志并针对失败步骤单独重跑。")
            return "\n".join(lines)
        lines = ["本轮执行已完成。"]
        if artifacts:
            lines.append("关键产物：")
            for k, v in list(artifacts.items())[:5]:
                lines.append(f"- {k}: {v}")
        if metrics:
            metric_line = ", ".join(f"{k}={v:.3g}" for k, v in metrics.items() if isinstance(v, (int, float)))
            if metric_line:
                lines.append(f"指标：{metric_line}")
        return "\n".join(lines)

    @staticmethod
    def _is_structure_only_task(state: SessionState) -> bool:
        inferred = infer_target_artifact(
            requirement=state.spec.requirement,
            simulation_type=state.spec.simulation_type,
            current_target=state.spec.target_artifact,
        )
        return inferred in {"structure", "structure_png", "tdr_info"}

    def decide_next_operation(self, instruction: str, executed_history: list[str] | None = None) -> dict[str, Any]:
        """逐步决策模式：一次只决定下一步操作。

        这是主 Agent 的核心决策接口，用于替代“一次性整批计划”：
        - 每轮读取最新状态
        - 决定一个 next_operation
        - 执行后再进入下一轮决策
        """
        state: SessionState | None = None
        if self.state_file.exists():
            state = self._load_state()
        else:
            # 无会话时总是先创建默认会话，避免“路由先判 chat”导致可执行任务被跳过。
            self.create_session(instruction)
            state = self._load_state()

        assert state is not None

        # 不做前置 chat 路由短路：先规划可执行工具，再决定是否纯语言回答。
        # 这样“解释现有图片/文件”的请求不会被误判为闲聊而跳过工具调用。

        direct_image_reply = self._direct_multimodal_image_reply(instruction, state)
        if direct_image_reply is not None:
            return direct_image_reply

        # 记录本轮执行意图，供后续 codegen 组装结构化 requirement。
        if instruction.strip():
            state.artifacts["last_user_instruction"] = instruction.strip()
            self._save_state(state)

        tool_names = self.mcp_tools.list_tool_names()
        tool_guide = "\n".join(f"- {n}: {self.MCP_TOOL_NOTES.get(n, '')}" for n in tool_names)
        system_prompt = (
            self.llm.skills.load("main_agent", "")
            + "\n\n"
            + self.llm.skills.load("planner", "")
            + "\n\n"
            + (
                "你是 TCAD 主 Agent 的逐步决策器。"
                "每次只返回下一步一个 MCP 工具，不要返回工具数组。"
                "你只能输出 JSON，不能输出其他文字。\n"
                "JSON 格式："
                "{\"done\": false, \"next_tool\": \"...\", \"next_args\": {}, \"reason\": \"...\", \"target_artifact\": \"unspecified\", \"assistant_pre\": \"...\", \"assistant_post_success\": \"...\", \"assistant_post_failure\": \"...\", \"assistant_reply\": \"\"}\n"
                "若不需要调用工具（例如仅问概念/解释），返回："
                "{\"done\": true, \"next_tool\": null, \"reason\": \"language_only\", \"target_artifact\": \"unspecified\", \"assistant_reply\": \"给用户的中文回答\"}\n"
                "next_tool 只能使用以下 MCP 工具名。\n"
                "工具说明：\n"
                + tool_guide
            )
        )
        user_prompt = (
            f"用户指令:\\n{instruction}\\n\\n"
            f"当前 stage: {state.stage}\\n"
            f"当前 target_artifact: {state.spec.target_artifact}\\n"
            f"当前产物键: {sorted(state.artifacts.keys())}\\n"
            f"最近备注(末3条): {state.notes[-3:]}\\n\\n"
            f"当前 task_spec:\\n{self._format_task_spec(state)}\\n\\n"
            f"本轮已执行工具: {executed_history or []}\\n\\n"
            "要求:\\n"
            "1) 优先在用户明确要求电学仿真/曲线/指标（如 IdVg/IdVd/IV/Ion/Ioff/SS/validate）时，推进到 SDevice 与 validate。\\n"
            "2) 若用户目标是局部任务，只给最小必要下一步。\\n"
            "3) 不能跳过关键依赖。\\n"
            "4) 避免重复规划本轮已执行过且未改变目标状态的工具。\\n"
            "5) 结构图导出只用 run_svisual_sde_export；曲线导出只用 run_svisual_export。\\n"
            "6) 对 run_bash，尽量提供 next_args。图像内容问题优先直接多模态回答，不要额外规划图片问答工具。\\n"
            "7) 当 done=false 时，assistant_pre 必须给出自然中文句子（说明这一步的目的/依据）；assistant_post_success/assistant_post_failure 为可选，仅在有新增信息时填写，避免与 assistant_pre 重复。\\n"
            "8) 必须尊重 target_artifact；若 target_artifact 是 structure/structure_png/tdr_info，则不要规划 SDevice 链。\\n"
            "9) 若用户仅描述结构/工艺参数，或只说“电学性能”但未明确仿真动作，优先停在 SDE 链路（可到 inspect_tdr / run_svisual_sde_export），必要时先澄清再进入 SDevice。\\n"
            "10) 若本轮用户明确改变了目标终点（例如只要结构图/只看 tdr_info/继续 IV 曲线/继续验证），请在 JSON 中返回最贴近的 target_artifact；否则返回当前 target_artifact 或 unspecified。\n"
            "11) 输出必须是合法 JSON。"
        )
        self.tracer.event(
            "MainAgent",
            "compose_prompt",
            {"operation": "decide_next_operation", "instruction": preview_text(instruction), "stage": state.stage},
            session_id=state.session_id,
        )

        source = "llm"
        reason = ""
        next_op: str | None = None
        next_args: dict[str, Any] = {}
        assistant_reply = ""
        assistant_pre = ""
        assistant_post_success = ""
        assistant_post_failure = ""
        llm_error_message = ""
        raw_payload: dict[str, Any] | None = None
        planned_candidate_name: str | None = None
        try:
            raw = self.llm.chat_main(
                [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                verbose=False,
            )
            raw_payload = self._extract_json_block(raw)
        except Exception as exc:
            llm_error_message = str(exc)
            self.tracer.event(
                "MainAgent",
                "decide_llm_error",
                {"error": str(exc)},
                session_id=state.session_id,
            )
            raw_payload = None

        if raw_payload:
            parsed_target = normalize_target_artifact(str(raw_payload.get("target_artifact", "unspecified")))
            if parsed_target != "unspecified":
                state.spec.target_artifact = parsed_target
                self._save_state(state)
            done_flag = bool(raw_payload.get("done", False))
            reason = str(raw_payload.get("reason", "llm"))
            assistant_reply = str(raw_payload.get("assistant_reply", "")).strip()
            assistant_pre = str(raw_payload.get("assistant_pre", "")).strip()
            assistant_post_success = str(raw_payload.get("assistant_post_success", "")).strip()
            assistant_post_failure = str(raw_payload.get("assistant_post_failure", "")).strip()
            if done_flag:
                if not assistant_reply:
                    assistant_reply = self._language_only_reply(instruction, state)
                return {
                    "session_id": state.session_id,
                    "stage": state.stage,
                    "done": True,
                    "next_operation": None,
                    "next_tool": None,
                    "reason": reason or "llm_done",
                    "source": source,
                    "assistant_reply": assistant_reply,
                    "assistant_pre": "",
                    "assistant_post_success": "",
                    "assistant_post_failure": "",
                }
            candidate = raw_payload.get("next_tool")
            if candidate is None:
                candidate = raw_payload.get("next_operation")
            if candidate is None:
                candidate = raw_payload.get("operation")
            if isinstance(candidate, str) and candidate.strip():
                candidate_name = candidate.strip()
                planned_candidate_name = candidate_name
                if candidate_name in tool_names:
                    next_op = candidate_name
            args_payload = raw_payload.get("next_args", {})
            if isinstance(args_payload, dict):
                next_args = args_payload

        guard_candidate = next_op or planned_candidate_name
        if guard_candidate and target_blocks_sdevice(state.spec.target_artifact) and guard_candidate in SDEVICE_CHAIN_TOOLS:
            satisfied = target_satisfied(state.stage, state.spec.target_artifact)
            reason = reason or "target_artifact_guard"
            return {
                "session_id": state.session_id,
                "stage": state.stage,
                "done": True,
                "next_operation": None,
                "next_tool": None,
                "reason": "target_artifact_guard",
                "source": source,
                "assistant_reply": (
                    f"当前目标已限定在 `{state.spec.target_artifact}`，系统不会继续推进到 SDevice。"
                    + (" 结构侧目标已满足。" if satisfied else " 如需继续电学仿真，请明确把目标改为 iv_curve、validation_report 或 full_chain。")
                ),
                "assistant_pre": "",
                "assistant_post_success": "",
                "assistant_post_failure": "",
            }

        if not next_op:
            if not reason:
                reason = "fallback_due_to_invalid_llm_output"

        if not next_op and not assistant_reply:
            if llm_error_message:
                assistant_reply = (
                    "主模型调用失败，无法生成下一步操作。"
                    f"错误: {llm_error_message}. "
                    "请检查 TCAD_LLM_API_KEY 及模型/网关可用性。"
                )
            else:
                assistant_reply = self._language_only_reply(instruction, state)

        done = next_op is None
        self.tracer.event(
            "MainAgent",
            "decide_next_result",
            {
                "instruction": preview_text(instruction),
                "source": source,
                "done": done,
                "next_operation": next_op,
                "reason": reason,
                "raw_payload": raw_payload or {},
                "assistant_reply": preview_text(assistant_reply, 300) if assistant_reply else "",
                "assistant_pre": preview_text(assistant_pre, 200) if assistant_pre else "",
                "assistant_post_success": preview_text(assistant_post_success, 200) if assistant_post_success else "",
                "assistant_post_failure": preview_text(assistant_post_failure, 200) if assistant_post_failure else "",
            },
            session_id=state.session_id,
        )
        return {
            "session_id": state.session_id,
            "stage": state.stage,
            "done": done,
            "next_operation": next_op,
            "next_tool": next_op,
            "next_args": next_args,
            "reason": reason or source,
            "source": source,
            "assistant_reply": assistant_reply,
            "assistant_pre": assistant_pre,
            "assistant_post_success": assistant_post_success,
            "assistant_post_failure": assistant_post_failure,
        }

    # ━━━━━━━━━━━━━━━━ 操作分发与自动执行 ━━━━━━━━━━━━━━

    def run_operation(
        self,
        op: str,
        args: dict[str, Any] | None = None,
        instruction: str = "",
        reason: str = "",
        assistant_pre: str = "",
        assistant_post_success: str = "",
        assistant_post_failure: str = "",
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """执行单个 MCP 工具，并打印每步进展。"""
        state = self._load_state()
        if assistant_pre:
            print(assistant_pre, file=sys.stderr, flush=True)
            self._emit_runtime_event(event_sink, {"kind": "assistant_chunk", "chunk": assistant_pre})
        print(f"\n[→] {op}", file=sys.stderr, flush=True)
        tools = set(self.mcp_tools.list_tool_names())
        mcp_tool = op
        if mcp_tool not in tools:
            raise ValueError(f"Unsupported tool: {op}")
        self.tracer.event(
            "MainAgent",
            "mcp_tool_call",
            {"tool": mcp_tool, "mapped_operation": op},
            session_id=self.DEFAULT_SESSION,
        )
        call_kwargs: dict[str, Any] = dict(args or {})
        tool_event_id = f"{reason or mcp_tool}:{time.time_ns()}"
        if mcp_tool in {"run_svisual_export", "run_svisual_sde_export"}:
            # 显式选择 source_file，避免工具层隐式兜底导致行为不透明。
            if not call_kwargs.get("source_file"):
                chosen = ""
                keys = ["plot"] if mcp_tool == "run_svisual_export" else ["mesh", "tdr"]
                for key in keys:
                    p = state.artifacts.get(key, "")
                    if p and Path(p).exists():
                        chosen = p
                        break
                # 纠偏：若误选了曲线导出工具但只有结构 TDR，则自动切到结构导出。
                if mcp_tool == "run_svisual_export" and not chosen:
                    for key in ["tdr", "mesh"]:
                        p = state.artifacts.get(key, "")
                        if p and Path(p).exists():
                            mcp_tool = "run_svisual_sde_export"
                            chosen = p
                            break
                call_kwargs["source_file"] = chosen
            if not call_kwargs.get("mode"):
                call_kwargs["mode"] = "plt" if mcp_tool == "run_svisual_export" else "tdr"
        elif mcp_tool == "run_bash":
            if not call_kwargs.get("command"):
                call_kwargs["command"] = "ls -la"
            call_kwargs.setdefault("cwd", str(state.session_dir))
        self._emit_runtime_event(
            event_sink,
            {
                "kind": "tool_start",
                "event_id": tool_event_id,
                "tool_name": mcp_tool,
                "mapped_operation": op,
                "stage": state.stage,
                "args": call_kwargs,
                "reason": reason,
            },
        )
        result = self.mcp_tools.call_tool(mcp_tool, **call_kwargs)
        stage = str(result.get("stage", "?"))
        # 判断本次 tool 调用是否成功：
        # 1) 有 run_result.success 则以其为准；
        # 2) 有 generation.ok 则以其为准；
        # 3) 只读/查询型工具（show_state）默认成功；
        # 4) 其它情况下回退到 stage 是否 *_failed。
        ok = True
        run_result = result.get("run_result")
        generation = result.get("generation")
        if isinstance(run_result, dict) and "success" in run_result:
            ok = bool(run_result.get("success"))
        elif isinstance(generation, dict) and "ok" in generation:
            ok = bool(generation.get("ok"))
        elif mcp_tool in {"show_state"}:
            ok = True
        else:
            ok = not stage.endswith("failed")
        symbol = "✓" if ok else "✗"
        print(f"[{symbol}] {op} → stage={stage}", file=sys.stderr, flush=True)
        arts = result.get("artifacts", {})
        # 输出关键产物路径，避免用户“看不到文件在哪里”。
        key_map = {
            "run_bash": ["last_bash_log"],
            "generate_sde_code": ["sde_cmd"],
            "run_sde": ["mesh", "bnd", "log_sde"],
            "run_svisual_sde_export": ["svisual_source", "svisual_png", "svisual_script"],
            "inspect_tdr": ["tdr_info_report"],
            "generate_sdevice_code": ["sdevice_cmd"],
            "run_sdevice": ["plot", "tdr", "log_sdevice"],
            "run_svisual_export": ["svisual_source", "svisual_png", "svisual_curve_txt", "svisual_script"],
            "validate_results": ["validation_report"],
        }
        for k in key_map.get(mcp_tool, []):
            if arts.get(k):
                print(f"    {k}: {arts[k]}", file=sys.stderr, flush=True)
        self.tracer.event(
            "MainAgent",
            "mcp_tool_done",
            {"tool": mcp_tool, "mapped_operation": op, "stage": stage, "ok": ok, "args": call_kwargs},
            session_id=result.get("session_id", self.DEFAULT_SESSION),
        )
        self._emit_runtime_event(
            event_sink,
            {
                "kind": "tool_end",
                "event_id": tool_event_id,
                "tool_name": mcp_tool,
                "mapped_operation": op,
                "stage": stage,
                "ok": ok,
                "args": call_kwargs,
            },
        )
        for key in key_map.get(mcp_tool, []):
            if arts.get(key):
                self._emit_runtime_event(
                    event_sink,
                    {
                        "kind": "artifact",
                        "event_id": tool_event_id,
                        "tool_name": mcp_tool,
                        "artifact_key": key,
                        "artifact_path": arts[key],
                        "stage": stage,
                    },
                )
        result["_tool_ok"] = ok
        if not ok:
            # 打印失败摘要：从 notes 或 stage 提取错误信息
            notes = result.get("notes", [])
            last_note = notes[-1] if notes else ""
            print(f"    失败原因: {last_note}", file=sys.stderr, flush=True)
            # 打印关键日志路径
            for key in ["log_sde_syntax", "log_sde", "log_sdevice_syntax", "log_sdevice", "last_log_hint"]:
                if arts.get(key):
                    print(f"    日志: {arts[key]}", file=sys.stderr, flush=True)
                    break
            if assistant_post_failure and assistant_post_failure != assistant_pre:
                print(assistant_post_failure, file=sys.stderr, flush=True)
                self._emit_runtime_event(event_sink, {"kind": "assistant_chunk", "chunk": assistant_post_failure})
        else:
            if assistant_post_success and assistant_post_success != assistant_pre:
                print(assistant_post_success, file=sys.stderr, flush=True)
                self._emit_runtime_event(event_sink, {"kind": "assistant_chunk", "chunk": assistant_post_success})
        return result

    def agent_decide_and_execute(
        self,
        instruction: str,
        *,
        should_abort: Callable[[], bool] | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """真实计划驱动的执行循环：先生成完整计划，再逐步推进。"""
        executed: list[str] = []
        last: dict[str, Any] = {}
        assistant_reply = ""
        aborted = False
        max_steps = 24
        print(f"[workspace] {self.runtime_root}", file=sys.stderr, flush=True)

        if not self.state_file.exists():
            self.create_session(instruction)
        state = self._load_state()

        direct_image_reply = self._direct_multimodal_image_reply(instruction, state)
        if direct_image_reply is not None:
            assistant_reply = str(direct_image_reply.get("assistant_reply", "")).strip()
            if assistant_reply:
                self._emit_runtime_event(event_sink, {"kind": "assistant_chunk", "chunk": assistant_reply})
            return direct_image_reply

        if instruction.strip():
            state.artifacts["last_user_instruction"] = instruction.strip()

        available_tool_names = (
            self.mcp_tools.list_tool_names()
            if hasattr(self, "mcp_tools") and hasattr(self.mcp_tools, "list_tool_names")
            else list(self.MCP_TOOL_ORDER)
        )
        build_execution_plan(
            state=state,
            instruction=instruction or state.spec.requirement,
            tool_names=available_tool_names,
        )
        self._save_state(state)
        if state.plan_steps:
            first_titles = "、".join(step.title for step in state.plan_steps[:4])
            self._emit_runtime_event(
                event_sink,
                {
                    "kind": "assistant_chunk",
                    "chunk": f"我先列一下这轮计划：{first_titles}。",
                },
            )
            self._emit_plan_created(state, event_sink)

        if target_satisfied(state.stage, state.spec.target_artifact):
            finalize_plan_if_done(state)
            self._save_state(state)
            if state.plan_steps:
                self._emit_plan_completed(state, event_sink)
            assistant_reply = "当前目标已经满足，我先不重复执行。若你需要继续扩展到下一阶段，可以直接给我新的指令。"
            self._emit_runtime_event(event_sink, {"kind": "assistant_chunk", "chunk": assistant_reply})
            return self._dump(
                state,
                {
                    "decision_mode": "plan_driven",
                    "executed_operations": executed,
                    "last_operation_result": last,
                    "assistant_reply": assistant_reply,
                    "max_steps": max_steps,
                    "aborted": False,
                },
            )

        for _ in range(max_steps):
            if should_abort is not None and should_abort():
                aborted = True
                assistant_reply = assistant_reply or "当前会话已收到中止请求，已停止继续执行。"
                break

            step = select_next_plan_step(state)
            if step is None:
                break

            step = update_plan_step(state, step_id=step.step_id, status="in_progress") or step
            self._save_state(state)
            self._emit_plan_step_update(state, step, event_sink)

            last = self.run_operation(
                step.tool_name,
                args=step.tool_args,
                instruction=instruction,
                reason=f"plan_step:{step.step_id}",
                assistant_pre=step.narration_pre,
                assistant_post_success=step.narration_post_success,
                assistant_post_failure=step.narration_post_failure,
                event_sink=event_sink,
            )
            executed.append(step.tool_name)

            state = self._load_state()
            note = state.notes[-1] if state.notes else str(last.get("stage", ""))
            step_status = "completed" if bool(last.get("_tool_ok", True)) else "failed"
            step = update_plan_step(state, step_id=step.step_id, status=step_status, note=note) or step

            if bool(last.get("_tool_ok", True)):
                finalize_plan_if_done(state)
            self._save_state(state)
            self._emit_plan_step_update(state, step, event_sink)

            if not bool(last.get("_tool_ok", True)):
                replanned = replan_failed_tail(state, failed_step_id=step.step_id)
                if replanned:
                    state.notes.append(f"Auto replanned after {step.step_id}.")
                    self._save_state(state)
                    self._emit_plan_replanned(state, event_sink, failed_step_id=step.step_id)
                    continue
                break

            if should_abort is not None and should_abort():
                aborted = True
                assistant_reply = assistant_reply or "当前会话已收到中止请求，已停止继续执行。"
                break

            if target_satisfied(state.stage, state.spec.target_artifact):
                finalize_plan_if_done(state)
                self._save_state(state)
                break
            if str(last.get("stage", "")) == "validated":
                finalize_plan_if_done(state)
                self._save_state(state)
                break

        if not self.state_file.exists():
            if assistant_reply:
                self._emit_runtime_event(event_sink, {"kind": "assistant_chunk", "chunk": assistant_reply})
            return {
                "session_id": self.DEFAULT_SESSION,
                "stage": "no_session",
                "decision_mode": "plan_driven",
                "executed_operations": executed,
                "last_operation_result": last,
                "assistant_reply": assistant_reply,
                "max_steps": max_steps,
                "aborted": aborted,
            }

        state = self._load_state()
        finalize_plan_if_done(state)
        self._save_state(state)
        plan_completed = bool(state.plan_steps) and not aborted and not state.blocker and select_next_plan_step(state) is None
        if plan_completed:
            self._emit_plan_completed(state, event_sink)

        if not executed and not assistant_reply:
            assistant_reply = "当前没有可继续推进的计划步骤。你可以继续细化需求，或明确下一阶段目标。"
            self._emit_runtime_event(event_sink, {"kind": "assistant_chunk", "chunk": assistant_reply})
        elif not assistant_reply:
            assistant_reply = ""

        return self._dump(
            state,
            {
                "decision_mode": "plan_driven",
                "executed_operations": executed,
                "last_operation_result": last,
                "assistant_reply": assistant_reply,
                "max_steps": max_steps,
                "aborted": aborted,
            },
        )
