from __future__ import annotations

"""MCP stdio 服务入口层。

职责边界：
- 只负责构建/启动 FastMCP 服务
- 不在此文件实现具体工具逻辑（由 `tool_service.py` 承担）
"""

import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

THIS_DIR = Path(__file__).resolve().parent
ROOT = THIS_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from src.core import DebugTracer
from tool_service import MCPToolService, register_tools


def build_server(workspace: Path) -> FastMCP:
    """创建 FastMCP 实例并完成工具注册。"""
    tracer = DebugTracer(workspace)
    service = MCPToolService(workspace, tracer=tracer)
    mcp = FastMCP("tcad-sentaurus")
    tracer.event("MCPServer", "init", {"workspace": str(workspace), "name": "tcad-sentaurus"})
    register_tools(mcp, service)
    return mcp


def run_stdio(workspace: Path) -> None:
    """以 stdio 传输启动 MCP 服务。"""
    server = build_server(workspace)
    server.run(transport="stdio")


if __name__ == "__main__":
    run_stdio(Path("/data/yphu/TCAD_Agent/code"))
