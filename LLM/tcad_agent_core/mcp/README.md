# TCAD MCP 服务

本目录同时提供两种 MCP 接入方式：

1. **stdio 工具直连模式**（本地工具全集）
2. **streamable-http 网关模式**（managed instance + `call(api_tcad_*)`）
3. **HTTP client helper**（供外部 Python/UI 作为 MCP HTTP client 复用）

---

## 1) stdio 模式

启动：

```bash
bash /data/yphu/TCAD_Agent/code/mcp/start_mcp.sh
```

主要用途：直接暴露 TCAD 细粒度工具（SDE/SDevice/svisual/tdx 等）。

### 当前工具列表（19 个）

- `create_session(requirement)`
- `show_state()`
- `describe_tools()`
- `run_bash(command, cwd="", timeout_s=30)`
- `generate_sde_code()`
- `check_sde_syntax()`
- `run_sde()`
- `run_svisual_sde_export(source_file="", mode="tdr")`
- `inspect_tdr(tdr_filename="sde_result_msh.tdr")`
- `tdx_convert(command, source_file, dest_file="", options=[])`
- `tdx_tclcmd(tcl_command)`
- `generate_sdevice_code()`
- `check_sdevice_syntax()`
- `run_sdevice()`
- `run_svisual_export(source_file="", mode="plt")`
- `run_svisual_tcl_script(script_content="", script_file="", expected_outputs=[])`
- `run_svisual_cutline_export(source_file, axis="x", at=0.0, variables=[])`
- `run_inspect_script(script_content="", script_file="", input_files=[], expected_outputs=[], batch=True)`
- `validate_results()`

说明：
- `run_svisual_sde_export`：结构图导出（`.tdr`/`.msh.tdr`）
- `run_svisual_export`：电学曲线导出（`.plt`）
- `describe_tools`：返回工具目录、分类、enabled/disabled 状态

可通过环境变量禁用工具：

```bash
TCAD_MCP_DENY_TOOLS=run_bash,run_sdevice
```

---

## 2) HTTP 网关模式

启动：

```bash
bash /data/yphu/TCAD_Agent/code/mcp/start_mcp_http.sh
```

默认地址：`http://127.0.0.1:8766/mcp`

可用环境变量：

- `TCAD_MCP_HTTP_HOST`（默认 `127.0.0.1`）
- `TCAD_MCP_HTTP_PORT`（默认 `8766`）
- `TCAD_GATEWAY_WORKSPACE`（默认仓库根目录）
- `TCAD_GATEWAY_ASYNC_WORKERS`（默认 `2`）
- `TCAD_GATEWAY_DENY_METHODS`（逗号分隔 `api_tcad_*`）
- `TCAD_GATEWAY_DEFAULT_TIMEOUT_MS`（全局默认超时，`0` 表示不限制）
- `TCAD_GATEWAY_MAX_TIMEOUT_MS`（全局最大超时，`0` 表示不限制）
- `TCAD_GATEWAY_METHOD_DEFAULT_TIMEOUT_MS`（如 `api_tcad_run_sdevice=0`）
- `TCAD_GATEWAY_METHOD_MAX_TIMEOUT_MS`（如 `api_tcad_run_bash=30000`）
- `TCAD_GATEWAY_ASYNC_METHODS`（`*` 或逗号分隔 `api_tcad_*`）

### 网关对外工具

- `start_tcad_server(setup_workdir)`
- `call(method, params, timeout_ms, return_mode, instance_id?)`
- `call_async_start(method, params, timeout_ms, return_mode, instance_id?)`
- `call_async_status(job_id, include_response?)`
- `call_async_wait(job_id, wait_timeout_ms?, include_response?)`
- `list_instances()`
- `stop_server(instance_id?)`
- `cleanup_stale()`

### `call(...)` 方法命名

`call(...)` 仅接受 `api_tcad_*`，例如：

- 会话/发现：
  - `api_tcad_ping`
  - `api_tcad_handshake`
  - `api_tcad_list_methods`
  - `api_tcad_describe_method`
  - `api_tcad_create_session`
  - `api_tcad_show_state`
  - `api_tcad_show_capabilities`
  - `api_tcad_describe_tools`
- 对话驱动：
  - `api_tcad_decide_next_operation`
  - `api_tcad_run_operation`
  - `api_tcad_agent_decide_and_execute`
- 执行步骤：
  - `api_tcad_generate_sde`
  - `api_tcad_check_sde`
  - `api_tcad_run_sde`
  - `api_tcad_run_svisual_sde`
  - `api_tcad_inspect_tdr`
  - `api_tcad_generate_sdevice`
  - `api_tcad_check_sdevice`
  - `api_tcad_run_sdevice`
  - `api_tcad_run_svisual`
  - `api_tcad_validate_results`
  - `api_tcad_run_bash`
  - `api_tcad_tdx_convert`
  - `api_tcad_tdx_tclcmd`
  - `api_tcad_run_svisual_tcl_script`
  - `api_tcad_run_svisual_cutline_export`
  - `api_tcad_run_inspect_script`

---

## 设计说明

- `mcp/stdio_server.py`：stdio 入口（工具直连）
- `mcp/http_server.py`：HTTP 入口（managed gateway）
- `mcp/tool_service.py`：工具注册与内部总线
- `mcp/service.py`：实例管理 + `call(api_tcad_*)` 分发

核心执行仍由 `src/agent_system.py` 负责。

---

## 一键冒烟脚本（推荐）

```bash
python3 /data/yphu/TCAD_Agent/code/scripts/gateway_e2e_smoke.py
```

可选带会话创建（会调用 requirement 解析）：

```bash
python3 /data/yphu/TCAD_Agent/code/scripts/gateway_e2e_smoke.py --with-session \
  --requirement "仅创建测试会话，不执行仿真。"
```

---

## 3) Python HTTP Client Helper

若需要从外部 Python 程序访问 HTTP MCP 网关，可复用：

- `src/mcp_http_client.py`

主要能力：

- `TcadMCPHTTPClient(server_url=...)`
- `start_tcad_server(setup_workdir)`
- `call(method="api_tcad_*", ...)`
- `list_instances()`

说明：

- 该 helper 使用惰性导入，不会影响默认本地执行路径。
- 它会显式避开仓库内的 `mcp/` 包，优先导入 site-packages 中的 MCP client 实现。
