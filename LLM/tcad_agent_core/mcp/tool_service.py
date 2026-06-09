from __future__ import annotations

"""MCP 工具实现与内部工具总线。

设计目标：
1. 对外：供 stdio MCP server 注册并暴露工具。
2. 对内：供主 Agent 直接按 MCP 工具名调用（不依赖外部进程）。

这样可以保证“同一套工具定义、同一条执行路径”，
避免出现外部 MCP 与内部调用行为不一致的问题。
"""

from pathlib import Path
import inspect
import os
from typing import TYPE_CHECKING, Any, Callable

from src.core import DebugTracer, preview_text
from src.shared_contracts import build_tool_registry

if TYPE_CHECKING:
    from src.agent_system import TCADAgentSystem


ToolFunc = Callable[..., dict[str, Any]]


class MCPToolService:
    """MCP 工具服务封装。

    作用：
    - 将工具名映射到 `TCADAgentSystem` 能力
    - 统一记录 call/done/error 调试事件
    - 支持外部 MCP server 调用与内部 Agent 调用复用
    """

    TOOL_METADATA: dict[str, dict[str, Any]] = {
        "create_session": {"category": "session", "summary": "创建/重置会话并解析需求"},
        "show_state": {"category": "session", "summary": "查看当前状态快照"},
        "run_bash": {"category": "utility", "summary": "执行 Bash 命令"},
        "generate_sde_code": {"category": "sde", "summary": "生成 SDE deck"},
        "check_sde_syntax": {"category": "sde", "summary": "SDE 语法检查"},
        "run_sde": {"category": "sde", "summary": "执行 SDE 构建结构"},
        "run_svisual_sde_export": {"category": "sde", "summary": "导出结构 PNG"},
        "inspect_tdr": {"category": "sde", "summary": "检查 TDR 信息"},
        "tdx_convert": {"category": "utility", "summary": "执行 tdx 转换"},
        "tdx_tclcmd": {"category": "utility", "summary": "执行 tdx Tcl 命令"},
        "generate_sdevice_code": {"category": "sdevice", "summary": "生成 SDevice deck"},
        "check_sdevice_syntax": {"category": "sdevice", "summary": "SDevice 预检查"},
        "run_sdevice": {"category": "sdevice", "summary": "执行电学仿真"},
        "run_svisual_export": {"category": "sdevice", "summary": "导出曲线 PNG/文本"},
        "run_svisual_tcl_script": {"category": "utility", "summary": "执行自定义 svisual 脚本"},
        "run_svisual_cutline_export": {"category": "utility", "summary": "cutline 导出"},
        "run_inspect_script": {"category": "utility", "summary": "执行 inspect 脚本"},
        "validate_results": {"category": "validation", "summary": "执行结果验证"},
    }

    def __init__(
        self,
        workspace: Path,
        *,
        agent: "TCADAgentSystem" | None = None,
        tracer: DebugTracer | None = None,
    ) -> None:
        # 延迟导入避免循环依赖：agent_system 会反向引用本模块
        if agent is None:
            from src.agent_system import TCADAgentSystem

            agent = TCADAgentSystem(workspace)

        self.workspace = workspace
        self.agent = agent
        self.tracer = tracer or DebugTracer(workspace)
        self._deny_tools = self._load_deny_tools()

    def _call(self, name: str, payload: dict[str, Any]) -> None:
        self.tracer.event("MCPTool", "call", {"tool": name, **payload})

    def _done(self, name: str, payload: dict[str, Any], session_id: str | None = None) -> None:
        self.tracer.event("MCPTool", "done", {"tool": name, **payload}, session_id=session_id)

    def _error(self, name: str, exc: Exception, payload: dict[str, Any]) -> None:
        self.tracer.event("MCPTool", "error", {"tool": name, "error": str(exc), **payload})

    @staticmethod
    def _preview_payload(payload: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in payload.items():
            if isinstance(v, str):
                out[k] = preview_text(v)
            else:
                out[k] = v
        return out

    def _invoke(
        self,
        *,
        name: str,
        payload: dict[str, Any],
        action: Callable[[], dict[str, Any]],
        done_payload: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        default_session: str | None = "default",
    ) -> dict[str, Any]:
        """统一调用模板，减少每个 tool 的重复样板代码。"""
        call_payload = self._preview_payload(payload)
        self._call(name, call_payload)
        try:
            out = action()
            done = done_payload(out) if done_payload else {"stage": out.get("stage")}
            session_id = out.get("session_id", default_session)
            self._done(name, done, session_id=session_id)
            return out
        except Exception as exc:
            self._error(name, exc, call_payload)
            raise

    def _load_deny_tools(self) -> set[str]:
        """读取禁用工具配置。"""
        raw = os.environ.get("TCAD_MCP_DENY_TOOLS", "")
        if not raw.strip():
            return set()
        denied = {item.strip() for item in raw.split(",") if item.strip()}
        return denied

    @staticmethod
    def _normalize_kwargs(func: ToolFunc, kwargs: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """规范化工具参数：
        1) 兼容 {"parameters": {...}} 这种包装形态；
        2) 过滤目标函数不接受的多余参数。
        """
        normalized = dict(kwargs)
        sig = inspect.signature(func)
        params = sig.parameters
        accepts_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        accepted_keys = {
            n
            for n, p in params.items()
            if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        }

        wrapped = normalized.get("parameters")
        if isinstance(wrapped, dict) and "parameters" not in accepted_keys:
            if len(normalized) == 1:
                normalized = dict(wrapped)
            else:
                merged = dict(normalized)
                merged.pop("parameters", None)
                for k, v in wrapped.items():
                    merged.setdefault(k, v)
                normalized = merged

        ignored: list[str] = []
        if not accepts_varkw:
            filtered: dict[str, Any] = {}
            for k, v in normalized.items():
                if k in accepted_keys:
                    filtered[k] = v
                else:
                    ignored.append(k)
            normalized = filtered
        return normalized, ignored

    def _dispatch(self) -> dict[str, ToolFunc]:
        """工具名 -> 实现函数映射。

        注意：这里是“单一事实来源”。
        对外 MCP 注册与主 Agent 内部调用都共享这张表。
        """
        return {
            "create_session": self.agent.create_session,
            "show_state": self.agent.show_state,
            "describe_tools": self.describe_tools_api,
            "run_bash": self.agent.run_bash,
            "generate_sde_code": self.agent.generate_sde,
            "check_sde_syntax": self.agent.check_sde,
            "run_sde": self.agent.run_sde,
            "run_svisual_sde_export": self.agent.run_svisual_sde,
            "inspect_tdr": self.agent.inspect_tdr,
            "tdx_convert": self.agent.tdx_convert,
            "tdx_tclcmd": self.agent.tdx_tclcmd,
            "generate_sdevice_code": self.agent.generate_sdevice,
            "check_sdevice_syntax": self.agent.check_sdevice,
            "run_sdevice": self.agent.run_sdevice,
            "run_svisual_export": self.agent.run_svisual,
            "run_svisual_tcl_script": self.agent.run_svisual_tcl_script,
            "run_svisual_cutline_export": self.agent.run_svisual_cutline_export,
            "run_inspect_script": self.agent.run_inspect_script,
            "validate_results": self.agent.validate,
        }

    def list_tool_names(self) -> list[str]:
        """返回当前工具列表（排序后）。"""
        return sorted(self._dispatch().keys())

    def describe_tools(self) -> dict[str, Any]:
        dispatch = self._dispatch()
        registry = build_tool_registry(
            metadata=self.TOOL_METADATA,
            dispatch=dispatch,
            denied_tools=self._deny_tools,
        )
        names = sorted(dispatch.keys())
        denied = sorted(n for n in names if n in self._deny_tools)
        return {
            "ok": True,
            "registry_version": registry.version,
            "tool_count": len(names),
            "denied_tools": denied,
            "tools": [item.to_dict() for item in registry.tools],
        }

    def call_tool(self, name: str, **kwargs: Any) -> dict[str, Any]:
        """通用调用入口：按工具名执行。

        这是主 Agent 内部“工具总线”的核心接口。
        """
        dispatch = self._dispatch()
        if name not in dispatch:
            raise ValueError(f"Unsupported MCP tool: {name}")
        if name in self._deny_tools:
            raise PermissionError(f"Tool disabled by TCAD_MCP_DENY_TOOLS: {name}")
        func = dispatch[name]
        normalized_kwargs, ignored = self._normalize_kwargs(func, kwargs)
        payload = dict(kwargs)
        if ignored:
            payload["ignored_args"] = ignored
        return self._invoke(name=name, payload=payload, action=lambda: func(**normalized_kwargs))

    def create_session(self, requirement: str) -> dict[str, Any]:
        return self.call_tool("create_session", requirement=requirement)

    def show_state(self) -> dict[str, Any]:
        return self.call_tool("show_state")

    def describe_tools_api(self) -> dict[str, Any]:
        return self.describe_tools()

    def run_bash(self, command: str, cwd: str = "", timeout_s: int = 30) -> dict[str, Any]:
        return self.call_tool("run_bash", command=command, cwd=cwd, timeout_s=timeout_s)

    def generate_sde_code(self) -> dict[str, Any]:
        return self.call_tool("generate_sde_code")

    def check_sde_syntax(self) -> dict[str, Any]:
        return self.call_tool("check_sde_syntax")

    def run_sde(self) -> dict[str, Any]:
        return self.call_tool("run_sde")

    def run_svisual_sde_export(self, source_file: str = "", mode: str = "tdr") -> dict[str, Any]:
        return self.call_tool("run_svisual_sde_export", source_file=source_file, mode=mode)

    def inspect_tdr(self, tdr_filename: str = "sde_result_msh.tdr") -> dict[str, Any]:
        return self.call_tool("inspect_tdr", tdr_filename=tdr_filename)

    def tdx_convert(self, command: str, source_file: str, dest_file: str = "", options: list[str] | None = None) -> dict[str, Any]:
        return self.call_tool("tdx_convert", command=command, source_file=source_file, dest_file=dest_file, options=options)

    def tdx_tclcmd(self, tcl_command: str) -> dict[str, Any]:
        return self.call_tool("tdx_tclcmd", tcl_command=tcl_command)

    def generate_sdevice_code(self) -> dict[str, Any]:
        return self.call_tool("generate_sdevice_code")

    def check_sdevice_syntax(self) -> dict[str, Any]:
        return self.call_tool("check_sdevice_syntax")

    def run_sdevice(self) -> dict[str, Any]:
        return self.call_tool("run_sdevice")

    def run_svisual_export(self, source_file: str = "", mode: str = "plt") -> dict[str, Any]:
        return self.call_tool("run_svisual_export", source_file=source_file, mode=mode)

    def run_svisual_tcl_script(
        self,
        script_content: str = "",
        script_file: str = "",
        expected_outputs: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.call_tool(
            "run_svisual_tcl_script",
            script_content=script_content,
            script_file=script_file,
            expected_outputs=expected_outputs,
        )

    def run_svisual_cutline_export(
        self,
        source_file: str,
        axis: str = "x",
        at: float = 0.0,
        variables: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.call_tool(
            "run_svisual_cutline_export",
            source_file=source_file,
            axis=axis,
            at=at,
            variables=variables,
        )

    def run_inspect_script(
        self,
        script_content: str = "",
        script_file: str = "",
        input_files: list[str] | None = None,
        expected_outputs: list[str] | None = None,
        batch: bool = True,
    ) -> dict[str, Any]:
        return self.call_tool(
            "run_inspect_script",
            script_content=script_content,
            script_file=script_file,
            input_files=input_files,
            expected_outputs=expected_outputs,
            batch=batch,
        )

    def validate_results(self) -> dict[str, Any]:
        return self.call_tool("validate_results")


def register_tools(mcp, service: MCPToolService) -> None:
    """向 FastMCP 注册全部工具。"""

    @mcp.tool(name="create_session", description="Parse requirement and reset default session")
    def create_session(requirement: str) -> dict:
        return service.create_session(requirement)

    @mcp.tool(name="show_state", description="Show current session state")
    def show_state() -> dict:
        return service.show_state()

    @mcp.tool(name="describe_tools", description="Describe TCAD MCP tools and enabled/disabled status")
    def describe_tools() -> dict:
        return service.describe_tools_api()

    @mcp.tool(name="run_bash", description="Run bash command (ls/cat/head etc.)")
    def run_bash(command: str, cwd: str = "", timeout_s: int = 30) -> dict:
        return service.run_bash(command=command, cwd=cwd, timeout_s=timeout_s)

    @mcp.tool(name="generate_sde_code", description="Generate SDE deck by LLM")
    def generate_sde_code() -> dict:
        return service.generate_sde_code()

    @mcp.tool(name="check_sde_syntax", description="Run sde -S syntax check")
    def check_sde_syntax() -> dict:
        return service.check_sde_syntax()

    @mcp.tool(name="run_sde", description="Run SDE for mesh generation")
    def run_sde() -> dict:
        return service.run_sde()

    @mcp.tool(name="run_svisual_sde_export", description="Run svisual export from SDE mesh/tdr")
    def run_svisual_sde_export(source_file: str = "", mode: str = "tdr") -> dict:
        return service.run_svisual_sde_export(source_file=source_file, mode=mode)

    @mcp.tool(name="inspect_tdr", description="Inspect TDR using tdx -info")
    def inspect_tdr(tdr_filename: str = "sde_result_msh.tdr") -> dict:
        return service.inspect_tdr(tdr_filename=tdr_filename)

    @mcp.tool(name="tdx_convert", description="Run tdx conversion command")
    def tdx_convert(command: str, source_file: str, dest_file: str = "", options: list[str] | None = None) -> dict:
        return service.tdx_convert(command=command, source_file=source_file, dest_file=dest_file, options=options)

    @mcp.tool(name="tdx_tclcmd", description="Run tdx -tclcmd with one Tcl command")
    def tdx_tclcmd(tcl_command: str) -> dict:
        return service.tdx_tclcmd(tcl_command=tcl_command)

    @mcp.tool(name="generate_sdevice_code", description="Generate SDevice deck by LLM")
    def generate_sdevice_code() -> dict:
        return service.generate_sdevice_code()

    @mcp.tool(name="check_sdevice_syntax", description="Run sdevice -P pre-check")
    def check_sdevice_syntax() -> dict:
        return service.check_sdevice_syntax()

    @mcp.tool(name="run_sdevice", description="Run real SDevice simulation")
    def run_sdevice() -> dict:
        return service.run_sdevice()

    @mcp.tool(name="run_svisual_export", description="Run svisual export curve from SDevice plt")
    def run_svisual_export(source_file: str = "", mode: str = "plt") -> dict:
        return service.run_svisual_export(source_file=source_file, mode=mode)

    @mcp.tool(name="run_svisual_tcl_script", description="Run custom svisual Tcl script in batchx mode")
    def run_svisual_tcl_script(
        script_content: str = "",
        script_file: str = "",
        expected_outputs: list[str] | None = None,
    ) -> dict:
        return service.run_svisual_tcl_script(
            script_content=script_content,
            script_file=script_file,
            expected_outputs=expected_outputs,
        )

    @mcp.tool(name="run_svisual_cutline_export", description="Create cutline from TDR and export CSV/PNG")
    def run_svisual_cutline_export(
        source_file: str,
        axis: str = "x",
        at: float = 0.0,
        variables: list[str] | None = None,
    ) -> dict:
        return service.run_svisual_cutline_export(source_file=source_file, axis=axis, at=at, variables=variables)

    @mcp.tool(name="run_inspect_script", description="Run inspect script in batch mode for curve extraction")
    def run_inspect_script(
        script_content: str = "",
        script_file: str = "",
        input_files: list[str] | None = None,
        expected_outputs: list[str] | None = None,
        batch: bool = True,
    ) -> dict:
        return service.run_inspect_script(
            script_content=script_content,
            script_file=script_file,
            input_files=input_files,
            expected_outputs=expected_outputs,
            batch=batch,
        )

    @mcp.tool(name="validate_results", description="Validate structure/curve/metrics")
    def validate_results() -> dict:
        return service.validate_results()
