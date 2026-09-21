#!/usr/bin/env bash
#
# Real-LLM smoke for the CLI and the plugin lifecycle.
#
# Scope note: this script pipes commands into stdin, so sys.stdin.isatty() is
# false and InteractiveCLIRunner takes its input() fallback. That means the
# slash-command palette never renders here and this script cannot catch a
# regression in it. Use scripts/cli_palette_smoke.py for that -- it allocates
# a pty so the prompt_toolkit session is the one under test.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PYTHON=${PYTHON:-"$ROOT/.venv/bin/python"}
CLI=${CLI:-"$ROOT/.venv/bin/langharness"}
SMOKE_DIR=${SMOKE_DIR:-"$ROOT/.tmp-cli-real-smoke"}
SERVER_PORT=${SERVER_PORT:-11534}
PROVIDER=${PROVIDER:-}

if [[ -z "$PROVIDER" ]]; then
  echo "PROVIDER is required for the real LLM portion." >&2
  exit 2
fi

export LANG_HARNESS_DIR="$SMOKE_DIR"

echo "== dynamic plugin lifecycle =="
"$CLI" --dir "$SMOKE_DIR" plugins discover \
  --server-port "$SERVER_PORT" --token secret
"$CLI" --dir "$SMOKE_DIR" plugins install real.echo echo \
  --scope server --server-port "$SERVER_PORT" --token secret
"$CLI" --dir "$SMOKE_DIR" plugins disable real-echo \
  --scope server --server-port "$SERVER_PORT" --token secret
"$CLI" --dir "$SMOKE_DIR" plugins enable real-echo \
  --scope server --server-port "$SERVER_PORT" --token secret
"$CLI" --dir "$SMOKE_DIR" plugins uninstall real-echo \
  --scope server --server-port "$SERVER_PORT" --token secret

echo "== real LLM interactive flow =="
printf '%s\n' \
  'remember smoke-alpha' \
  'what did I just say?' \
  '/agents' \
  '/agent simple_agent' \
  'what did I just say?' \
  '/exit' |
  "$CLI" --dir "$SMOKE_DIR" --provider "$PROVIDER" interactive \
    --server-port "$SERVER_PORT" \
    --token secret \
    --user-id smoke_user \
    --agent-id simple_agent
