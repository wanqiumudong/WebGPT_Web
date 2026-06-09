#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${WEB_FABGPT_HOST:-10.98.193.46}"
LOCALHOST="${WEB_FABGPT_LOCALHOST:-127.0.0.1}"
VLLM_GPU="${WEB_FABGPT_VLLM_GPU:-5}"
RAG_GPU="${WEB_FABGPT_RAG_GPU:-5}"
DEFECT_GPU="${WEB_FABGPT_DEFECT_GPU:-5}"
LITHO_GPU="${WEB_FABGPT_LITHO_GPU:-5}"
CONDA_HOME="${WEB_FABGPT_CONDA_HOME:-/data/yphu/anaconda3}"
CONDA_ENVS_DIR="${CONDA_HOME}/envs"
JAVA_HOME="${WEB_FABGPT_JAVA_HOME:-/data/share/Sentaurus_TCAD/jdk-17.0.2}"
STROOT="${WEB_FABGPT_STROOT:-/data/share/Sentaurus_TCAD/sentaurus24/V-2024.03}"
STRELEASE="${WEB_FABGPT_STRELEASE:-V-2024.03}"
STROOT_LIB="${WEB_FABGPT_STROOT_LIB:-${STROOT}/tcad/${STRELEASE}/lib}"
STDB="${WEB_FABGPT_STDB:-/data/yphu/STDB}"

SILICONFLOW_API_KEY="${WEB_FABGPT_SILICONFLOW_API_KEY:-}"
OHMYGPT_API_KEY="${WEB_FABGPT_OHMYGPT_API_KEY:-}"
TEXT_API_BASE_URL="${WEB_FABGPT_TEXT_API_BASE_URL:-https://api.siliconflow.cn/v1/chat/completions}"
VL_API_BASE_URL="${WEB_FABGPT_VL_API_BASE_URL:-https://api.siliconflow.cn/v1/chat/completions}"
TEXT_MODEL="${WEB_FABGPT_TEXT_MODEL:-Qwen/Qwen2.5-72B-Instruct}"
VL_MODEL="${WEB_FABGPT_VL_MODEL:-Qwen/Qwen2.5-VL-72B-Instruct}"
CHATBOT_PROVIDER="${WEB_FABGPT_CHATBOT_PROVIDER:-siliconflow}"
LITHO_PROVIDER="${WEB_FABGPT_LITHO_PROVIDER:-siliconflow}"
TCAD_PROVIDER="${TCAD_LLM_PROVIDER:-ohmygpt}"
TCAD_API_BASE_URL="${WEB_FABGPT_TCAD_API_BASE_URL:-https://api.ohmygpt.com/v1}"
TCAD_TEXT_MODEL="${WEB_FABGPT_TCAD_TEXT_MODEL:-gemini-3.1-flash-lite-preview}"
CIRCUIT_PROVIDER="${WEB_FABGPT_CIRCUIT_PROVIDER:-siliconflow}"
DEFECT_MAX_CONCURRENT="${WEB_FABGPT_DEFECT_MAX_CONCURRENT:-1}"
LITHO_CPU_WORKERS="${WEB_FABGPT_LITHO_CPU_WORKERS:-2}"
LITHO_GPU_WORKERS="${WEB_FABGPT_LITHO_GPU_WORKERS:-1}"
TCAD_MAX_CONCURRENT="${WEB_FABGPT_TCAD_MAX_CONCURRENT:-2}"

MYSQL_PORT="${WEB_FABGPT_MYSQL_PORT:-4307}"
GPTSERVER_PORT="${WEB_FABGPT_GPTSERVER_PORT:-5107}"
USER_MANAGER_PORT="${WEB_FABGPT_USER_MANAGER_PORT:-5108}"
CHATBOT_PORT="${WEB_FABGPT_CHATBOT_PORT:-5101}"
DEFECT_PORT="${WEB_FABGPT_DEFECT_PORT:-5102}"
LITHO_PORT="${WEB_FABGPT_LITHO_PORT:-5103}"
TCAD_PORT="${WEB_FABGPT_TCAD_PORT:-5104}"
CIRCUIT_PORT="${WEB_FABGPT_CIRCUIT_PORT:-5105}"
RAG_PORT="${WEB_FABGPT_RAG_PORT:-5106}"
WEB_PORT="${WEB_FABGPT_WEB_PORT:-3000}"
VLLM_PORT="${WEB_FABGPT_VLLM_PORT:-5110}"
VLLM_SESSION="${WEB_FABGPT_VLLM_SESSION:-vllm_${VLLM_PORT}}"

