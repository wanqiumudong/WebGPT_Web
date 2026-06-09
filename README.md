# Web-FabGPT

Web-FabGPT 是当前可运行的半导体智能助手系统，主要由以下部分组成：

- React 前端
- Java `gptserver`，负责会话和消息
- Java `user-manager`，负责登录和用户状态
- MySQL
- Python 服务，包括 `chatbot`、`defect`、`litho`、`tcad`、`rag` 和 `circuit`

当前本地镜像同时包含 API 服务和本地运行服务：

- `chatbot` 使用 `SiliconFlow`
- `litho` 使用 `SiliconFlow` 做意图解析，同时保留本地仿真和优化 kernel
- `tcad` 使用 `ohmygpt` 做 LLM generation，并在本地执行 Sentaurus
- `circuit` 保留本地图像分析模型路径，同时在代码中使用 API 做后续文本路由
- `defect` 保留自己的本地缺陷检测模型栈
- `rag` 保留本地 text embedding 和 rerank 栈

这个仓库是 `/data/yphu/Web-FabGPT` 的本地可编辑、可运行镜像。

本地镜像使用 `10.98.193.46` 上的一组专用端口。

## 运行端口

- `3000` Web 前端，手动通过 `npm start` 启动
- `4307` MySQL
- `5101` chatbot
- `5102` defect
- `5103` litho
- `5104` tcad
- `5105` circuit
- `5106` rag
- `5107` gptserver
- `5108` user-manager

## 当前启动流程

1. 启动 Web 后端：

```bash
cd /data/yphu/Web-FabGPT
bash deploy.sh start
```

2. 查看后端状态：

```bash
bash deploy.sh status
```

3. 手动启动前端：

```bash
cd /data/yphu/Web-FabGPT/Web/front-end/Code
npm start
```

4. 逐个启动 LLM 服务：

```bash
cd /data/yphu/Web-FabGPT
bash scripts/llm/start_rag.sh
bash scripts/llm/start_chatbot.sh
bash scripts/llm/start_defect.sh
bash scripts/llm/start_litho.sh
bash scripts/llm/start_tcad.sh
bash scripts/llm/start_circuit.sh
```

5. 需要时停止 Web 后端：

```bash
bash deploy.sh stop
```

## 手动查看日志

六个 LLM 服务通过 tmux 手动管理。可以使用下面的命令查看或控制：

```bash
tmux ls
tmux capture-pane -pt chatbot
tmux capture-pane -pt TCAD
tmux attach -t RAG
tmux kill-session -t circuit
```

## 服务并发

- `chatbot` 由 API 支持，不消耗本地 GPU 做 language generation。
- `defect` 限制为同一时间只运行一个本地模型任务，默认在 `GPU4` 上运行。
- `litho` 只用 SiliconFlow 做意图解析。本地 CPU 任务默认并发数为 `2`，`neural_ilt` 默认限制为 `GPU5` 上一个本地 GPU 任务。
- `tcad` 使用 `ohmygpt` 做 LLM generation，本地 Sentaurus / agent 执行限制为 `2` 个并发 session。同一个 conversation 会始终串行执行。
- `circuit` 使用本地图像分析路径，并在代码中包含 API follow-up routing。本地模型路径当前默认在 `GPU4` 上启动。
- `rag` 支持并发任务接入，但重型 ingestion 仍受自身 runtime 约束。

## GPU 绑定

- `circuit` 默认使用 `GPU4`
- `defect` 默认使用 `GPU4`
- `rag` 默认使用 `GPU5`
- `litho neural_ilt` 默认使用 `GPU5`

启动前可以通过环境变量覆盖默认配置：

```bash
export WEB_FABGPT_RAG_GPU=5
export WEB_FABGPT_DEFECT_GPU=5
export WEB_FABGPT_LITHO_GPU=5
export WEB_FABGPT_LITHO_CPU_WORKERS=2
export WEB_FABGPT_LITHO_GPU_WORKERS=1
export WEB_FABGPT_TCAD_MAX_CONCURRENT=2
```

本地镜像从 shell 环境变量读取 provider credentials。当前混合配置通常需要：

```bash
export WEB_FABGPT_SILICONFLOW_API_KEY=...
export WEB_FABGPT_OHMYGPT_API_KEY=...
export WEB_FABGPT_TEXT_MODEL=Qwen/Qwen2.5-72B-Instruct
export WEB_FABGPT_VL_MODEL=Qwen/Qwen2.5-VL-72B-Instruct
```

如果 `RAG` 或 `defect` 已经在 tmux 中运行，修改 GPU 环境变量后需要先 kill 对应 session 再重新启动，否则旧进程仍会继续占用原来的 GPU。

## 仓库结构

- `Web/front-end/Code/`：React 前端
- `Web/back-end/gptserver/`：会话和消息后端
- `Web/back-end/user-management/`：登录和用户状态后端
- `Web/back-end/mysql/`：MySQL runtime
- `LLM/chatbot/qwen2_api.py`：chatbot 入口
- `LLM/defect/code/web_demo.py`：defect 入口
- `LLM/litho/litho.py`：litho 入口
- `LLM/tcad_agent_core/web/tcad_web_adapter.py`：tcad 入口
- `LLM/rag/text_rag_service.py`：rag 入口
- `LLM/circuit/circuit.py`：circuit 入口
- `deploy.sh`：统一管理 `mysql + gptserver + user-manager` 的 `start/status/stop`
- `scripts/llm/`：六个手动 LLM 启动脚本

## 说明

- `chatbot` 是之前称为 `qwen2` 的服务在 runtime 中的 session 名称。
- `LLM_bak_balance/` 是历史备份内容，不是当前 active runtime path。
- `circuit` 当前从 `LLM/circuit/local_models/global_step_700_actor/huggingface` 启动，代码中包含 image-local 和 text-API 混合路由。
- 部署细节见 [DEPLOY.md](DEPLOY.md)。
