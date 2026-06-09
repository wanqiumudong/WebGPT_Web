#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${1:-}" == "--check" ]]; then
  command -v python3 >/dev/null
  test -f "$ROOT_DIR/mcp/stdio_server.py"
  echo "MCP 启动检查通过: $ROOT_DIR/mcp/stdio_server.py"
  exit 0
fi

# Optional: load user shell env for Sentaurus binary PATH.
if [[ -f "$HOME/.bashrc" ]]; then
  # shellcheck disable=SC1090
  source "$HOME/.bashrc" || true
fi

exec python3 "$ROOT_DIR/mcp/stdio_server.py"