MYSQL_DIR="${ROOT_DIR}/Web/back-end/mysql"
MYSQL_START_SCRIPT="${MYSQL_DIR}/start.sh"
MYSQL_STOP_SCRIPT="${MYSQL_DIR}/stop.sh"
GPTSERVER_DIR="${ROOT_DIR}/Web/back-end/gptserver"
GPTSERVER_START_SCRIPT="${GPTSERVER_DIR}/bin/start.sh"
GPTSERVER_STOP_SCRIPT="${GPTSERVER_DIR}/bin/stop.sh"
USER_MANAGER_DIR="${ROOT_DIR}/Web/back-end/user-management"
USER_MANAGER_BUILD_SCRIPT="${USER_MANAGER_DIR}/build.sh"
USER_MANAGER_START_SCRIPT="${USER_MANAGER_DIR}/start.sh"
USER_MANAGER_STOP_SCRIPT="${USER_MANAGER_DIR}/stop.sh"
RAG_DIR="${ROOT_DIR}/LLM/rag"
RAG_ENTRY="${RAG_DIR}/text_rag_service.py"
VLLM_MODEL_DIR="${RAG_DIR}/models/qwen-colpali/Qwen2.5-VL-3B-Instruct"
CHATBOT_ENTRY="${ROOT_DIR}/LLM/chatbot/qwen2_api.py"
DEFECT_ENTRY="${ROOT_DIR}/LLM/defect/code/web_demo.py"
LITHO_ENTRY="${ROOT_DIR}/LLM/litho/litho.py"
TCAD_ENTRY="${ROOT_DIR}/LLM/tcad_agent_core/web/tcad_web_adapter.py"
CIRCUIT_ENTRY="${ROOT_DIR}/LLM/circuit/circuit.py"

require_siliconflow_key() {
  if [ -n "${SILICONFLOW_API_KEY}" ]; then
    return 0
  fi
  echo "[error] WEB_FABGPT_SILICONFLOW_API_KEY is not set. Please export it before starting SiliconFlow-backed services." >&2
  return 1
}

require_ohmygpt_key() {
  if [ -n "${OHMYGPT_API_KEY}" ]; then
    return 0
  fi
  echo "[error] WEB_FABGPT_OHMYGPT_API_KEY is not set. Please export it before starting ohmygpt-backed services." >&2
  return 1
}

port_open() {
  local port="$1"
  local host="${2:-$HOST}"

  python3 - "$host" "$port" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
try:
    with socket.create_connection((host, port), timeout=2):
        sys.exit(0)
except OSError:
    sys.exit(1)
PY
}

wait_for_port() {
  local port="$1"
  local label="$2"
  local attempts="${3:-30}"
  local host="${4:-$HOST}"
  local i

  for ((i = 1; i <= attempts; i++)); do
    if port_open "$port" "$host"; then
      echo "[ready] ${label} : ${host}:${port}"
      return 0
    fi
    sleep 1
  done

  echo "[error] ${label} did not open port ${host}:${port}" >&2
  return 1
}

wait_for_port_closed() {
  local port="$1"
  local label="$2"
  local attempts="${3:-30}"
  local host="${4:-$HOST}"
  local i

  for ((i = 1; i <= attempts; i++)); do
    if ! port_open "$port" "$host"; then
      echo "[stopped] ${label} : ${host}:${port}"
      return 0
    fi
    sleep 1
  done

  echo "[error] ${label} is still listening on ${host}:${port}" >&2
  return 1
}

