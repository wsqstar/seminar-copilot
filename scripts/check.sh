#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$ROOT_DIR/.uv-cache}"

(
  cd "$ROOT_DIR/backend"
  uv run pytest -q
)

(
  cd "$ROOT_DIR/frontend"
  npm run build
)
