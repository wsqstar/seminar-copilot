#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$ROOT_DIR/.uv-cache}"

# 以「补默认」的方式加载 .env.local：进程环境变量（如 DSH 插件注入的
# SEMINAR_ASR_BACKEND / DASHSCOPE_API_KEY）优先，文件只补齐缺失的键，
# 绝不覆盖已设置的值。
load_env_defaults() {
  local file="$1" line key value
  [[ -f "$file" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    # 已在进程环境中设置的键保持原值，文件不覆盖。
    if [[ -n "${!key+x}" ]]; then continue; fi
    value="${line#*=}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    if [[ ( "$value" == \"*\" || "$value" == '*' ) && ${#value} -ge 2 ]]; then
      value="${value:1:${#value}-2}"
    fi
    export "$key=$value"
  done < "$file"
}

load_env_defaults "$ROOT_DIR/.env.local"

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