tmux_has_session() {
  tmux has-session -t "$1" 2>/dev/null
}

print_simple_status_line() {
  local label="$1"
  local port="$2"
  local host="${3:-$HOST}"
  local state="closed"
  if port_open "$port" "$host"; then
    state="open"
  fi
  printf "%-16s %-8s %s@%s\n" "$label" "$state" "$port" "$host"
}

mysql_is_managed() {
  pgrep -f "${MYSQL_DIR}/bin/mysqld" >/dev/null 2>&1
}

gptserver_is_managed() {
  pgrep -f "gpt-server.jar --server.port=${GPTSERVER_PORT}" >/dev/null 2>&1
}

user_manager_is_managed() {
  pgrep -f "user-management-1.0.0.jar --server.port=${USER_MANAGER_PORT}" >/dev/null 2>&1
}

print_core_status_line() {
  local label="$1"
  local port="$2"
  local checker="$3"
  local host="${4:-$HOST}"
  local state="closed"

  if port_open "$port" "$host"; then
    state="open"
    if ! "$checker"; then
      state="occupied"
    fi
  fi

  printf "%-16s %-8s %s@%s\n" "$label" "$state" "$port" "$host"
}

print_manual_status_line() {
  local session="$1"
  local port="$2"
  local host="${3:-$HOST}"
  local session_state="absent"
  local port_state="closed"

  if tmux_has_session "$session"; then
    session_state="present"
  fi
  if port_open "$port" "$host"; then
    if [ "$session_state" = "present" ]; then
      port_state="open"
    else
      port_state="occupied"
    fi
  fi

  printf "%-16s %-8s %-8s %s@%s\n" "$session" "$session_state" "$port_state" "$port" "$host"
}

require_file() {
  local path="$1"
  if [ ! -f "$path" ]; then
    echo "[error] Missing file: ${path}" >&2
    return 1
  fi
}

require_dir() {
  local path="$1"
  if [ ! -d "$path" ]; then
    echo "[error] Missing directory: ${path}" >&2
    return 1
  fi
}

require_service_layout() {
  case "$1" in
    ${VLLM_SESSION})
      require_dir "$RAG_DIR"
      require_dir "$VLLM_MODEL_DIR"
      ;;
    RAG)
      require_dir "$RAG_DIR"
      require_file "$RAG_ENTRY"
      ;;
    chatbot)
      require_file "$CHATBOT_ENTRY"
      ;;
    defect)
      require_file "$DEFECT_ENTRY"
      ;;
    litho)
      require_file "$LITHO_ENTRY"
      ;;
    TCAD)
      require_dir "${ROOT_DIR}/LLM/tcad_agent_core"
      require_file "$TCAD_ENTRY"
      ;;
    circuit)
      require_file "$CIRCUIT_ENTRY"
      ;;
    *)
      echo "[error] Unknown service layout request: $1" >&2
      return 1
      ;;
  esac
}

run_in_dir() {
  local dir="$1"
  shift
  (
    cd "$dir"
    "$@"
  )
}

start_mysql() {
  echo "[phase] mysql"
  require_file "$MYSQL_START_SCRIPT"
  if port_open "$MYSQL_PORT" "$LOCALHOST"; then
    if mysql_is_managed; then
      echo "[skip] mysql already listening on ${LOCALHOST}:${MYSQL_PORT}"
      return 0
    fi
    echo "[error] ${LOCALHOST}:${MYSQL_PORT} is occupied by an unmanaged MySQL-compatible process." >&2
    return 1
  fi
  run_in_dir "$MYSQL_DIR" env WEB_FABGPT_MYSQL_PORT="${MYSQL_PORT}" bash ./start.sh
  wait_for_port "$MYSQL_PORT" "mysql" 30 "$LOCALHOST"
}

