# Web-FabGPT

Web-FabGPT is the current live semiconductor assistant stack built from:

- React front-end
- Java `gptserver` for sessions and messages
- Java `user-manager` for login and user status
- MySQL
- Python services for `chatbot`, `defect`, `litho`, `tcad`, `rag`, and `circuit`

The local mirror currently mixes API and local runtimes:

- `chatbot` uses `SiliconFlow`
- `litho` uses `SiliconFlow` for intent parsing and keeps local simulation and optimization kernels
- `tcad` uses `ohmygpt` for LLM generation and local Sentaurus execution
- `circuit` keeps a local image-analysis model path and also uses API follow-up routing in code
- `defect` keeps its own local defect model stack
- `rag` keeps its local text embedding and rerank stack

This repository copy is the local editable and runnable mirror at `/data/yphu/Web-FabGPT`.

The local mirror uses a dedicated local port family on `10.98.193.46`.

## Runtime Ports

- `3000` web front-end, started manually with `npm start`
- `4307` MySQL
- `5101` chatbot
- `5102` defect
- `5103` litho
- `5104` tcad
- `5105` circuit
- `5106` rag
- `5107` gptserver
- `5108` user-manager

## Current Workflow

1. Start the web backend:

```bash
cd /data/yphu/Web-FabGPT
bash deploy.sh start
```

2. Check backend status:

```bash
bash deploy.sh status
```

3. Start the front-end manually:

```bash
cd /data/yphu/Web-FabGPT/Web/front-end/Code
npm start
```

4. Start LLM services one by one:

```bash
cd /data/yphu/Web-FabGPT
bash scripts/llm/start_rag.sh
bash scripts/llm/start_chatbot.sh
bash scripts/llm/start_defect.sh
bash scripts/llm/start_litho.sh
bash scripts/llm/start_tcad.sh
bash scripts/llm/start_circuit.sh
```

5. Stop the web backend when needed:

```bash
bash deploy.sh stop
```

## Manual Logs

The six LLM services are tmux-based manual services. Use these commands for inspection:

```bash
tmux ls
tmux capture-pane -pt chatbot
tmux capture-pane -pt TCAD
tmux attach -t RAG
tmux kill-session -t circuit
```

## Service Concurrency

- `chatbot` is API-backed and does not consume local GPU for language generation.
- `defect` is limited to one local model job at a time on `GPU4`.
- `litho` uses SiliconFlow only for intent parsing. Local CPU jobs default to `2` concurrent workers, and `neural_ilt` is limited to one local GPU job on `GPU5`.
- `tcad` uses `ohmygpt` for LLM generation, while local Sentaurus/agent execution is limited to `2` concurrent sessions. The same conversation is always serialized.
- `circuit` uses a local image-analysis path plus API follow-up routing in code. Its local model path is currently started on `GPU4`.
- `rag` supports concurrent task intake, but heavy ingestion is still constrained by its own runtime.

## GPU Affinity

- `circuit` defaults to `GPU4`
- `defect` defaults to `GPU4`
- `rag` defaults to `GPU5`
- `litho neural_ilt` defaults to `GPU5`

You can override the defaults before startup:

```bash
export WEB_FABGPT_RAG_GPU=5
export WEB_FABGPT_DEFECT_GPU=5
export WEB_FABGPT_LITHO_GPU=5
export WEB_FABGPT_LITHO_CPU_WORKERS=2
export WEB_FABGPT_LITHO_GPU_WORKERS=1
export WEB_FABGPT_TCAD_MAX_CONCURRENT=2
```

The local mirror reads provider credentials from your shell environment. In the current mixed setup you typically need:

```bash
export WEB_FABGPT_SILICONFLOW_API_KEY=...
export WEB_FABGPT_OHMYGPT_API_KEY=...
export WEB_FABGPT_TEXT_MODEL=Qwen/Qwen2.5-72B-Instruct
export WEB_FABGPT_VL_MODEL=Qwen/Qwen2.5-VL-72B-Instruct
```

If `RAG` or `defect` is already running in tmux, kill that session before restarting with a new GPU assignment. Otherwise the old process will keep using its current card.

## Repository Map

- `Web/front-end/Code/`: React front-end
- `Web/back-end/gptserver/`: session and message backend
- `Web/back-end/user-management/`: login and user state backend
- `Web/back-end/mysql/`: MySQL runtime
- `LLM/chatbot/qwen2_api.py`: chatbot entry
- `LLM/defect/code/web_demo.py`: defect entry
- `LLM/litho/litho.py`: litho entry
- `LLM/tcad_agent_core/web/tcad_web_adapter.py`: tcad entry
- `LLM/rag/text_rag_service.py`: rag entry
- `LLM/circuit/circuit.py`: circuit entry
- `deploy.sh`: unified `start/status/stop` for `mysql + gptserver + user-manager`
- `scripts/llm/`: the six manual LLM start scripts

## Notes

- `chatbot` is the runtime session name for the service previously called `qwen2`.
- `LLM_bak_balance/` is historical backup content, not the active runtime path.
- `circuit` currently starts from `LLM/circuit/local_models/global_step_700_actor/huggingface` and its code contains hybrid image-local plus text-API routing.
- The deployment details live in [DEPLOY.md](/data/yphu/Web-FabGPT/DEPLOY.md).
