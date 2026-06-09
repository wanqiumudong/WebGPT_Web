# 架构说明

本文档描述当前 `/data/yphu/TCAD_Agent/code` 的实际架构和数据流。

## 1. 目标

- 用户只用自然语言驱动 TCAD 任务。
- 主 Agent 自主决定是否调用工具。
- 工具真实调用 Sentaurus 命令，不做伪执行。
- 结果用结构/曲线/指标联合验证。

## 2. 模块结构

- `main.py`
  - 默认交互入口（`python3 main.py`）。
  - 提供 `/help`、`/state`、`/tools`、`/clean`、`/exit`。
- `src/agent_system.py`
  - 主编排器，维护 `runtime/default/state.json`。
  - LLM 优先做：意图路由、下一步决策、需求结构化、子模型工程简报。
  - 决策失败时使用最小规则兜底，保证流程可执行。
- `src/llm_engine.py`
  - 管理 LLM 客户端。
  - 生成 SDE/SDevice deck，失败时根据日志重试。
- `src/sentaurus_ops.py`
  - 统一封装 `sde/sdevice/svisual/tdx/inspect` 调用。
  - 负责日志落盘、超时控制、产物路径回传。
- `src/validate.py`
  - 验证结构完整性、曲线有效性、指标提取（Ion/Ioff/SS）。
- `mcp/tool_service.py`
  - MCP 工具总线（内部调用与外部 stdio 服务共用同一工具定义）。
- `mcp/stdio_server.py`
  - MCP stdio 服务入口。
- `mcp/http_server.py` + `mcp/service.py`
  - HTTP 网关入口与 managed-instance 服务层。
- `src/mcp_http_client.py`
  - 外部 Python/UI 访问 HTTP MCP 网关的同步 helper。

## 3. 主流程

典型阶段：

1. `created`
2. `sde_generated`
3. `sde_checked`
4. `sde_done`
5. `svisual_sde_done`
6. `tdr_inspected`
7. `sdevice_generated`
8. `sdevice_checked`
9. `sdevice_done`
10. `svisual_done`
11. `validated`

主 Agent 为逐步循环：每轮只决定并执行一个操作，直到完成或失败。

## 4. 模型优先策略

- `create_session`：LLM 先解析 requirement，规则解析兜底。
- `decide_next_operation`：LLM 先判断 chat/execute，再给下一步操作。
- `generate_sde/generate_sdevice`：主 Agent 先生成工程简报，再调用子生成器。
- 针对用户显式需求（结构图、曲线图、指标），规划提示词中要求覆盖对应工具。

## 5. 关键产物

- `runtime/default/run/`
  - `sde_dvs.cmd`、`sdevice_des.cmd`
  - `sde_result_msh.tdr`、`sde_result_bnd.tdr`
  - `*.plt`、`*.tdr`
- `runtime/default/reports/`
  - 结构/曲线 PNG
  - 曲线文本
  - `validation.json`
- `runtime/default/logs/`
  - `debug_trace.jsonl`
  - 模型 prompt 与原始输出
  - 主 Agent 下发简报（`main_agent_sde_brief.txt`、`main_agent_sdevice_brief.txt`）

## 6. 当前交付快照

最新可检阅交付位于 `deliverables/current/`：

- `DELIVERY_STATUS.md`
- `MCP_AUDIT_REPORT.md`
- `mcp_audit_results.json`
- `RUN_1772343388_NMOS_E2E_DIRECT_V2/`（真实案例产物与日志）

历史快照整理后的当前案例入口位于 `deliverables/catalogs/`：

- `active_case_manifest_v1.json`
- `manual_full_suite_v2_recovered.json`

lessons/tutorial 相关快照仍保留在 `deliverables/archive_git_recovery_20260307/`，但不作为当前执行入口。

## 7. 网关说明

当前 HTTP gateway 入口和 MCP 使用说明位于 `mcp/README.md`。
