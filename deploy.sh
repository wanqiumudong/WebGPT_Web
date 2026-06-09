#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${ROOT_DIR}/scripts/common.sh"

usage() {
  cat <<'EOF'
Usage:
  bash deploy.sh start
  bash deploy.sh status
  bash deploy.sh stop

Scope:
  - start/stop only manage mysql, gptserver, user-manager
  - front-end is manual: cd Web/front-end/Code && npm start
  - LLM services are manual: bash scripts/llm/start_<service>.sh

Recommended manual order:
  1. bash deploy.sh start
  2. cd Web/front-end/Code && npm start
  3. bash scripts/llm/start_rag.sh
  4. bash scripts/llm/start_chatbot.sh
  5. bash scripts/llm/start_defect.sh
  6. bash scripts/llm/start_litho.sh
  7. bash scripts/llm/start_tcad.sh
  8. bash scripts/llm/start_circuit.sh
EOF
}

status() {
  echo "core backend"
  printf "%-16s %-8s %s\n" "service" "status" "port"
  printf "%-16s %-8s %s\n" "-------" "------" "----"
  print_core_status_line "mysql" "${MYSQL_PORT}" mysql_is_managed "${LOCALHOST}"
  print_core_status_line "gptserver" "${GPTSERVER_PORT}" gptserver_is_managed "${HOST}"
  print_core_status_line "user-manager" "${USER_MANAGER_PORT}" user_manager_is_managed "${HOST}"
  echo
  echo "front-end"
  printf "%-16s %-8s %s\n" "service" "status" "port"
  printf "%-16s %-8s %s\n" "-------" "------" "----"
  print_simple_status_line "web" "${WEB_PORT}" "${HOST}"
  echo
  echo "manual llm services"
  printf "%-16s %-8s %-8s %s\n" "service" "tmux" "status" "port"
  printf "%-16s %-8s %-8s %s\n" "-------" "----" "------" "----"
  print_manual_status_line "RAG" "${RAG_PORT}"
  print_manual_status_line "chatbot" "${CHATBOT_PORT}"
  print_manual_status_line "defect" "${DEFECT_PORT}"
  print_manual_status_line "litho" "${LITHO_PORT}"
  print_manual_status_line "TCAD" "${TCAD_PORT}"
  print_manual_status_line "circuit" "${CIRCUIT_PORT}"
}

cmd="${1:-}"

case "${cmd}" in
  start)
    start_core_stack
    ;;
  status)
    status
    ;;
  stop)
    stop_core_stack
    ;;
  ""|help|-h|--help)
    usage
    ;;
  *)
    echo "Unknown command: ${cmd}" >&2
    usage
    exit 1
    ;;
esac
