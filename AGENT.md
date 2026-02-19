# AGENT.md

本文件是 `/data/Web-FabGPT` 的统一 Agent 协作说明。

## 1) 项目定位

Web-FabGPT 是一个面向半导体 EDA 场景的 LLM Agent Web 框架，集成了多类专业模型与工具链：

- 通用聊天（Chatbot / Qwen2 API）
- 缺陷检测（Defect / FabGPT）
- 器件仿真问答（TCAD）
- 电路图分析与网表生成（CircuitThink）
- 文档知识库检索（RAG Manager / ColPali + Milvus）
- 光刻相关工具流（Litho）

整体由 React 前端 + 多个 Python Flask 服务 + Java 后端服务 + MySQL 组成，并通过 5 个负载均衡器做统一入口。

## 2) 文件总结（Repository Map）

### 2.1 顶层目录与文件

- `LLM/`：核心 AI 服务与模型代码（当前目录体量最大，约 113G）
- `Web/`：前后端 Web 工程与运行环境（约 4.6G）
- `rag_load_balancer.py`：RAG 负载均衡器入口（Flask）
- `defect_load_balancer.py`：Defect 负载均衡器入口（Flask）
- `tcad_load_balancer.py`：TCAD 负载均衡器入口（Flask）
- `circuit_load_balancer.py`：Circuit 负载均衡器入口（Flask）
- `chatbot_load_balancer.py`：Chatbot 负载均衡器入口（Flask）
- `COMPLETE_DEPLOYMENT_PLAN.md`：8 路并行部署规划（规划文档）
- `CLAUDE.md`：旧文档入口（已迁移到 `AGENT.md`）

### 2.2 `LLM/` 关键结构

- `LLM/FabGPT/code/`
  - `qwen2_api.py`：通用聊天 API 服务（会联动会话系统与 RAG）
  - `web_demo_datacenter.py`：Defect GPU 侧服务入口
- `LLM/Circuit_Think/circuit.py`：CircuitThink 服务入口（Qwen2.5-VL）
- `LLM/TCAD_code/TCAD_web_MilvusRAG.py`：TCAD 服务入口（含 RAG 调用）
- `LLM/RagManager/colpali_rag_manager.py`：RAG Manager 服务入口
- `LLM/RagManager/Rag_Framework/`：RAG 核心框架（配置、检索、任务管理、用户配置）
- `LLM/litho.py` + `LLM/litho_code/`：光刻流程服务与解析执行链
- `LLM/Mirror/`：镜像/备份代码树（与主代码存在重复）
- `LLM/utils/response_utils.py`：通用响应工具

### 2.3 `Web/` 关键结构

- `Web/front-end/Code/`：React 前端工程
  - `src/layout/index.jsx`：主布局
  - `src/components/container/container.jsx`：页面路由装配
  - `src/pages/`：ChatBot / FabGPT / TCAD / CircuitThink / Guangke 等页面
  - `src/components/RagManager/index.jsx`：知识库管理页面组件
  - `src/api/`：各模型 API 封装（大多对接 5100-5104 负载均衡器）
  - `src/utils/request.js`：统一请求封装
- `Web/back-end/gptserver/`：Java gptserver 产物与启动脚本（含 `gpt-server.jar`）
- `Web/back-end/user-management/`：用户管理服务源码（Spring Boot）与脚本
- `Web/back-end/mysql/`：MySQL 运行目录与数据文件

### 2.4 运行产物与大体量目录（非核心源码）

以下目录包含依赖、数据或运行时产物，通常不应作为功能开发首选修改区：

- `Web/front-end/Code/node_modules/`
- `Web/front-end/Code/build/`
- `Web/back-end/mysql/` 及其 `data/`
- `LLM/**/logs/`, `LLM/**/upload_files/`, `LLM/**/generate_files/`, `LLM/**/output*`
- `LLM/Mirror/`（镜像代码）

## 3) 架构与端口（按当前代码）

### 3.1 负载均衡器入口

- `rag_load_balancer.py` -> `0.0.0.0:5100`
- `defect_load_balancer.py` -> `0.0.0.0:5101`
- `tcad_load_balancer.py` -> `0.0.0.0:5102`
- `circuit_load_balancer.py` -> `0.0.0.0:5103`
- `chatbot_load_balancer.py` -> `0.0.0.0:5104`

### 3.2 负载均衡器当前后端目标（代码现状）

