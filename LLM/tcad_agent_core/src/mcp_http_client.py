from __future__ import annotations

"""Sync wrapper for TCAD MCP streamable HTTP transport.

This module is optional. It is kept separate from the local direct-execution
path so `main.py` and the core agent can stay lightweight.
"""

import importlib
import json
from pathlib import Path
import sys
from typing import Any


class MCPHTTPClientError(RuntimeError):
    """Raised when MCP HTTP tool invocation fails."""


def _import_external_mcp_client() -> tuple[Any, Any, Any, Any, Any]:
    """Import site-packages `mcp` without colliding with local `mcp/` package."""
    repo_root = Path(__file__).resolve().parent.parent
    original_sys_path = list(sys.path)
    original_local_mcp = sys.modules.pop("mcp", None)
    try:
        filtered_path: list[str] = []
        for path_str in sys.path:
            if not path_str:
                continue
            try:
                if Path(path_str).resolve() == repo_root:
                    continue
            except Exception:
                pass
            filtered_path.append(path_str)
        sys.path = filtered_path

        anyio_mod = importlib.import_module("anyio")
        httpx_mod = importlib.import_module("httpx")
        mcp_mod = importlib.import_module("mcp")
        streamable_http_mod = importlib.import_module("mcp.client.streamable_http")
        return (
            anyio_mod,
            httpx_mod,
            getattr(mcp_mod, "ClientSession"),
            getattr(streamable_http_mod, "create_mcp_http_client"),
            getattr(streamable_http_mod, "streamable_http_client"),
        )
    finally:
        sys.path = original_sys_path
        if original_local_mcp is not None:
            sys.modules["mcp"] = original_local_mcp


class TcadMCPHTTPClient:
    """Thin sync wrapper over the MCP streamable HTTP client APIs."""

    def __init__(
        self,
        *,
        server_url: str,
        timeout_seconds: float = 30.0,
        sse_read_timeout_seconds: float = 600.0,
    ) -> None:
        self.server_url = server_url
        self.timeout_seconds = timeout_seconds
        self.sse_read_timeout_seconds = sse_read_timeout_seconds

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        anyio_mod, _, _, _, _ = _import_external_mcp_client()
        return anyio_mod.run(self._call_tool_async, name, arguments or {})

    def start_tcad_server(self, setup_workdir: str) -> dict[str, Any]:
        return self.call_tool("start_tcad_server", {"setup_workdir": setup_workdir})

    def call(
        self,
        *,
        method: str,
        params: Any = None,
        timeout_ms: int | None = None,
        return_mode: str | None = None,
        instance_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "method": method,
            "params": params,
            "timeout_ms": timeout_ms,
            "return_mode": return_mode,
            "instance_id": instance_id,
        }
        return self.call_tool("call", {key: value for key, value in payload.items() if value is not None})

    def list_instances(self) -> dict[str, Any]:
        return self.call_tool("list_instances", {})

    async def _call_tool_async(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        _, httpx_mod, client_session_cls, create_http_client, streamable_http_client = _import_external_mcp_client()
        http_client = create_http_client(
            timeout=httpx_mod.Timeout(self.timeout_seconds, read=self.sse_read_timeout_seconds)
        )
        async with http_client:
            async with streamable_http_client(
                url=self.server_url,
                http_client=http_client,
                terminate_on_close=True,
            ) as (read_stream, write_stream, _):
                async with client_session_cls(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(name, arguments)
                    decoded = self._decode_call_result(result)
                    if decoded.get("is_error"):
                        raise MCPHTTPClientError(f"MCP tool error ({name}): {decoded}")
                    return decoded

    @staticmethod
    def _decode_call_result(result: Any) -> dict[str, Any]:
        payload = result.model_dump(mode="python") if hasattr(result, "model_dump") else dict(result)
        out: dict[str, Any] = {
            "is_error": bool(payload.get("isError", False)),
            "structured": payload.get("structuredContent"),
            "content": payload.get("content", []),
            "raw": payload,
        }

        if isinstance(out["structured"], dict):
            out["data"] = out["structured"]
            return out

        texts: list[str] = []
        for block in out["content"]:
            if isinstance(block, dict) and block.get("type") == "text":
                text = str(block.get("text", ""))
                if text:
                    texts.append(text)

        if len(texts) == 1:
            maybe_json = texts[0].strip()
            if maybe_json.startswith("{") or maybe_json.startswith("["):
                try:
                    out["data"] = json.loads(maybe_json)
                    return out
                except Exception:
                    pass
            out["data"] = {"text": texts[0]}
            return out

        out["data"] = {"texts": texts}
        return out


# Backward-compatible alias for the previous prototype name.
FlowcodeMCPHTTPClient = TcadMCPHTTPClient
