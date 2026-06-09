# Sentaurus TCAD Agent

## 1. 项目定位

这个项目是一个面向 Sentaurus TCAD 的 Agent 后端框架，目标是让用户只通过自然语言就能完成以下事情：

- 生成 SDE 器件结构代码
- 检查并运行 SDE，产出网格与 TDR
- 生成 SDevice 仿真代码
- 运行 SDevice，产出 PLT/TDR
- 通过 SVisual/Inspect 等工具导出结构图、曲线图和指标
- 执行最终验证并给出可追溯报告
- 通过 stdio MCP 或 HTTP gateway 对外提供可编排接口

当前策略为“LLM 主导 + 最小兜底”：

- 主 Agent 决策、需求解析、子模型简报由 LLM 主导完成
- 当 LLM 输出异常时，仅做最小流程兜底（不中断）
- 不注入本地案例检索片段，SDE/SDevice 文本由模型直接生成
- 已去除基于关键词/正则的需求语义提取与 deck 规则修补链

## 2. 系统总览

```text
用户输入（自然语言）
        │
        ▼
main.py 交互入口
        │
        ▼
TCADAgentSystem（src/agent_system.py）
  - 会话状态管理
  - 决策下一步操作
  - 通过 MCP 工具总线调用能力
        │
        ├── LLMDeckEngine（src/llm_engine.py）
        │     - 生成 SDE/SDevice 文本
        │     - 失败日志回灌重试
        │
        ├── SentaurusOps（src/sentaurus_ops.py）
        │     - 真实调用 sde/sdevice/svisual/tdx/inspect
        │
        ├── TcadGatewayMCPService（mcp/service.py）
        │     - managed instance + call(api_tcad_*)
        │
        ├── TcadMCPHTTPClient（src/mcp_http_client.py）
        │     - 外部 Python/UI 的 HTTP MCP client helper
        │
        └── PhysicalValidator（src/validate.py）
              - 结构/曲线/指标联合验证
```

## 3. 核心执行流程

默认主流程阶段（`state.stage`）如下：

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
11. `validated` 或 `validation_failed`

说明：

- 主 Agent 采用逐步决策模式：每轮由 LLM 决定“下一步一个操作”。
- 当 LLM 决策异常时，回退到最小流程兜底，避免流程中断。
- 每个步骤执行后都更新 `runtime/default/state.json`，可断点查看状态。
- 启动 `python3 main.py` 时会先将旧的 `runtime/default` 归档到 `runtime/timeline/default_时间戳`，再创建新的 `runtime/default`。
- 增加重复步骤防护：若连续重复同一步且 stage 不变化，主循环会自动停止，避免死循环。

## 4. 模块细节

### 4.1 `main.py`

职责：

- 提供命令行交互界面
- 启动时自动归档上一轮 `runtime/default` 并重建全新 `default`
- 接收用户输入并交给主 Agent
- 提供 `/help`、`/state`、`/tools`、`/clean`、`/exit`
- 普通对话仅显示自然语言回复（不直接打印整段 JSON）
- `/state` 和 `/tools` 保留 JSON 输出用于调试/检查

### 4.2 `src/agent_system.py`

职责：

- 系统主控与编排
- 维护会话状态与阶段推进
- 把用户输入交给 LLM 做下一步决策
- 将决策映射为 MCP 工具调用
- 汇总每步输出（文件路径、日志、指标）

关键点：

- `create_session(requirement)` 使用 LLM 解析；若失败则回退到最小默认规格（不做关键词提取）
- `decide_next_operation()` 在无会话时会先自动 `create_session`，再做执行/问答决策，避免首轮可执行任务被误判为纯聊天
- `decide_next_operation()` 由 LLM 路由问答/执行并返回 JSON 决策
- 决策提示词中显式要求“优先复用已有产物，不重复生成上游步骤”
- `generate_sde/generate_sdevice` 前，主 Agent 先生成工程简报再下发子模型
- `run_operation()` 统一通过内部 MCP 工具总线执行
- `agent_decide_and_execute()` 在执行后会生成面向用户的简洁汇报文本

### 4.3 `src/llm_engine.py`

职责：

- 管理 LLM 客户端与模型配置
- 生成 SDE/SDevice deck（纯生成）
- 生成失败后用错误日志回灌重试
- 保存每轮 `system/user/raw` prompt 产物

关键点：