stop_mysql() {
  echo "[phase] mysql"
  require_file "$MYSQL_STOP_SCRIPT"
  if ! port_open "$MYSQL_PORT" "$LOCALHOST"; then
    echo "[skip] mysql is not listening on ${LOCALHOST}:${MYSQL_PORT}"
    return 0
  fi
  if ! mysql_is_managed; then
    echo "[error] ${LOCALHOST}:${MYSQL_PORT} is occupied by an unmanaged MySQL-compatible process." >&2
    return 1
  fi
  run_in_dir "$MYSQL_DIR" env WEB_FABGPT_MYSQL_PORT="${MYSQL_PORT}" bash ./stop.sh
  wait_for_port_closed "$MYSQL_PORT" "mysql" 30 "$LOCALHOST"
}

start_gptserver() {
  echo "[phase] gptserver"
  require_file "$GPTSERVER_START_SCRIPT"
  if port_open "$GPTSERVER_PORT" "$HOST"; then
    if gptserver_is_managed; then
      echo "[skip] gptserver already listening on ${HOST}:${GPTSERVER_PORT}"
      return 0
    fi
    echo "[error] ${HOST}:${GPTSERVER_PORT} is occupied by an unmanaged process." >&2
    return 1
  fi
  run_in_dir "$GPTSERVER_DIR" env \
    WEB_FABGPT_GPTSERVER_PORT="${GPTSERVER_PORT}" \
    WEB_FABGPT_MYSQL_PORT="${MYSQL_PORT}" \
    JAVA_HOME="${JAVA_HOME}" \
    bash ./bin/start.sh
  wait_for_port "$GPTSERVER_PORT" "gptserver" 30 "$HOST"
}

stop_gptserver() {
  echo "[phase] gptserver"
  require_file "$GPTSERVER_STOP_SCRIPT"
  if ! port_open "$GPTSERVER_PORT" "$HOST"; then
    echo "[skip] gptserver is not listening on ${HOST}:${GPTSERVER_PORT}"
    return 0
  fi
  if ! gptserver_is_managed; then
    echo "[error] ${HOST}:${GPTSERVER_PORT} is occupied by an unmanaged process." >&2
    return 1
  fi
  run_in_dir "$GPTSERVER_DIR" env WEB_FABGPT_GPTSERVER_PORT="${GPTSERVER_PORT}" bash ./bin/stop.sh || true
  if port_open "$GPTSERVER_PORT" "$HOST"; then
    pkill -f "gpt-server.jar --server.port=${GPTSERVER_PORT}" || true
    pkill -f "gpt-server.jar" || true
  fi
  wait_for_port_closed "$GPTSERVER_PORT" "gptserver" 30 "$HOST"
}

start_user_manager() {
  echo "[phase] user-manager"
  require_file "$USER_MANAGER_START_SCRIPT"
  if port_open "$USER_MANAGER_PORT" "$HOST"; then
    if user_manager_is_managed; then
      echo "[skip] user-manager already listening on ${HOST}:${USER_MANAGER_PORT}"
      return 0
    fi
    echo "[error] ${HOST}:${USER_MANAGER_PORT} is occupied by an unmanaged process." >&2
    return 1
  fi
  mkdir -p "${USER_MANAGER_DIR}/logs"
  if [ ! -f "${USER_MANAGER_DIR}/target/user-management-1.0.0.jar" ] && [ -f "$USER_MANAGER_BUILD_SCRIPT" ]; then
    run_in_dir "$USER_MANAGER_DIR" bash ./build.sh
  fi
  run_in_dir "$USER_MANAGER_DIR" env \
    WEB_FABGPT_USER_MANAGER_PORT="${USER_MANAGER_PORT}" \
    WEB_FABGPT_MYSQL_PORT="${MYSQL_PORT}" \
    WEB_FABGPT_GPTSERVER_PORT="${GPTSERVER_PORT}" \
    WEB_FABGPT_GPTSERVER_LOGIN_URL="http://${HOST}:${GPTSERVER_PORT}/login" \
    JAVA_HOME="${JAVA_HOME}" \
    bash ./start.sh
  wait_for_port "$USER_MANAGER_PORT" "user-manager" 30 "$HOST"
}

