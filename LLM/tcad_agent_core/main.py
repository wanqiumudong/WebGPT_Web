#!/usr/bin/env python3
from __future__ import annotations

"""TCAD Agent 默认交互入口。

- 直接运行 `python3 main.py` 进入交互界面。
- 不提供单次执行参数与子命令。
- 清理能力通过交互命令 `/clean` 提供。
"""

import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from src.agent_system import TCADAgentSystem
from src.core import preview_text

try:
    # prompt_toolkit 在中英文混输场景下比内置 input 更稳。
    from prompt_toolkit import PromptSession
except Exception:  # pragma: no cover - 依赖缺失时自动回退
    PromptSession = None


WORKSPACE = Path(__file__).resolve().parent
PINNED_MODEL = "gemini-3.1-flash-lite-preview"
_ANSI_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ANSI_SS3_RE = re.compile(r"\x1bO.")
_CARET_ARROW_RE = re.compile(r"(?:\^\[\[[ABCD])+")


def _pin_models_for_main() -> None:
    """在 main.py 入口内固定模型，避免每次手动传环境变量。"""
    os.environ["TCAD_LLM_MODEL"] = PINNED_MODEL
    os.environ["TCAD_MODEL_MAIN"] = PINNED_MODEL
    os.environ["TCAD_MODEL_SDE"] = PINNED_MODEL
    os.environ["TCAD_MODEL_SDEVICE"] = PINNED_MODEL


def clean_runtime() -> None:
    """清理 runtime/default 下的运行产物和状态。"""
    runtime = WORKSPACE / "runtime" / "default"
    for sub in ["run", "logs", "reports"]:
        d = runtime / sub
        if not d.exists():
            continue
        for child in d.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
    state = runtime / "state.json"
    if state.exists():
        state.unlink(missing_ok=True)


def rotate_default_runtime() -> Path | None:
    """启动时归档旧 default 目录，并重建新的 default 工作目录。"""
    runtime_root = WORKSPACE / "runtime"
    default_dir = runtime_root / "default"
    timeline_root = runtime_root / "timeline"
    runtime_root.mkdir(parents=True, exist_ok=True)

    archived_to: Path | None = None
    if default_dir.exists():
        timeline_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archived_to = timeline_root / f"default_{stamp}"
        suffix = 1
        while archived_to.exists():
            archived_to = timeline_root / f"default_{stamp}_{suffix:02d}"
            suffix += 1
        shutil.move(str(default_dir), str(archived_to))

    for sub in ["run", "logs", "reports"]:
        (default_dir / sub).mkdir(parents=True, exist_ok=True)
    return archived_to


def _client_help() -> str:
    return (
        "可用命令:\n"
        "  /state：查看当前工作进程的状态\n"
        "  /tools：查看可用工具与智能体\n"
        "  /clean：清理当前工作进程\n"
        "  /exit：退出"
    )


def _print_dialog_reply(out: dict) -> None:
    """普通对话场景只显示主模型回复，减少 JSON 噪声。"""
    reply = str(out.get("assistant_reply", "")).strip() if isinstance(out, dict) else ""
    if reply:
        print(reply)
        return
    # 兜底：若模型未给文本，给一行最小提示。
    stage = str(out.get("stage", "")).strip() if isinstance(out, dict) else ""
    if stage:
        print(f"已完成本轮处理（stage={stage}）。可用 /state 查看详细信息。")
    else:
        print("已完成本轮处理。可用 /state 查看详细信息。")


def _build_line_reader():
    """返回交互读入函数。

    交互 TTY 下强制使用 prompt_toolkit，确保方向键是“移动光标”而不是字符回显。
    非交互管道模式（如 echo | python main.py）才回退到 input。
    """
    is_tty = sys.stdin.isatty() and sys.stdout.isatty()
    if is_tty:
        if PromptSession is None:
            raise RuntimeError(
                "当前交互模式需要 prompt_toolkit。请执行: pip install -r requirements.txt"
            )
        session = PromptSession()

        def _read() -> str:
            return session.prompt("\nClient> ").strip()

        return _read

    def _read_fallback() -> str:
        return input("\nClient> ").strip()

    return _read_fallback


def _normalize_user_input(text: str) -> str:
    """过滤方向键等终端转义符，避免污染用户输入。"""
    cleaned = _ANSI_CSI_RE.sub("", text)
    cleaned = _ANSI_SS3_RE.sub("", cleaned)
    cleaned = _CARET_ARROW_RE.sub("", cleaned)
    return cleaned.strip()


def run_client_mode(workspace: Path) -> int:
    """运行用户交互客户端。"""
    archived = rotate_default_runtime()
    agent = TCADAgentSystem(workspace)
    read_line = _build_line_reader()

    print("=" * 64)
    print("TCAD Agent Client")
    print("输入 /help 查看命令")
    print("=" * 64)
    if archived is not None:
        print(f"已归档上一轮目录: {archived}")

    while True:
        try:
            raw_user = read_line()
        except (EOFError, KeyboardInterrupt):
            print("\n退出。")
            return 0

        user = _normalize_user_input(raw_user)
        if not user:
            continue

        agent.tracer.event(
            "ClientUI",
            "user_input",
            {
                "raw_input": preview_text(raw_user),
                "normalized_input": preview_text(user),
                "active_session": "default",
            },
            session_id="default",
        )

        if user.lower() in {"/exit", "/quit", "exit", "quit"}:
            print("结束。")
            return 0
        if user == "/help":
            print(_client_help())
            continue

        try:
            if user == "/clean":
                clean_runtime()
                # 重新实例化，确保 tracer/session 状态与文件系统一致。
                agent = TCADAgentSystem(workspace)
                out = {"ok": True, "message": "runtime/default 已清理"}
                print(out["message"])
            elif user == "/state":
                out = agent.show_state()
                print(json.dumps(out, ensure_ascii=False, indent=2))
            elif user in {"/tools", "/caps"}:
                out = agent.show_capabilities()
                print(json.dumps(out, ensure_ascii=False, indent=2))
            else:
                out = agent.agent_decide_and_execute(user)
                _print_dialog_reply(out)
        except Exception as exc:
            agent.tracer.event(
                "ClientUI",
                "error",
                {"error": str(exc), "raw_input": preview_text(user)},
                session_id="default",
            )
            print(f"[ERROR] {exc}")


def main() -> int:
    _pin_models_for_main()
    if len(sys.argv) > 1:
        print("请直接运行: python3 main.py", file=sys.stderr)
        return 2
    return run_client_mode(WORKSPACE)


if __name__ == "__main__":
    raise SystemExit(main())