- `generate_sde()`：写入 `run/sde_dvs.cmd`，再调用 `sde -S`、`sde -e -l`、`tdx -info` 做真实校验
- `generate_sdevice()`：写入 `run/sdevice_des.cmd`，再调用 `sdevice -P` 预检查
- 纯生成模式下不注入 Training/Dataset/Lessons 参考片段
- 不做本地规则化 sanitize，deck 直接由模型输出并通过真实工具校验

### 4.4 `src/sentaurus_ops.py`

职责：

- 统一封装外部命令执行
- 真实调用 `sde/sdevice/svisual/tdx/inspect`
- 统一日志落盘、返回码处理、产物路径回传

特性：

- 启动时检查核心二进制是否在 PATH 中
- 每个工具调用写入结构化调试事件

### 4.5 `src/validate.py`

职责：

- 最终结果验证（不是 Python 语法验证）

验证维度：

- 输入输出文件完整性
- SDE/SDevice 关键结构存在性
- PLT 曲线可解析性
- Ion/Ioff/SS 等指标提取与目标对比（若目标给定）

说明：

- 已去掉“材料关键词匹配需求”“2D/3D 字符串匹配维度”等硬识别检查

### 4.6 `mcp/tool_service.py`

职责：

- 定义并注册 MCP 工具
- 提供 `list_tools / call_tool`
- 作为主 Agent 的内部工具总线

### 4.7 `mcp/stdio_server.py`

职责：

- 提供 stdio 协议 MCP 服务入口
- 便于外部客户端接入同一套工具能力

### 4.8 `mcp/service.py` + `mcp/http_server.py`

职责：

- 提供 managed gateway（`start_tcad_server` + `call(api_tcad_*)`）
- 支持多实例会话、异步调用（`call_async_*`）和实例生命周期管理
- 将外部网关协议与内部 `TCADAgentSystem` 解耦

### 4.9 `src/mcp_http_client.py`

职责：

- 提供 HTTP MCP client 的同步封装
- 便于外部 Python 程序或未来 UI 壳通过 `start_tcad_server + call(api_tcad_*)` 访问主系统
- 避免本地仓库 `mcp/` 包与 site-packages `mcp` 冲突

### 4.10 `skills/`

当前保留 4 个技能提示词目录：

- `main_agent`
- `planner`
- `sde_codegen`
- `sdevice_codegen`

作用：

- 作为系统提示词来源，约束主决策与代码生成行为

## 5. MCP 工具列表（20）

1. `create_session(requirement="")`：创建或重置默认会话（保存原始需求文本）
2. `show_state()`：查看会话状态、产物与指标
3. `describe_tools()`：查看 MCP 工具目录与启用状态
4. `run_bash(command, cwd="", timeout_s=30)`：执行 Bash 命令（如 `ls/cat/head`）
5. `generate_sde_code()`：调用 LLM 生成 SDE deck
6. `check_sde_syntax()`：运行 `sde -S`
7. `run_sde()`：运行 `sde -e -l`
8. `run_svisual_sde_export(source_file="", mode="tdr")`：导出结构图
9. `inspect_tdr(tdr_filename="sde_result_msh.tdr")`：运行 `tdx -info`
10. `tdx_convert(command, source_file, dest_file="", options=[])`：运行 `tdx --<command>`
11. `tdx_tclcmd(tcl_command)`：运行 `tdx -tclcmd`
12. `generate_sdevice_code()`：调用 LLM 生成 SDevice deck
13. `check_sdevice_syntax()`：运行 `sdevice -P`
14. `run_sdevice()`：运行 `sdevice --exit-on-failure`
15. `run_svisual_export(source_file="", mode="plt")`：导出曲线 PNG/文本
16. `run_svisual_tcl_script(script_content="", script_file="", expected_outputs=[])`：运行自定义 svisual Tcl
17. `run_svisual_cutline_export(source_file, axis="x", at=0.0, variables=[])`：cutline CSV/PNG 导出
18. `run_inspect_script(script_content="", script_file="", input_files=[], expected_outputs=[], batch=True)`：运行 inspect 脚本提参
19. `validate_results()`：执行联合验证

## 6. 输入输出文件关系（关键）

主链路常见文件关系如下：

