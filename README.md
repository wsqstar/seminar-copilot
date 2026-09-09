# Seminar Copilot

Local-first seminar recorder and question tracker. It records microphone PCM,
transcribes rolling audio with MLX Whisper, links timestamped evidence to a
prepared question list, and optionally asks DeepSeek Harness for structured
answer-coverage judgments.

## Start here

Read [HANDOFF.md](HANDOFF.md) before changing the project.

## Requirements

- macOS on Apple silicon
- `ffmpeg`
- Node.js 20+
- `uv`
- Optional: `dsh --profile headless` configured for DeepSeek analysis

## Install

```bash
cd backend
uv sync --extra dev

cd ../frontend
npm install
```

## Run

For the normal local workflow, create `.env.local` from `.env.example`, then:

```bash
./scripts/dev.sh
```

Open <http://127.0.0.1:5173>. The launcher still runs the frontend and backend
as separate processes and stops both with `Ctrl+C`.

Manual startup remains available for debugging.

Terminal 1:

```bash
cd backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Terminal 2:

```bash
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

Open <http://127.0.0.1:5173>. The Vite server proxies `/api` and `/ws` to the
loopback backend.

The setup screen stays read-only until Whisper finishes its one-time warmup.
For a room lecture, select the laptop microphone. For an online meeting, select
a legal system-audio loopback input such as a preconfigured BlackHole device;
the browser cannot capture speaker output through the microphone API alone.

## Privacy boundary

- Recording and Whisper transcription stay local.
- Raw audio and session state are stored under `data/sessions/` by default and
  are ignored by Git.
- DeepSeek analysis is off by default. Enabling it sends only the rolling
  stable transcript and question state to the configured model provider.
- The app suggests questions but never sends messages or plays speech on the
  user's behalf.
- Confirm that recording is permitted before starting.

## Environment

```bash
SEMINAR_DATA_DIR=/absolute/path/to/session-data
SEMINAR_WHISPER_MODEL=mlx-community/whisper-large-v3-turbo
SEMINAR_PREWARM_WHISPER=1
SEMINAR_DSH_BIN=/opt/homebrew/bin/dsh
SEMINAR_OBSIDIAN_EXPORT_DIR=/absolute/path/to/obsidian/folder
SEMINAR_ENABLE_DEMO=1
```

`.env.local` is ignored by Git so local paths and service configuration remain
on the machine.

## Checks

```bash
cd backend && uv run pytest
cd frontend && npm run build
```

The optional `e2e/smoke.cjs` test expects both services to be running with demo
mode enabled and a locally installed Playwright package.
