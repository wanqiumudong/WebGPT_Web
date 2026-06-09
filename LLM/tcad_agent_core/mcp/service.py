from __future__ import annotations

"""TCAD MCP gateway service.

提供 managed-instance + call(api_tcad_*) 合约，便于外部系统按统一接口
进行会话管理、异步调用和流水线编排。
"""

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timezone
import inspect
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable
import uuid

from src.agent_system import TCADAgentSystem
from src.core import DebugTracer, preview_text
from src.shared_contracts import build_error_envelope, build_job_record


@dataclass
class ManagedInstance:
    instance_id: str
    setup_workdir: Path
    agent: TCADAgentSystem
    created_at: float
    call_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


@dataclass
class AsyncJob:
    job_id: str
    instance_id: str | None
    method: str
    created_at: float
    future: Future[dict[str, Any]]


class InstanceBusyError(RuntimeError):
    """Raised when a managed instance is still processing a previous call."""


VALID_RETURN_MODES = frozenset({"typed", "text", "raw", "both"})


class TcadGatewayMCPService:
    """Managed gateway service for TCAD."""

    def __init__(
        self,
        workspace: Path,
        *,
        max_async_workers: int = 2,
        agent_factory: Callable[[Path, Path], TCADAgentSystem] | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.tracer = DebugTracer(self.workspace)
        self._lock = threading.RLock()
        self._instances: dict[str, ManagedInstance] = {}
        self._active_instance_id: str | None = None
        self._executor = ThreadPoolExecutor(max_workers=max(1, max_async_workers), thread_name_prefix="tcad_gateway_async")
        self._jobs: dict[str, AsyncJob] = {}
        self._agent_factory = agent_factory or (lambda ws, rr: TCADAgentSystem(ws, runtime_root=rr))
        self._method_handlers: dict[str, Callable[..., Any]] = {
            "api_tcad_ping": self._api_ping,
            "api_tcad_handshake": self._api_handshake,
            "api_tcad_list_methods": self._api_list_methods,
            "api_tcad_describe_method": self._api_describe_method,
            "api_tcad_create_session": self._api_create_session,
            "api_tcad_show_state": self._api_show_state,
            "api_tcad_show_capabilities": self._api_show_capabilities,
            "api_tcad_describe_tools": self._api_describe_tools,
            "api_tcad_register_asset": self._api_register_asset,
            "api_tcad_list_assets": self._api_list_assets,
            "api_tcad_delete_asset": self._api_delete_asset,
            "api_tcad_decide_next_operation": self._api_decide_next_operation,
            "api_tcad_run_operation": self._api_run_operation,
            "api_tcad_agent_decide_and_execute": self._api_agent_decide_and_execute,
            "api_tcad_generate_sde": self._api_generate_sde,
            "api_tcad_check_sde": self._api_check_sde,
            "api_tcad_run_sde": self._api_run_sde,
            "api_tcad_run_svisual_sde": self._api_run_svisual_sde,
            "api_tcad_inspect_tdr": self._api_inspect_tdr,
            "api_tcad_generate_sdevice": self._api_generate_sdevice,
            "api_tcad_check_sdevice": self._api_check_sdevice,
            "api_tcad_run_sdevice": self._api_run_sdevice,
            "api_tcad_run_svisual": self._api_run_svisual,
            "api_tcad_validate_results": self._api_validate_results,
            "api_tcad_run_bash": self._api_run_bash,
            "api_tcad_tdx_convert": self._api_tdx_convert,
            "api_tcad_tdx_tclcmd": self._api_tdx_tclcmd,
            "api_tcad_run_svisual_tcl_script": self._api_run_svisual_tcl_script,
            "api_tcad_run_svisual_cutline_export": self._api_run_svisual_cutline_export,
            "api_tcad_run_inspect_script": self._api_run_inspect_script,
        }
        self._method_docs: dict[str, str] = {
            "api_tcad_ping": "Liveness check for managed instance.",
            "api_tcad_handshake": "Return protocol/tool metadata.",
            "api_tcad_list_methods": "List supported api_tcad_* methods.",
            "api_tcad_describe_method": "Describe one api_tcad_* method by name.",
            "api_tcad_create_session": "Create/reset one TCAD session from requirement.",
            "api_tcad_show_state": "Show current session state snapshot.",
            "api_tcad_show_capabilities": "Show backend capabilities.",
            "api_tcad_describe_tools": "Show MCP tool catalog with enabled/disabled status.",
            "api_tcad_register_asset": "Register one uploaded session asset into the active runtime.",
            "api_tcad_list_assets": "List uploaded session assets bound to the active runtime.",
            "api_tcad_delete_asset": "Delete one uploaded session asset by file name.",
            "api_tcad_decide_next_operation": "LLM-driven next-step planner.",
            "api_tcad_run_operation": "Execute one canonical operation by op name.",
            "api_tcad_agent_decide_and_execute": "Run iterative decision + execution loop for one instruction.",
            "api_tcad_generate_sde": "Generate SDE deck.",
            "api_tcad_check_sde": "Run SDE syntax check.",
            "api_tcad_run_sde": "Run SDE mesh generation.",
            "api_tcad_run_svisual_sde": "Export SDE/TDR structure image.",
            "api_tcad_inspect_tdr": "Inspect TDR with tdx -info.",
            "api_tcad_generate_sdevice": "Generate SDevice deck.",
            "api_tcad_check_sdevice": "Run SDevice pre-check.",
            "api_tcad_run_sdevice": "Run SDevice simulation.",
            "api_tcad_run_svisual": "Export SDevice curve outputs.",
            "api_tcad_validate_results": "Validate structure/curve/metrics.",
            "api_tcad_run_bash": "Run bash command in session context.",
            "api_tcad_tdx_convert": "Run tdx conversion command.",
            "api_tcad_tdx_tclcmd": "Run one tdx Tcl command.",
            "api_tcad_run_svisual_tcl_script": "Run custom svisual Tcl script.",
            "api_tcad_run_svisual_cutline_export": "Run cutline export from TDR.",
            "api_tcad_run_inspect_script": "Run inspect script in batch mode.",
        }
        self._deny_methods = self._env_method_set("TCAD_GATEWAY_DENY_METHODS")
        self._default_timeout_ms = self._env_int("TCAD_GATEWAY_DEFAULT_TIMEOUT_MS", default=0)
        self._max_timeout_ms = self._env_int("TCAD_GATEWAY_MAX_TIMEOUT_MS", default=0)
        self._method_default_timeout_ms = self._env_method_timeout_map("TCAD_GATEWAY_METHOD_DEFAULT_TIMEOUT_MS")
        self._method_max_timeout_ms = self._env_method_timeout_map("TCAD_GATEWAY_METHOD_MAX_TIMEOUT_MS")
        self._async_allow_all, self._async_enabled_methods = self._env_async_methods("TCAD_GATEWAY_ASYNC_METHODS")

    @staticmethod
    def _env_int(name: str, *, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            value = int(raw.strip())
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
        if value < 0:
            raise ValueError(f"{name} must be >= 0")
        return value

    @staticmethod
    def _env_method_set(name: str) -> set[str]:
        raw = os.environ.get(name, "")
        out = {item.strip() for item in raw.split(",") if item.strip()}
        for method in out:
            if not method.startswith("api_tcad_"):
                raise ValueError(f"{name} method must start with api_tcad_: {method!r}")
        return out

    @staticmethod
    def _parse_timeout_value(raw: str, *, env_name: str, method: str) -> int:
        text = raw.strip().lower()
        if text in {"inf", "infinite", "unbounded"}:
            return 0
        try:
            val = int(text)
        except ValueError as exc:
            raise ValueError(f"{env_name} timeout must be integer or inf for {method!r}") from exc
        if val < 0:
            raise ValueError(f"{env_name} timeout must be >= 0 for {method!r}")
        return val

    def _env_method_timeout_map(self, name: str) -> dict[str, int]:
        raw = os.environ.get(name, "")
        if raw.strip() == "":
            return {}
        out: dict[str, int] = {}
        for entry in raw.split(","):
            item = entry.strip()
            if item == "":
                continue
            if "=" not in item:
                raise ValueError(f"{name} entry must be method=timeout format: {item!r}")
            method, timeout_str = item.split("=", 1)
            method = method.strip()
            if not method.startswith("api_tcad_"):
                raise ValueError(f"{name} method must start with api_tcad_: {method!r}")
            out[method] = self._parse_timeout_value(timeout_str, env_name=name, method=method)
        return out

    @staticmethod
    def _env_async_methods(name: str) -> tuple[bool, set[str]]:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return True, set()
        methods: set[str] = set()
        wildcard = False
        for entry in raw.split(","):
            item = entry.strip()
            if item == "":
                continue
            if item == "*":
                wildcard = True
                continue
            if not item.startswith("api_tcad_"):
                raise ValueError(f"{name} method must start with api_tcad_ or be '*': {item!r}")
            methods.add(item)
        if wildcard and methods:
            raise ValueError(f"{name} cannot mix '*' with explicit method names")
        if wildcard:
            return True, set()
        return False, methods

    @classmethod
    def from_env(cls) -> "TcadGatewayMCPService":
        default_workspace = Path(__file__).resolve().parent.parent
        workspace = Path(os.getenv("TCAD_GATEWAY_WORKSPACE", str(default_workspace))).resolve()
        workers = int(os.getenv("TCAD_GATEWAY_ASYNC_WORKERS", "2"))
        return cls(workspace=workspace, max_async_workers=workers)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def start_tcad_server(self, setup_workdir: str) -> dict[str, Any]:
        setup_path = Path(setup_workdir).expanduser().resolve()
        setup_path.mkdir(parents=True, exist_ok=True)
        instance_id = f"tcad-{uuid.uuid4().hex[:8]}"

        agent = self._agent_factory(self.workspace, setup_path)
        inst = ManagedInstance(
            instance_id=instance_id,
            setup_workdir=setup_path,
            agent=agent,
            created_at=time.time(),
        )

        with self._lock:
            self._instances[instance_id] = inst
            self._active_instance_id = instance_id

        self.tracer.event(
            "TCADGatewayMCP",
            "start_instance",
            {
                "instance_id": instance_id,
                "setup_workdir": str(setup_path),
                "workspace": str(self.workspace),
            },
            session_id="default",
        )

        return {
            "ok": True,
            "instance_id": instance_id,
            "setup_workdir": str(setup_path),
            "active": True,
            "created_at": self._iso_utc(inst.created_at),
        }

    def stop_server(self, instance_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            resolved = self._resolve_instance_id(instance_id)
            if resolved is None or resolved not in self._instances:
                return {"ok": False, "error": "No managed instance found."}
            self._instances.pop(resolved, None)
            if self._active_instance_id == resolved:
                self._active_instance_id = next(iter(self._instances.keys()), None)

        self.tracer.event("TCADGatewayMCP", "stop_instance", {"instance_id": resolved}, session_id="default")
        return {"ok": True, "stopped_instance_id": resolved, "active_instance_id": self._active_instance_id}

    def list_instances(self) -> dict[str, Any]:
        with self._lock:
            active = self._active_instance_id
            items = [
                {
                    "instance_id": inst.instance_id,
                    "setup_workdir": str(inst.setup_workdir),
                    "created_at": self._iso_utc(inst.created_at),
                    "active": inst.instance_id == active,
                }
                for inst in self._instances.values()
            ]
        return {"ok": True, "active_instance_id": active, "instances": items}

    def cleanup_stale(self) -> dict[str, Any]:
        cleaned = 0
        with self._lock:
            finished = [job_id for job_id, job in self._jobs.items() if job.future.done()]
            for job_id in finished:
                self._jobs.pop(job_id, None)
                cleaned += 1
        return {"ok": True, "cleaned": cleaned}

    def get_instance(self, instance_id: str | None = None) -> ManagedInstance:
        return self._require_instance(instance_id)

    def call(
        self,
        *,
        method: str,
        params: Any = None,
        timeout_ms: int | None = None,
        return_mode: str | None = None,
        instance_id: str | None = None,
    ) -> dict[str, Any]:
        started = time.time()
        try:
            inst = self._require_instance(instance_id)
            if not isinstance(method, str) or not method.startswith("api_tcad_"):
                return {
                    "ok": False,
                    "method": method,
                    "instance_id": inst.instance_id,
                    "error": build_error_envelope(code="TCAD-1000", message="method must start with api_tcad_"),
                }
            if method in self._deny_methods:
                return {
                    "ok": False,
                    "method": method,
                    "instance_id": inst.instance_id,
                    "error": build_error_envelope(
                        code="TCAD-1005",
                        message=f"Method denied by policy: {method}",
                    ),
                }
            if return_mode is not None and return_mode not in VALID_RETURN_MODES:
                return {
                    "ok": False,
                    "method": method,
                    "instance_id": inst.instance_id,
                    "error": build_error_envelope(
                        code="TCAD-1006",
                        message=f"Unsupported return_mode: {return_mode}",
                    ),
                }

            handler = self._method_handlers.get(method)
            if handler is None:
                return {
                    "ok": False,
                    "method": method,
                    "instance_id": inst.instance_id,
                    "error": build_error_envelope(code="TCAD-1001", message=f"Unknown method: {method}"),
                }

            effective_timeout_ms, timeout_err = self._resolve_timeout_ms(method, timeout_ms)
            if timeout_err is not None:
                return {"ok": False, "method": method, "instance_id": inst.instance_id, "error": timeout_err}

            args, kwargs = self._normalize_params(params)
            cancel_event = threading.Event()
            call_kwargs = dict(kwargs)
            if self._handler_accepts_cancel_event(handler):
                call_kwargs["cancel_event"] = cancel_event
            self.tracer.event(
                "TCADGatewayMCP",
                "call",
                {
                    "instance_id": inst.instance_id,
                    "method": method,
                    "params_preview": preview_text(str(params), 500),
                    "timeout_ms": timeout_ms,
                    "effective_timeout_ms": effective_timeout_ms,
                    "return_mode": return_mode,
                },
                session_id="default",
            )
            ok_in_time, data = self._invoke_with_timeout(
                inst,
                lambda: handler(inst, *args, **call_kwargs),
                timeout_ms=effective_timeout_ms or 0,
                cancel_event=cancel_event,
            )
            if not ok_in_time:
                elapsed_ms = int((time.time() - started) * 1000)
                return {
                    "ok": False,
                    "method": method,
                    "instance_id": inst.instance_id,
                    "elapsed_ms": elapsed_ms,
                    "error": build_error_envelope(
                        code="TCAD-1004",
                        message=f"Call timeout for {method}",
                        details={"effective_timeout_ms": effective_timeout_ms},
                        retryable=True,
                    ),
                }
            elapsed_ms = int((time.time() - started) * 1000)
            return {
                "ok": True,
                "method": method,
                "instance_id": inst.instance_id,
                "elapsed_ms": elapsed_ms,
                "return_mode": return_mode or "typed",
                "data": data,
            }
        except InstanceBusyError as exc:
            elapsed_ms = int((time.time() - started) * 1000)
            return {
                "ok": False,
                "method": method,
                "instance_id": inst.instance_id if "inst" in locals() else instance_id,
                "elapsed_ms": elapsed_ms,
                "error": build_error_envelope(code="TCAD-2002", message=str(exc), retryable=True),
            }
        except Exception as exc:  # pragma: no cover
            elapsed_ms = int((time.time() - started) * 1000)
            return {
                "ok": False,
                "method": method,
                "instance_id": instance_id,
                "elapsed_ms": elapsed_ms,
                "error": build_error_envelope(code="TCAD-2001", message=str(exc)),
            }

    def call_async_start(
        self,
        *,
        method: str,
        params: Any = None,
        timeout_ms: int | None = None,
        return_mode: str | None = None,
        instance_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            resolved_instance_id = self._resolve_instance_id(instance_id)
        if not isinstance(method, str) or not method.startswith("api_tcad_"):
            return {
                "ok": False,
                "error": build_error_envelope(code="TCAD-1000", message="method must start with api_tcad_"),
            }
        if method not in self._method_handlers:
            return {
                "ok": False,
                "error": build_error_envelope(code="TCAD-1001", message=f"Unknown method: {method}"),
            }
        if method in self._deny_methods:
            return {
                "ok": False,
                "error": build_error_envelope(
                    code="TCAD-1005",
                    message=f"Method denied by policy: {method}",
                ),
            }
        if not self._is_async_allowed(method):
            return {
                "ok": False,
                "error": build_error_envelope(
                    code="TCAD-1007",
                    message=f"Method not enabled for async call: {method}",
                ),
            }
        job_id = f"job-{uuid.uuid4().hex[:10]}"
        future = self._executor.submit(
            self.call,
            method=method,
            params=params,
            timeout_ms=timeout_ms,
            return_mode=return_mode,
            instance_id=resolved_instance_id,
        )
        job = AsyncJob(
            job_id=job_id,
            instance_id=resolved_instance_id,
            method=method,
            created_at=time.time(),
            future=future,
        )
        with self._lock:
            self._jobs[job_id] = job
        return {
            "ok": True,
            "job_id": job_id,
            "instance_id": resolved_instance_id,
            "method": method,
            "created_at": self._iso_utc(job.created_at),
            "job_record": build_job_record(
                job_id=job.job_id,
                instance_id=job.instance_id,
                method=job.method,
                created_at=job.created_at,
                done=False,
                running=job.future.running(),
            ),
        }

    def call_async_status(self, *, job_id: str, include_response: bool = True) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            return {
                "ok": False,
                "error": build_error_envelope(code="TCAD-3001", message=f"Unknown job_id: {job_id}"),
            }
        done = job.future.done()
        response = job.future.result() if done and include_response else None
        out: dict[str, Any] = {
            "ok": True,
            "job_id": job_id,
            "method": job.method,
            "instance_id": job.instance_id,
            "done": done,
            "created_at": self._iso_utc(job.created_at),
            "job_record": build_job_record(
                job_id=job.job_id,
                instance_id=job.instance_id,
                method=job.method,
                created_at=job.created_at,
                done=done,
                running=job.future.running(),
                response=response if isinstance(response, dict) else None,
            ),
        }
        if response is not None:
            out["response"] = response
        return out

    def call_async_wait(
        self,
        *,
        job_id: str,
        wait_timeout_ms: int | None = None,
        include_response: bool = True,
    ) -> dict[str, Any]:
        job = self._jobs.get(job_id)
        if job is None:
            return {
                "ok": False,
                "error": build_error_envelope(code="TCAD-3001", message=f"Unknown job_id: {job_id}"),
            }
        timeout_s = None if wait_timeout_ms is None else max(0, wait_timeout_ms) / 1000.0
        try:
            response = job.future.result(timeout=timeout_s)
            out: dict[str, Any] = {
                "ok": True,
                "job_id": job_id,
                "method": job.method,
                "instance_id": job.instance_id,
                "done": True,
                "job_record": build_job_record(
                    job_id=job.job_id,
                    instance_id=job.instance_id,
                    method=job.method,
                    created_at=job.created_at,
                    done=True,
                    running=False,
                    response=response if isinstance(response, dict) else None,
                ),
            }
            if include_response:
                out["response"] = response
            return out
        except FutureTimeoutError:
            return {
                "ok": True,
                "job_id": job_id,
                "method": job.method,
                "instance_id": job.instance_id,
                "done": False,
                "wait_timeout_ms": wait_timeout_ms,
                "job_record": build_job_record(
                    job_id=job.job_id,
                    instance_id=job.instance_id,
                    method=job.method,
                    created_at=job.created_at,
                    done=False,
                    running=job.future.running(),
                ),
            }

    def _require_instance(self, instance_id: str | None) -> ManagedInstance:
        with self._lock:
            resolved = self._resolve_instance_id(instance_id)
            if resolved is None:
                raise RuntimeError("No active instance. Call start_tcad_server first.")
            inst = self._instances.get(resolved)
            if inst is None:
                raise RuntimeError(f"Managed instance not found: {resolved}")
            return inst

    def _resolve_instance_id(self, instance_id: str | None) -> str | None:
        return instance_id or self._active_instance_id

    def _is_async_allowed(self, method: str) -> bool:
        if self._async_allow_all:
            return True
        return method in self._async_enabled_methods

    def _resolve_timeout_ms(self, method: str, requested_timeout_ms: int | None) -> tuple[int | None, dict[str, Any] | None]:
        if requested_timeout_ms is not None:
            if not isinstance(requested_timeout_ms, int) or requested_timeout_ms < 0:
                return None, build_error_envelope(code="TCAD-1002", message="timeout_ms must be integer >= 0")
            effective = requested_timeout_ms
        else:
            effective = self._method_default_timeout_ms.get(method, self._default_timeout_ms)

        max_limit = self._method_max_timeout_ms.get(method, self._max_timeout_ms)
        if max_limit > 0 and effective > 0 and effective > max_limit:
            return None, {
                **build_error_envelope(
                    code="TCAD-1003",
                    message=f"timeout_ms exceeds max policy for {method}",
                    details={"requested_timeout_ms": effective, "max_timeout_ms": max_limit},
                )
            }
        return effective, None

    @staticmethod
    def _handler_accepts_cancel_event(handler: Callable[..., Any]) -> bool:
        try:
            return "cancel_event" in inspect.signature(handler).parameters
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _invoke_with_timeout(
        inst: ManagedInstance,
        fn: Callable[[], Any],
        *,
        timeout_ms: int,
        cancel_event: threading.Event | None = None,
    ) -> tuple[bool, Any]:
        if not inst.call_lock.acquire(blocking=False):
            raise InstanceBusyError(f"Managed instance busy: {inst.instance_id}")

        if timeout_ms <= 0:
            try:
                return True, fn()
            finally:
                inst.call_lock.release()

        box: dict[str, Any] = {}
        err: dict[str, BaseException] = {}

        def _runner() -> None:
            try:
                box["result"] = fn()
            except BaseException as exc:  # noqa: BLE001
                err["error"] = exc
            finally:
                inst.call_lock.release()

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        t.join(timeout_ms / 1000.0)
        if t.is_alive():
            if cancel_event is not None:
                cancel_event.set()
            return False, None
        if "error" in err:
            raise err["error"]
        return True, box.get("result")

    @staticmethod
    def _normalize_params(params: Any) -> tuple[tuple[Any, ...], dict[str, Any]]:
        if params is None:
            return (), {}
        if isinstance(params, dict):
            return (), params
        if isinstance(params, (list, tuple)):
            return tuple(params), {}
        return (params,), {}

    @staticmethod
    def _iso_utc(ts: float) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    # ---- api_tcad_* handlers ----

    def _api_ping(self, inst: ManagedInstance) -> dict[str, Any]:
        return {
            "pong": True,
            "instance_id": inst.instance_id,
            "runtime_root": str(inst.agent.runtime_root),
            "timestamp": self._iso_utc(time.time()),
        }

    def _api_handshake(self, inst: ManagedInstance) -> dict[str, Any]:
        return {
            "protocol_version": "tcad-gateway-v1",
            "tool_name": "tcad-agent",
            "tool_version": "0.2.0",
            "instance_id": inst.instance_id,
            "gateway_policy": {
                "deny_methods": sorted(self._deny_methods),
                "default_timeout_ms": self._default_timeout_ms,
                "max_timeout_ms": self._max_timeout_ms,
                "async_allow_all": self._async_allow_all,
                "async_enabled_methods": sorted(self._async_enabled_methods),
            },
        }

    def _api_list_methods(self, inst: ManagedInstance) -> dict[str, Any]:
        _ = inst
        return {"methods": sorted(self._method_handlers.keys())}

    def _api_describe_method(self, inst: ManagedInstance, method: str) -> dict[str, Any]:
        _ = inst
        if method not in self._method_handlers:
            raise ValueError(f"Unknown method: {method}")
        return {"name": method, "doc": self._method_docs.get(method, "")}

    def _api_create_session(self, inst: ManagedInstance, requirement: str = "", instruction: str = "") -> dict[str, Any]:
        req = (requirement or instruction).strip()
        if not req:
            raise ValueError("api_tcad_create_session requires `requirement` (or legacy `instruction`).")
        return inst.agent.create_session(req)

    def _api_show_state(self, inst: ManagedInstance) -> dict[str, Any]:
        try:
            return inst.agent.show_state()
        except FileNotFoundError:
            return {
                "session_id": inst.agent.DEFAULT_SESSION,
                "stage": "no_session",
                "artifacts": {},
                "metrics": {},
                "notes": ["No active session yet. Run one instruction or call api_tcad_create_session first."],
            }

    def _api_show_capabilities(self, inst: ManagedInstance) -> dict[str, Any]:
        return inst.agent.show_capabilities()

    def _api_describe_tools(self, inst: ManagedInstance) -> dict[str, Any]:
        return inst.agent.mcp_tools.describe_tools()

    def _api_register_asset(self, inst: ManagedInstance, source_path: str, file_name: str = "", role: str = "auto") -> dict[str, Any]:
        return inst.agent.register_session_asset(source_path=source_path, file_name=file_name, role=role)

    def _api_list_assets(self, inst: ManagedInstance) -> dict[str, Any]:
        return inst.agent.list_session_assets()

    def _api_delete_asset(self, inst: ManagedInstance, file_name: str) -> dict[str, Any]:
        return inst.agent.delete_session_asset(file_name=file_name)

    def _api_decide_next_operation(self, inst: ManagedInstance, instruction: str) -> dict[str, Any]:
        return inst.agent.decide_next_operation(instruction)

    def _api_run_operation(
        self,
        inst: ManagedInstance,
        op: str,
        args: dict[str, Any] | None = None,
        instruction: str = "",
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        if cancel_event is not None and cancel_event.is_set():
            return {"stage": "aborted", "aborted": True, "op": op}
        return inst.agent.run_operation(op, args=args or {}, instruction=instruction)

    def _api_agent_decide_and_execute(
        self,
        inst: ManagedInstance,
        instruction: str,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        should_abort = cancel_event.is_set if cancel_event is not None else None
        return inst.agent.agent_decide_and_execute(instruction, should_abort=should_abort)

    def _api_generate_sde(self, inst: ManagedInstance) -> dict[str, Any]:
        return inst.agent.generate_sde()

    def _api_check_sde(self, inst: ManagedInstance) -> dict[str, Any]:
        return inst.agent.check_sde()

    def _api_run_sde(self, inst: ManagedInstance) -> dict[str, Any]:
        return inst.agent.run_sde()

    def _api_run_svisual_sde(self, inst: ManagedInstance, source_file: str = "", mode: str = "tdr") -> dict[str, Any]:
        return inst.agent.run_svisual_sde(source_file=source_file, mode=mode)

    def _api_inspect_tdr(self, inst: ManagedInstance, tdr_filename: str = "sde_result_msh.tdr") -> dict[str, Any]:
        return inst.agent.inspect_tdr(tdr_filename=tdr_filename)

    def _api_generate_sdevice(self, inst: ManagedInstance) -> dict[str, Any]:
        return inst.agent.generate_sdevice()

    def _api_check_sdevice(self, inst: ManagedInstance) -> dict[str, Any]:
        return inst.agent.check_sdevice()

    def _api_run_sdevice(self, inst: ManagedInstance) -> dict[str, Any]:
        return inst.agent.run_sdevice()

    def _api_run_svisual(self, inst: ManagedInstance, source_file: str = "", mode: str = "plt") -> dict[str, Any]:
        return inst.agent.run_svisual(source_file=source_file, mode=mode)

    def _api_validate_results(self, inst: ManagedInstance) -> dict[str, Any]:
        return inst.agent.validate()

    def _api_run_bash(self, inst: ManagedInstance, command: str, cwd: str = "", timeout_s: int = 30) -> dict[str, Any]:
        return inst.agent.run_bash(command=command, cwd=cwd, timeout_s=timeout_s)

    def _api_tdx_convert(
        self,
        inst: ManagedInstance,
        command: str,
        source_file: str,
        dest_file: str = "",
        options: list[str] | None = None,
    ) -> dict[str, Any]:
        return inst.agent.tdx_convert(command=command, source_file=source_file, dest_file=dest_file, options=options)

    def _api_tdx_tclcmd(self, inst: ManagedInstance, tcl_command: str) -> dict[str, Any]:
        return inst.agent.tdx_tclcmd(tcl_command=tcl_command)

    def _api_run_svisual_tcl_script(
        self,
        inst: ManagedInstance,
        script_content: str = "",
        script_file: str = "",
        expected_outputs: list[str] | None = None,
    ) -> dict[str, Any]:
        return inst.agent.run_svisual_tcl_script(
            script_content=script_content,
            script_file=script_file,
            expected_outputs=expected_outputs,
        )

    def _api_run_svisual_cutline_export(
        self,
        inst: ManagedInstance,
        source_file: str,
        axis: str = "x",
        at: float = 0.0,
        variables: list[str] | None = None,
    ) -> dict[str, Any]:
        return inst.agent.run_svisual_cutline_export(source_file=source_file, axis=axis, at=at, variables=variables)

    def _api_run_inspect_script(
        self,
        inst: ManagedInstance,
        script_content: str = "",
        script_file: str = "",
        input_files: list[str] | None = None,
        expected_outputs: list[str] | None = None,
        batch: bool = True,
    ) -> dict[str, Any]:
        return inst.agent.run_inspect_script(
            script_content=script_content,
            script_file=script_file,
            input_files=input_files,
            expected_outputs=expected_outputs,
            batch=batch,
        )
