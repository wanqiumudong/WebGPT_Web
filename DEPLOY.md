# Web-FabGPT Deployment

## Goal

Keep deployment minimal and predictable:

- `deploy.sh start|status|stop` manages only `mysql + gptserver + user-manager`
- the front-end is started manually with `npm start`
- the six LLM services are started manually, one script per service

This document targets the local runnable mirror at `/data/yphu/Web-FabGPT`.

The remote runtime checkout remains `/data/Web-FabGPT`, but the commands below assume the local mirror on `10.98.193.46`.

## Provider Layout

The local mirror now uses:

- `SiliconFlow` text model for `chatbot`
- `SiliconFlow` vision-language model for `litho` parser and `circuit`
- `ohmygpt` text model for `tcad` by default
- local standalone model for `defect`
- local text embedding + reranker stack for `rag`
- local optical kernels and Sentaurus tools where the service itself needs them

Required shell environment before starting the API-backed services:

```bash
export WEB_FABGPT_SILICONFLOW_API_KEY=...
export WEB_FABGPT_OHMYGPT_API_KEY=...
export WEB_FABGPT_TEXT_MODEL=Qwen/Qwen2.5-72B-Instruct
export WEB_FABGPT_VL_MODEL=Qwen/Qwen2.5-VL-72B-Instruct
```

## Local Default Ports

The local default profile is:

- `4307` MySQL
- `5101` chatbot
- `5102` defect
- `5103` litho
- `5104` tcad
- `5105` circuit
- `5106` rag
- `5107` gptserver
- `5108` user-manager
- `3000` front-end

If `bash deploy.sh status` shows `occupied`, the port is already held by another local process and this stack should not reuse it blindly.

## Core Commands

Start the web backend:

```bash
cd /data/yphu/Web-FabGPT
bash deploy.sh start
```

Check status:

```bash
bash deploy.sh status
```

Stop the web backend:

```bash
bash deploy.sh stop
```

This does not start or stop the front-end and does not start or stop the six LLM services.

## Clean Start From Zero

If you want to start the whole system manually from a fully stopped state, use this exact order:

Start the web backend:

```bash
cd /data/yphu/Web-FabGPT
bash deploy.sh start
bash deploy.sh status
```

Start the front-end manually:

```bash
cd /data/yphu/Web-FabGPT/Web/front-end/Code
npm start
```

Start the six LLM services in order:

```bash
cd /data/yphu/Web-FabGPT
bash scripts/llm/start_rag.sh
bash scripts/llm/start_chatbot.sh
bash scripts/llm/start_defect.sh
bash scripts/llm/start_litho.sh
bash scripts/llm/start_tcad.sh
bash scripts/llm/start_circuit.sh
```

## Front-End

Start the front-end manually:

```bash
cd /data/yphu/Web-FabGPT/Web/front-end/Code
npm start
```

The front-end listens on `3000`.

## LLM Start Scripts

The manual LLM entrypoints are:

- `bash scripts/llm/start_rag.sh`
- `bash scripts/llm/start_chatbot.sh`
- `bash scripts/llm/start_defect.sh`
- `bash scripts/llm/start_litho.sh`
- `bash scripts/llm/start_tcad.sh`
- `bash scripts/llm/start_circuit.sh`

Recommended order:

1. `bash scripts/llm/start_rag.sh`
2. `bash scripts/llm/start_chatbot.sh`
3. `bash scripts/llm/start_defect.sh`
4. `bash scripts/llm/start_litho.sh`
5. `bash scripts/llm/start_tcad.sh`
6. `bash scripts/llm/start_circuit.sh`

## GPU Affinity

The default GPU split is:

- `GPU4`: `defect`
- `GPU5`: `rag` and `litho neural_ilt`

`chatbot`, `litho` parser, and `tcad` are API-backed. `circuit` starts a local model in `16bit` mode by default. The shared `vLLM` helper in `scripts/common.sh` is retained as an optional service hook, but the standard `start_circuit.sh` path does not start it.

If you need to change the split temporarily, export these variables before starting the services:

```bash
export WEB_FABGPT_RAG_GPU=5
export WEB_FABGPT_DEFECT_GPU=5
export WEB_FABGPT_LITHO_GPU=5
export WEB_FABGPT_LITHO_CPU_WORKERS=2
export WEB_FABGPT_LITHO_GPU_WORKERS=1
export WEB_FABGPT_TCAD_MAX_CONCURRENT=2
```

`defect` is forced into single-GPU mode through the start script to avoid accidental cross-GPU sharding.

If a related tmux session is already running, changing these variables alone does not move the process to another GPU. Kill the session first, then start it again:

```bash
tmux kill-session -t RAG
tmux kill-session -t defect
tmux kill-session -t litho
tmux kill-session -t TCAD
```

## Status Interpretation

`bash deploy.sh status` reports:

- core backend state for `mysql`, `gptserver`, and `user-manager`
- front-end port state for `web`
- manual service state for `RAG`, `chatbot`, `defect`, `litho`, `TCAD`, and `circuit`

For the manual LLM services:

- `tmux=present` means the session exists
- `status=open` means the port is listening
- `tmux=absent` with `status=open` means the port is occupied by an unmanaged process and should be cleaned before using the new scripts

## Service Dependency Notes

- `chatbot` requires `gptserver`
- `circuit` requires `gptserver`
- `tcad` works best with `rag`
- `chatbot`, `litho` parser, and `circuit` require a valid `WEB_FABGPT_SILICONFLOW_API_KEY`
- `tcad` requires a valid `WEB_FABGPT_OHMYGPT_API_KEY` in the default profile
- `defect` does not use SiliconFlow
- `rag` supports concurrent task intake and some parallel document work, but heavy ingestion is still constrained by a shared lock

## Logs And Manual Control

The six LLM services are manual tmux sessions:

```bash
tmux ls
tmux capture-pane -pt chatbot
tmux capture-pane -pt RAG
tmux attach -t TCAD
tmux kill-session -t circuit
```

## Runtime Ports

- `4307` MySQL
- `5101` chatbot
- `5102` defect
- `5103` litho
- `5104` tcad
- `5105` circuit
- `5106` rag
- `5107` gptserver
- `5108` user-manager
- `3000` front-end