- `run/sde_dvs.cmd` → 输入 `sde -S`、`sde -e -l`
- `run/sde_result_msh.tdr`、`run/sde_result_bnd.tdr` ← `run_sde()`
- `reports/tdr_info_*.txt` ← `inspect_tdr()`
- `run/sdevice_des.cmd` → 输入 `sdevice -P`、`sdevice --exit-on-failure`
- `run/*.plt`、`run/*.tdr` ← `run_sdevice()`
- `reports/*.png`、`reports/*_curve.txt` ← `run_svisual_export()` / `run_svisual_sde_export()`
- `reports/validation.json` ← `validate_results()`

## 7. 运行时可追踪能力

每轮运行会留下以下可追踪文件：

- `runtime/default/state.json`：当前会话状态
- `runtime/default/logs/debug_trace.jsonl`：会话级事件轨迹
- `runtime/default/reports/debug_global.jsonl`：全局事件轨迹
- `runtime/default/logs/prompts/*.txt`：SDE/SDevice 子模型 prompt 与原始输出
- `runtime/default/logs/main_agent_sde_brief.txt`：主 Agent 给 SDE 子模型的工程简报
- `runtime/default/logs/main_agent_sdevice_brief.txt`：主 Agent 给 SDevice 子模型的工程简报

## 8. 环境配置

最小依赖：

- 可执行程序：`sde`、`sdevice`、`svisual`、`tdx`（`inspect` 可选）
- Python 依赖：`pip install -r requirements.txt`
- 若要使用 HTTP gateway / MCP Python client，需要安装 `mcp`、`anyio`、`httpx`、`uvicorn`

LLM 默认配置（内置）：

- Base URL: `https://api.ohmygpt.com/v1`
- 主模型: `gemini-3.1-flash-lite-preview`
- SDE/SDevice 模型: `gemini-3.1-flash-lite-preview`

说明：

- 代码不应内置 API Key；请通过 `TCAD_LLM_API_KEY` 或部署脚本注入。
- 默认网关保留 `ohmygpt` 与 `siliconflow` 两类配置。
- 如需覆盖 provider、base URL 或模型，使用 `TCAD_LLM_*` 环境变量。
- `siliconflow + Qwen2.5` 的 SDE/SDevice 备用接入口已保留在 `src/llm_engine.py` 注释中（默认关闭）。

## 9. 快速启动

```bash
source ~/.bashrc
which sde sdevice svisual tdx inspect

cd /data/yphu/TCAD_Agent/code
pip install -r requirements.txt
python3 main.py
```

HTTP gateway 启动：

```bash
bash /data/yphu/TCAD_Agent/code/mcp/start_mcp_http.sh
```

HTTP 网关模式（可选）：

```bash
bash /data/yphu/TCAD_Agent/code/mcp/start_mcp_http.sh
# 默认: http://127.0.0.1:8766/mcp
```

网关一键冒烟（可选）：

```bash
python3 /data/yphu/TCAD_Agent/code/scripts/gateway_e2e_smoke.py
```

交互命令：

- `/help`
- `/state`
- `/tools`
- `/clean`
- `/exit`

交互输出说明：

- 普通输入：显示模型自然语言回复
- 执行过程：保留 `[→] / [✓] / [✗]` 工具执行日志
- 详细状态与调试：通过 `/state`、`runtime/default/logs/*` 查看

## 10. 交付与参考文件

- 架构补充：`ARCHITECTURE.md`
- MCP 说明：`mcp/README.md`
- 最新 QA 与报告：`deliverables/current/`

## 11. 当前工作逻辑（重点）

主 Agent 不是固定脚本，它每轮都做一次“路由 + 计划 + 执行”：

1. 路由（chat/execute）  
- 对当前用户输入做 LLM 路由。  
- `chat`：只返回文本，不调用工具。  
- `execute`：进入工具决策链。

2. 会话与上下文处理  
- 会话状态存放在 `runtime/default/state.json`。  
- 不做自动重置：所有执行请求默认在当前会话上续跑。  
- 若要开始新任务，用户显式执行 `/clean` 后再输入新需求。  

3. LLM 计划与单步执行  
- 先由 LLM 产出一组候选操作（scope）。  
- 系统只执行其中“当前尚未完成”的下一步。  
- 每步执行完都写回 `state.json`，下一轮继续从最新 stage 决策。

4. 工具执行  
- 实际调用 MCP 工具总线（内部），再由 `SentaurusOps` 调真实二进制。  
- 终端显示 `[→] / [✓] / [✗]`；详细日志写入 `runtime/default/logs`。