- RAG LB: `remote_server=10.98.193.46`, `rag_ports=[5006]`（注释保留 8 实例端口）
- Defect LB: `remote_server=10.98.193.46`, `defect_ports=[5008]`（注释保留 8 实例端口）
- TCAD LB: `local_server=10.98.64.22`, `tcad_ports=[5002]`（注释保留 8 实例端口）
- Circuit LB: `remote_server=10.98.193.46`, `circuit_ports=[5007]`（注释保留 8 实例端口）
- Chatbot LB: `local_server=10.98.64.22`, `chatbot_ports=[5013]`（注释保留 8 实例端口）

说明：这些脚本已具备“8 路并行”设计，但当前数组实际只启用单端口。

### 3.3 关键服务默认端口（入口脚本默认值）

- `LLM/RagManager/colpali_rag_manager.py`：`RAG_MANAGER_PORT` 默认 `5006`
- `LLM/FabGPT/code/web_demo_datacenter.py`：`SERVICE_PORT` 默认 `5008`
- `LLM/Circuit_Think/circuit.py`：`SERVICE_PORT` 默认 `5007`
- `LLM/TCAD_code/TCAD_web_MilvusRAG.py`：`SERVICE_PORT` 默认 `5004`
- `LLM/FabGPT/code/qwen2_api.py`：`SERVICE_PORT` 默认 `5002`

## 4) 前端 API 映射（核心）

前端 API 文件当前主要使用负载均衡器入口：

- `src/api/chatBot.js` -> `http://10.98.64.22:5104`
- `src/api/fabGpt.js` -> `http://10.98.64.22:5101`
- `src/api/tcadApi.js` -> `http://10.98.64.22:5102`
- `src/api/circuitApi.js` -> `http://10.98.64.22:5103`
- `src/api/ragApi.js` -> `http://10.98.64.22:5100`

同时，多个页面仍直接调用 `http://10.98.64.22:8080` 的会话/消息接口（`session/*`, `message/*`），因此 gptserver 仍是会话主链路。

## 5) 后端与账户系统

- gptserver 配置位于 `Web/back-end/gptserver/BOOT-INF/classes/application.properties`，连接 MySQL `127.0.0.1:4306/zgpt`。
- 用户管理服务在 `Web/back-end/user-management/`：
  - Spring Boot 端口：`5203`（`application.properties`）
  - 另有 `SimpleUserServer.java`（8081）作为简化实现/历史保留
- MySQL 位于 `Web/back-end/mysql/`，数据表包括 `users/sessions/messages/models` 等。

## 6) 端口与配置注意事项（非常重要）

当前代码存在“默认端口”与“负载均衡器目标端口”不完全一致的情况，部署时必须通过环境变量统一：

- Chatbot:
  - `qwen2_api.py` 默认 `5002`
  - `chatbot_load_balancer.py` 目标为 `5013`
- TCAD:
  - `TCAD_web_MilvusRAG.py` 默认 `5004`
  - `tcad_load_balancer.py` 目标为 `5002`

建议统一使用 `SERVICE_PORT` / `RAG_MANAGER_PORT` 显式启动，避免默认值错配。

## 7) 常用启动路径（开发/联调）

- 前端：
  - `cd Web/front-end/Code && npm install && npm start`
- 用户管理：
  - `cd Web/back-end/user-management && ./build.sh && ./start.sh`
- MySQL：
  - `cd Web/back-end/mysql && ./start.sh`
- 负载均衡器（按需）：
  - `python rag_load_balancer.py`
  - `python defect_load_balancer.py`
  - `python tcad_load_balancer.py`
  - `python circuit_load_balancer.py`
  - `python chatbot_load_balancer.py`

## 8) Agent 协作建议

- 优先修改源码目录，不要误改运行产物与大模型文件。
- 涉及端口、IP、路由时，务必同时检查：
  - 前端 API 常量
  - 负载均衡器目标端口
  - 后端服务默认端口/环境变量
- `LLM/Mirror/` 为镜像树，除非明确要求，不应与主树同时改动。
- 对会话链路问题优先检查 `8080` 相关接口（前端大量直连）。

## 9) 已知风险

- 部分服务脚本中存在硬编码 API Key 与内网 IP。
- 文档与代码可能出现端口漂移（尤其 8 实例规划与单实例实配并存）。
- 仓库包含大量运行数据与依赖，变更前需确认是否应纳入版本控制。