stop_user_manager() {
  echo "[phase] user-manager"
  require_file "$USER_MANAGER_STOP_SCRIPT"
  if ! port_open "$USER_MANAGER_PORT" "$HOST"; then
    echo "[skip] user-manager is not listening on ${HOST}:${USER_MANAGER_PORT}"
    return 0
  fi
  if ! user_manager_is_managed; then
    echo "[error] ${HOST}:${USER_MANAGER_PORT} is occupied by an unmanaged process." >&2
    return 1
  fi
  run_in_dir "$USER_MANAGER_DIR" env WEB_FABGPT_USER_MANAGER_PORT="${USER_MANAGER_PORT}" bash ./stop.sh || true
  if port_open "$USER_MANAGER_PORT" "$HOST"; then
    pkill -f "user-management-1.0.0.jar --server.port=${USER_MANAGER_PORT}" || true
    pkill -f "user-management-1.0.0.jar" || true
  fi
  wait_for_port_closed "$USER_MANAGER_PORT" "user-manager" 30 "$HOST"
}

start_core_stack() {
  start_mysql
  start_gptserver
  start_user_manager
}

stop_core_stack() {
  stop_user_manager
  stop_gptserver
  stop_mysql
}

service_command() {
  case "$1" in
    ${VLLM_SESSION})
      cat <<EOF
cd ${ROOT_DIR} && export WEB_FABGPT_HOST=${HOST} NO_PROXY=127.0.0.1,localhost,${HOST} no_proxy=127.0.0.1,localhost,${HOST} CUDA_VISIBLE_DEVICES=${VLLM_GPU} VLLM_WORKER_MULTIPROC_METHOD=spawn && exec ${CONDA_ENVS_DIR}/circuit/bin/python -m vllm.entrypoints.openai.api_server --host 0.0.0.0 --port ${VLLM_PORT} --model ${VLLM_MODEL_DIR} --served-model-name webfabgpt-vl-3b --api-key webfabgpt-local --gpu-memory-utilization 0.50 --max-model-len 4096 --tensor-parallel-size 1 --dtype bfloat16
EOF
      ;;
    RAG)
      cat <<EOF
cd ${ROOT_DIR}/LLM/rag && export WEB_FABGPT_HOST=${HOST} WEB_FABGPT_BIND_HOST=${HOST} WEB_FABGPT_RAG_PORT=${RAG_PORT} WEB_FABGPT_CHATBOT_PORT=${CHATBOT_PORT} WEB_FABGPT_TCAD_PORT=${TCAD_PORT} NO_PROXY=127.0.0.1,localhost,${HOST} no_proxy=127.0.0.1,localhost,${HOST} CUDA_VISIBLE_DEVICES=${RAG_GPU} && exec ${CONDA_ENVS_DIR}/rag/bin/python text_rag_service.py
EOF
      ;;
    chatbot)
      cat <<EOF
cd ${ROOT_DIR}/LLM/chatbot && export WEB_FABGPT_HOST=${HOST} NO_PROXY=127.0.0.1,localhost,${HOST} no_proxy=127.0.0.1,localhost,${HOST} WEB_FABGPT_CHATBOT_PROVIDER=${CHATBOT_PROVIDER} WEB_FABGPT_CHATBOT_PORT=${CHATBOT_PORT} WEB_FABGPT_TEXT_API_BASE_URL=${TEXT_API_BASE_URL} WEB_FABGPT_CHATBOT_API_URL=${TEXT_API_BASE_URL} WEB_FABGPT_LLM_API_URL=${TEXT_API_BASE_URL} WEB_FABGPT_SILICONFLOW_API_KEY=${SILICONFLOW_API_KEY} WEB_FABGPT_CHATBOT_API_KEY=${SILICONFLOW_API_KEY} WEB_FABGPT_LLM_API_KEY=${SILICONFLOW_API_KEY} WEB_FABGPT_TEXT_MODEL=${TEXT_MODEL} WEB_FABGPT_CHATBOT_MODEL=${TEXT_MODEL} WEB_FABGPT_BACKEND_PORT=${GPTSERVER_PORT} WEB_FABGPT_RAG_PORT=${RAG_PORT} && exec ${CONDA_ENVS_DIR}/chatbot/bin/python qwen2_api.py
EOF
      ;;
    defect)
      cat <<EOF
