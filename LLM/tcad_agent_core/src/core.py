from __future__ import annotations

"""核心基础模块 —— Sentaurus TCAD Agent 的数据结构与基础能力。

本模块是整个 Agent 系统的基座，提供四类能力：

1. 数据结构  — SessionSpec / SessionState / StepResult / ValidationResult
               定义从需求输入到仿真产物再到验证结果的完整数据流。
2. 需求兜底  — parse_requirement()
               当上层 LLM 解析失败时，返回无规则、无关键词匹配的最小规格。
3. 技能加载  — SkillLibrary
               读取 skills/*/SKILL.md 提供领域知识给 LLM prompt。
4. 调试追踪  — DebugTracer
               JSONL 格式的结构化事件日志，支持全局/会话双通道。
"""

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .task_spec import TaskSpec


# ━━━━━━━━━━━━━━━━━━━━━━ 数据结构定义 ━━━━━━━━━━━━━━━━━━━━━━
# 以下 dataclass 构成 Agent 的核心数据流：
#   用户需求 → SessionSpec → SessionState → StepResult → ValidationResult


@dataclass
class Targets:
    """用户可能指定的性能目标。

    所有字段可选——未指定时跳过对应的验证检查。
    """

    ion_min: float | None = None          # 开态电流下限 (A)
    ioff_max: float | None = None         # 关态电流上限 (A)
    ss_max_mv_dec: float | None = None    # 亚阈值摆幅上限 (mV/dec)，理想值 ~60


@dataclass
class SessionSpec:
    """会话规格：从用户需求中抽取的结构化输入。

    由 parse_requirement() 自动生成，驱动后续的
    SDE 建模 → SDevice 仿真 → SVisual 后处理 流程。
    """

    requirement: str                                        # 原始需求文本
    device_type: str = "unspecified"                        # 器件类型（由 LLM 解析）
    simulation_type: str = "unspecified"                    # 仿真类型（由 LLM 解析）
    target_artifact: str = "unspecified"                    # 本轮目标终点（结构图/曲线/验证等）
    parameters: dict[str, float] = field(default_factory=dict)   # 物理参数（Lg/tox/Vd 等）
    targets: Targets = field(default_factory=Targets)             # 性能目标
    task_spec: TaskSpec = field(default_factory=TaskSpec)         # 声明式任务规格


@dataclass
class TaskTodo:
    """任务视图中的精简 Todo。"""

    content: str
    status: str = "pending"


@dataclass
class PlanStep:
    """真实执行计划中的单步定义。"""

    step_id: str
    title: str
    tool_name: str
    tool_args: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    expected_artifacts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    attempt: int = 1
    narration_pre: str = ""
    narration_post_success: str = ""
    narration_post_failure: str = ""


@dataclass
class SessionState:
    """会话运行态：跟踪整个仿真流程的阶段、产物与指标。

    每个 session 拥有独立目录，生命周期为：
    created → sde_done → sdevice_done → svisual_done → validated
    """

    session_id: str
    session_dir: Path
    spec: SessionSpec
    stage: str = "created"                                  # 当前阶段标识
    artifacts: dict[str, str] = field(default_factory=dict) # 产物路径映射（键=角色, 值=路径）
    metrics: dict[str, float] = field(default_factory=dict) # 提取的性能指标
    notes: list[str] = field(default_factory=list)          # 过程备注 / 修复记录
    task_summary: str = ""                                  # 当前任务一句话摘要
    done_criteria: list[str] = field(default_factory=list)  # 当前任务完成标准
    todos: list[TaskTodo] = field(default_factory=list)     # 当前任务 Todo 列表
    current_step: str = ""                                  # 当前正在推进的步骤
    blocker: str = ""                                       # 当前阻塞
    next_step_hint: str = ""                                # 下一步建议
    plan_steps: list[PlanStep] = field(default_factory=list)  # 真实执行计划
    plan_id: str = ""                                       # 当前计划标识
    plan_attempt: int = 0                                   # 当前计划重规划次数（含首轮）


@dataclass
class StepResult:
    """单个执行步骤的标准返回结构。

    每个 Runner（SDE/SDevice/SVisual）执行后统一返回此结构，
    便于 Orchestrator 做状态推进与错误判断。
    """

    success: bool
    stage: str
    message: str
    logs: dict[str, str] = field(default_factory=dict)      # 日志文件路径
    artifacts: dict[str, str] = field(default_factory=dict) # 新产物路径
    details: dict[str, Any] = field(default_factory=dict)   # 附加诊断信息


@dataclass
class ValidationResult:
    """物理级验证结果。

    由 PhysicalValidator 生成，包含多维度检查项和提取的电学指标。
    """

    success: bool
    message: str
    checks: dict[str, bool]       # 各维度检查通过/失败
    metrics: dict[str, float]     # 提取的电学指标（Ion/Ioff/SS 等）


