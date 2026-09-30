#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$ROOT_DIR/.uv-cache}"

if [[ -f "$ROOT_DIR/.env.local" ]]; then
  set -a
  source "$ROOT_DIR/.env.local"
  set +a
fi

if [[ -n "${SEMINAR_CREDENTIAL_ENV:-}" && -f "$SEMINAR_CREDENTIAL_ENV" ]]; then
  set -a
  source "$SEMINAR_CREDENTIAL_ENV"
  set +a
fi

# huggingface_hub builds an httpx client from the proxy environment at
# import time; bracketed IPv6 entries such as "[::1]" in NO_PROXY crash that
# parsing with "Invalid port". Strip bracketed entries for this app only.
sanitize_no_proxy() {
  printf '%s' "$1" | tr ',' '\n' | grep -vE '^\[.*\]$' | paste -sd, -
}
if [[ -n "${NO_PROXY:-}" ]]; then
  export NO_PROXY="$(sanitize_no_proxy "$NO_PROXY")"
fi
if [[ -n "${no_proxy:-}" ]]; then
  export no_proxy="$(sanitize_no_proxy "$no_proxy")"
fi

cleanup() {
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

(
  cd "$ROOT_DIR/backend"
  uv run uvicorn app.main:app --host 127.0.0.1 --port 8765
) &
BACKEND_PID=$!

(
  cd "$ROOT_DIR/frontend"
  npm run dev -- --host 127.0.0.1 --port 5173
) &
FRONTEND_PID=$!

printf '\nSeminar Copilot: http://127.0.0.1:5173\n'
printf 'Press Ctrl+C to stop both services.\n\n'
while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 1
done