cd ${ROOT_DIR}/LLM/defect/code && export FABGPT_HOST=0.0.0.0 FABGPT_PORT=${DEFECT_PORT} FABGPT_PUBLIC_BASE_URL=http://${HOST}:${DEFECT_PORT} CUDA_VISIBLE_DEVICES=${DEFECT_GPU} FABGPT_MODEL_PARALLEL=0 FABGPT_MAIN_DEVICE=cuda:0 FABGPT_LLM_DEVICE_MAP=cuda:0 && exec ${CONDA_ENVS_DIR}/defect/bin/python web_demo.py
EOF
      ;;
    litho)
      cat <<EOF
cd ${ROOT_DIR}/LLM/litho && export WEB_FABGPT_HOST=${HOST} WEB_FABGPT_LITHO_PROVIDER=${LITHO_PROVIDER} WEB_FABGPT_LITHO_PORT=${LITHO_PORT} WEB_FABGPT_WEB_PORT=${WEB_PORT} WEB_FABGPT_VL_API_BASE_URL=${VL_API_BASE_URL} WEB_FABGPT_LITHO_LLM_API_URL=${VL_API_BASE_URL} WEB_FABGPT_SILICONFLOW_API_KEY=${SILICONFLOW_API_KEY} WEB_FABGPT_LITHO_LLM_API_KEY=${SILICONFLOW_API_KEY} WEB_FABGPT_VL_MODEL=${VL_MODEL} WEB_FABGPT_LITHO_LLM_MODEL=${VL_MODEL} WEB_FABGPT_LITHO_CPU_WORKERS=${LITHO_CPU_WORKERS} WEB_FABGPT_LITHO_GPU_WORKERS=${LITHO_GPU_WORKERS} CUDA_VISIBLE_DEVICES=${LITHO_GPU} NO_PROXY=127.0.0.1,localhost,${HOST} no_proxy=127.0.0.1,localhost,${HOST} PYTHONPATH=${ROOT_DIR}/LLM/litho/litho_code/thirdparty/OpenILT/thirdparty/adaptive-boxes:${ROOT_DIR}/LLM/litho/litho_code/thirdparty/OpenILT:\${PYTHONPATH:-} && exec ${CONDA_ENVS_DIR}/litho/bin/python litho.py
EOF
      ;;
    TCAD)
      cat <<EOF
cd ${ROOT_DIR}/LLM/tcad_agent_core && export JAVA_HOME=${JAVA_HOME} STROOT=${STROOT} STRELEASE=${STRELEASE} STROOT_LIB=${STROOT_LIB} STDB=${STDB} XLIB_NO_SHM=1 PATH=${JAVA_HOME}/bin:${STROOT}/bin:\$PATH TCAD_GATEWAY_WORKSPACE=${ROOT_DIR}/LLM/tcad_agent_core TCAD_WEB_HOST=0.0.0.0 TCAD_WEB_PORT=${TCAD_PORT} TCAD_LLM_PROVIDER=${TCAD_PROVIDER} TCAD_LLM_BASE_URL=${TCAD_API_BASE_URL} TCAD_WEB_LLM_BASE_URL=${TCAD_API_BASE_URL} TCAD_LLM_API_KEY=${OHMYGPT_API_KEY} TCAD_LLM_MODEL=${TCAD_TEXT_MODEL} TCAD_MODEL_MAIN=${TCAD_TEXT_MODEL} TCAD_MODEL_SDE=${TCAD_TEXT_MODEL} TCAD_MODEL_SDEVICE=${TCAD_TEXT_MODEL} WEB_FABGPT_TCAD_MAX_CONCURRENT=${TCAD_MAX_CONCURRENT} && exec ${CONDA_ENVS_DIR}/tcad/bin/python web/tcad_web_adapter.py
EOF
      ;;
    circuit)
      cat <<EOF
