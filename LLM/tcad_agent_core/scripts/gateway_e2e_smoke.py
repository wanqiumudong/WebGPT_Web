#!/usr/bin/env python3
from __future__ import annotations

"""TCAD Gateway end-to-end smoke script.

目标：一键验证 gateway 主链路可用：
start -> call -> call_async -> stop
"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _json_print(title: str, payload: dict) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    root = _project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from mcp.service import TcadGatewayMCPService

    parser = argparse.ArgumentParser(description="Smoke test for TCAD managed gateway.")
    parser.add_argument("--workspace", type=str, default=str(root), help="Project workspace path.")
    parser.add_argument(
        "--setup-workdir",
        type=str,
        default="",
        help="Managed instance workdir. Default: runtime/gateway_smoke_<timestamp>",
    )
    parser.add_argument(
        "--with-session",
        action="store_true",
        help="Also run api_tcad_create_session (will invoke requirement parsing).",
    )
    parser.add_argument(
        "--requirement",
        type=str,
        default="仅创建测试会话，不执行仿真。",
        help="Requirement text used when --with-session is enabled.",
    )
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.exists():
        print(f"[ERROR] workspace not found: {workspace}", file=sys.stderr)
        return 2

    if args.setup_workdir.strip():
        setup_workdir = Path(args.setup_workdir).expanduser().resolve()
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        setup_workdir = workspace / "runtime" / f"gateway_smoke_{stamp}"
    setup_workdir.mkdir(parents=True, exist_ok=True)

    service = TcadGatewayMCPService(workspace=workspace, max_async_workers=2)
    try:
        started = service.start_tcad_server(str(setup_workdir))
        _json_print("start_tcad_server", started)
        if not started.get("ok"):
            return 1
        iid = str(started["instance_id"])

        handshake = service.call(method="api_tcad_handshake", instance_id=iid)
        _json_print("call(api_tcad_handshake)", handshake)
        if not handshake.get("ok"):
            return 1

        methods = service.call(method="api_tcad_list_methods", instance_id=iid)
        _json_print("call(api_tcad_list_methods)", methods)
        if not methods.get("ok"):
            return 1

        caps = service.call(method="api_tcad_show_capabilities", instance_id=iid)
        _json_print("call(api_tcad_show_capabilities)", caps)
        if not caps.get("ok"):
            return 1

        if args.with_session:
            created = service.call(
                method="api_tcad_create_session",
                params={"requirement": args.requirement},
                instance_id=iid,
            )
            _json_print("call(api_tcad_create_session)", created)
            if not created.get("ok"):
                return 1

        state = service.call(method="api_tcad_show_state", instance_id=iid)
        _json_print("call(api_tcad_show_state)", state)
        if not state.get("ok"):
            return 1

        job = service.call_async_start(method="api_tcad_ping", instance_id=iid)
        _json_print("call_async_start(api_tcad_ping)", job)
        if not job.get("ok"):
            return 1

        waited = service.call_async_wait(job_id=str(job["job_id"]), wait_timeout_ms=5000, include_response=True)
        _json_print("call_async_wait", waited)
        if not waited.get("ok") or not waited.get("done", False):
            return 1

        stopped = service.stop_server(instance_id=iid)
        _json_print("stop_server", stopped)
        if not stopped.get("ok"):
            return 1

        print("\n[OK] gateway smoke passed.")
        return 0
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