# ━━━━━━━━━━━━━━━━━━━━━━ 通用工具函数 ━━━━━━━━━━━━━━━━━━━━━━


def to_jsonable(obj: Any) -> Any:
    """将 Path / dataclass 等对象递归转为可 JSON 序列化结构。

    Agent 需要将中间状态写入 JSONL 日志，而 Path 和 dataclass
    无法直接 json.dumps，故需要此递归转换。
    """
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "__dataclass_fields__"):
        return {k: to_jsonable(v) for k, v in obj.__dict__.items()}
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(x) for x in obj]
    return obj


def preview_text(text: str, max_chars: int = 1800) -> str:
    """截断长文本，避免日志 / LLM prompt 超长。

    Sentaurus 输出动辄上万行，直接塞进 prompt 会浪费 token 且降低效果。
    """
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


# ━━━━━━━━━━━━━━━━━━━━━━ 调试追踪器 ━━━━━━━━━━━━━━━━━━━━━━


class DebugTracer:
    """轻量 JSONL 追踪器——记录 Agent 每一步行为，便于事后回溯。

    双通道输出：
    - 全局日志：runtime/default/reports/debug_global.jsonl  （跨会话）
    - 会话日志：runtime/default/logs/debug_trace.jsonl      （单会话）

    通过环境变量 TCAD_DEBUG 控制开关（默认开启）。
    """

    def __init__(self, workspace: Path, enabled: bool | None = None) -> None:
        # 环境变量控制：TCAD_DEBUG=0/false/off/no 关闭追踪
        env = os.getenv("TCAD_DEBUG", "1").strip().lower()
        if enabled is None:
            enabled = env not in {"0", "false", "off", "no"}
        self.enabled = enabled
        self.workspace = workspace
        self.global_log = workspace / "runtime" / "default" / "reports" / "debug_global.jsonl"
        self.session_log: Path | None = None
        self._lock = threading.Lock()
        self.global_log.parent.mkdir(parents=True, exist_ok=True)

    def bind_session(self, session_dir: Path, session_id: str) -> None:
        """绑定会话后，后续事件同时写入全局与会话日志。

        必须在会话创建后立即调用，否则事件只写全局日志。
        """
        if not self.enabled:
            return
        log_dir = session_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.session_log = log_dir / "debug_trace.jsonl"
        self.event(
            "DebugTracer",
            "bind_session",
            {
                "session_id": session_id,
                "session_dir": str(session_dir),
                "session_log": str(self.session_log),
            },
            session_id=session_id,
        )

    def event(
        self,
        component: str,
        action: str,
        payload: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> None:
        """写入一条结构化追踪事件。

        线程安全——内部加锁，保证多线程场景下日志不交错。
        """
        if not self.enabled:
            return
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "component": component,
            "action": action,
            "session_id": session_id,
            "payload": to_jsonable(payload or {}),
        }
        line = json.dumps(rec, ensure_ascii=False)
        with self._lock:
            with self.global_log.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            if self.session_log is not None:
                with self.session_log.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")


def parse_requirement(requirement: str) -> SessionSpec:
    """最小兜底解析（无关键词匹配、无参数提取）。"""
    req = requirement.strip()
    return SessionSpec(
        requirement=req,
        device_type="unspecified",
        simulation_type="unspecified",
        target_artifact="unspecified",
        parameters={},
        targets=Targets(),
        task_spec=TaskSpec(),
    )


# ━━━━━━━━━━━━━━━━━━━━━━ 技能文本加载 ━━━━━━━━━━━━━━━━━━━━━━


class SkillLibrary:
    """读取 skills/*/SKILL.md 的简单加载器。

    每个 skill 目录包含一个 SKILL.md，提供该步骤的领域知识
    （如 SDE 建模要点、SDevice 物理模型选择等），
    在构建 LLM prompt 时注入以提升生成质量。
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.skills_dir = workspace / "skills"

    def load(self, name: str, fallback: str = "") -> str:
        p = self.skills_dir / name / "SKILL.md"
        if not p.exists():
            return fallback
        text = p.read_text(encoding="utf-8", errors="ignore").strip()
        return text or fallback

    def list_names(self) -> list[str]:
        """列出当前可用 skill 名称（目录下存在 SKILL.md）。"""
        if not self.skills_dir.exists():
            return []
        names: list[str] = []
        for item in sorted(self.skills_dir.iterdir(), key=lambda p: p.name):
            if not item.is_dir():
                continue
            if (item / "SKILL.md").exists():
                names.append(item.name)
        return names