cd ${ROOT_DIR}/LLM/circuit && export WEB_FABGPT_HOST=${HOST} NO_PROXY=127.0.0.1,localhost,${HOST} no_proxy=127.0.0.1,localhost,${HOST} WEB_FABGPT_CIRCUIT_PROVIDER=local WEB_FABGPT_CIRCUIT_PORT=${CIRCUIT_PORT} WEB_FABGPT_BACKEND_PORT=${GPTSERVER_PORT} WEB_FABGPT_CIRCUIT_RUN_MODE=16bit WEB_FABGPT_CIRCUIT_MODEL_PATH=${ROOT_DIR}/LLM/circuit/local_models/global_step_700_actor/huggingface CUDA_VISIBLE_DEVICES=4 && exec ${CONDA_ENVS_DIR}/circuit/bin/python circuit.py
EOF
      ;;
    *)
      echo "[error] Unknown service: $1" >&2
      return 1
      ;;
  esac
}

assert_no_unmanaged_port_owner() {
  local session="$1"
  local port="$2"
  local host="${3:-$HOST}"
  if port_open "$port" "$host" && ! tmux_has_session "$session"; then
    echo "[error] ${host}:${port} is already occupied, but tmux session ${session} does not exist." >&2
    return 1
  fi
}

start_tmux_service() {
  local session="$1"
  local port="$2"
  local attempts="${3:-60}"
  local host="${4:-$HOST}"
  local cmd
  local shell_cmd

  require_service_layout "$session"
  assert_no_unmanaged_port_owner "$session" "$port" "$host"

  if tmux_has_session "$session" && port_open "$port" "$host"; then
    echo "[skip] ${session} already listening on ${host}:${port}"
    return 0
  fi

  cmd="$(service_command "$session")"
  shell_cmd="/bin/bash --noprofile --norc -lc $(printf '%q' "$cmd")"
  if tmux_has_session "$session"; then
    echo "[respawn] ${session}"
    tmux respawn-pane -k -t "${session}:0.0" "$shell_cmd"
  else
    echo "[start] ${session}"
    tmux new-session -d -s "$session" "$shell_cmd"
  fi

  wait_for_port "$port" "$session" "$attempts" "$host"
}

ensure_vllm() {
  start_tmux_service "$VLLM_SESSION" "$VLLM_PORT" 120 "$HOST"
}

warn_if_service_closed() {
  local port="$1"
  local label="$2"
  local host="${3:-$HOST}"
  if ! port_open "$port" "$host"; then
    echo "[warn] ${label} is not listening on ${host}:${port}. Related features may be limited."
  fi
}

require_service_open() {
  local port="$1"
  local label="$2"
  local host="${3:-$HOST}"
  if ! port_open "$port" "$host"; then
    echo "[error] ${label} is not listening on ${host}:${port}." >&2
    return 1
  fi
}

start_llm_service() {
  case "$1" in
    rag)
      start_tmux_service "RAG" "$RAG_PORT" 60 "$HOST"
      ;;
    chatbot)
      require_service_open "$GPTSERVER_PORT" "gptserver" "$HOST"
      warn_if_service_closed "$RAG_PORT" "RAG" "$HOST"
      require_siliconflow_key
      start_tmux_service "chatbot" "$CHATBOT_PORT" 60 "$HOST"
      ;;
    defect)
      start_tmux_service "defect" "$DEFECT_PORT" 180 "$HOST"
      ;;
    litho)
      require_siliconflow_key
      start_tmux_service "litho" "$LITHO_PORT" 60 "$HOST"
      ;;
    tcad)
      require_ohmygpt_key
      start_tmux_service "TCAD" "$TCAD_PORT" 60 "$HOST"
      ;;
    circuit)
      require_service_open "$GPTSERVER_PORT" "gptserver" "$HOST"
      require_siliconflow_key
      start_tmux_service "circuit" "$CIRCUIT_PORT" 60 "$HOST"
      ;;
    *)
      echo "[error] Unsupported LLM service: $1" >&2
      return 1
      ;;
  esac
}
