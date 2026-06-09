from __future__ import annotations

"""TCAD MCP HTTP server entrypoint.

Transport: streamable-http
Contract: start_tcad_server + call(api_tcad_*)
"""

import os
import sys
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

THIS_DIR = Path(__file__).resolve().parent
ROOT = THIS_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from service import TcadGatewayMCPService


HELP_RESOURCE_URI = "tcad://help"
HELP_RESOURCE_TEXT = """TCAD MCP quick guide

Tools:
- start_tcad_server(setup_workdir)
- call(method, params, timeout_ms, return_mode, instance_id?)
- call_async_start(method, params, timeout_ms, return_mode, instance_id?)
- call_async_status(job_id, include_response?)
- call_async_wait(job_id, wait_timeout_ms?, include_response?)
- list_instances()
- stop_server(instance_id?)
- cleanup_stale()

Typical flow:
1) start_tcad_server(setup_workdir)
2) call(api_tcad_handshake)
3) call(api_tcad_create_session, {requirement: ...})
4) call(api_tcad_agent_decide_and_execute, {instruction: ...})

Policy env (optional):
- TCAD_GATEWAY_DENY_METHODS=api_tcad_run_bash,api_tcad_run_sdevice
- TCAD_GATEWAY_DEFAULT_TIMEOUT_MS=120000
- TCAD_GATEWAY_MAX_TIMEOUT_MS=600000
- TCAD_GATEWAY_METHOD_DEFAULT_TIMEOUT_MS=api_tcad_run_sdevice=0,api_tcad_agent_decide_and_execute=0
- TCAD_GATEWAY_METHOD_MAX_TIMEOUT_MS=api_tcad_run_bash=30000
- TCAD_GATEWAY_ASYNC_METHODS=*   (or comma-separated api_tcad_* list)
"""


def build_mcp_server(service: TcadGatewayMCPService) -> FastMCP:
    host = os.getenv("TCAD_MCP_HTTP_HOST", "127.0.0.1")
    port = int(os.getenv("TCAD_MCP_HTTP_PORT", "8766"))

    server = FastMCP(
        name="tcad-agent",
        instructions=(
            "Use start_tcad_server(setup_workdir) first, then call(method=api_tcad_*, params=...). "
            "Prefer api_tcad_list_methods/api_tcad_describe_method for method discovery."
        ),
        host=host,
        port=port,
        streamable_http_path="/mcp",
        log_level="INFO",
    )

    @server.tool(
        name="start_tcad_server",
        description="Create one managed TCAD runtime instance bound to setup_workdir.",
    )
    def start_tcad_server(setup_workdir: str) -> dict[str, Any]:
        return service.start_tcad_server(setup_workdir=setup_workdir)

    @server.tool(
        name="call",
        description="Call one api_tcad_* method with optional instance routing.",
    )
    def call(
        method: str,
        params: Any = None,
        timeout_ms: Optional[int] = None,
        return_mode: Optional[str] = None,
        instance_id: Optional[str] = None,
    ) -> dict[str, Any]:
        return service.call(
            method=method,
            params=params,
            timeout_ms=timeout_ms,
            return_mode=return_mode,
            instance_id=instance_id,
        )

    @server.tool(
        name="call_async_start",
        description="Start one async call(api_tcad_*) job and return job_id immediately.",
    )
    def call_async_start(
        method: str,
        params: Any = None,
        timeout_ms: Optional[int] = None,
        return_mode: Optional[str] = None,
        instance_id: Optional[str] = None,
    ) -> dict[str, Any]:
        return service.call_async_start(
            method=method,
            params=params,
            timeout_ms=timeout_ms,
            return_mode=return_mode,
            instance_id=instance_id,
        )

    @server.tool(name="call_async_status", description="Get async job status by job_id.")
    def call_async_status(job_id: str, include_response: bool = True) -> dict[str, Any]:
        return service.call_async_status(job_id=job_id, include_response=include_response)

    @server.tool(name="call_async_wait", description="Wait for async job completion.")
    def call_async_wait(
        job_id: str,
        wait_timeout_ms: Optional[int] = None,
        include_response: bool = True,
    ) -> dict[str, Any]:
        return service.call_async_wait(
            job_id=job_id,
            wait_timeout_ms=wait_timeout_ms,
            include_response=include_response,
        )

    @server.tool(name="list_instances", description="List managed runtime instances.")
    def list_instances() -> dict[str, Any]:
        return service.list_instances()

    @server.tool(name="stop_server", description="Stop one managed runtime instance.")
    def stop_server(instance_id: Optional[str] = None) -> dict[str, Any]:
        return service.stop_server(instance_id=instance_id)

    @server.tool(name="cleanup_stale", description="Cleanup completed async job records.")
    def cleanup_stale() -> dict[str, Any]:
        return service.cleanup_stale()

    @server.resource(
        HELP_RESOURCE_URI,
        name="TCAD MCP Help",
        description="Usage summary for TCAD MCP gateway contract.",
        mime_type="text/plain",
    )
    def help_resource() -> str:
        return HELP_RESOURCE_TEXT

    return server


def main() -> int:
    service = TcadGatewayMCPService.from_env()
    server = build_mcp_server(service)
    try:
        server.run(transport="streamable-http")
    finally:
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
