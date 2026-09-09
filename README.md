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

## Historical projects and continued recording

Open `历史项目` from the setup screen to revisit each complete seminar. A
project groups every recording phase for the same seminar preset and presents:

- playable local audio for each phase, including byte-range seeking;
- one combined transcript timeline with restart gaps kept explicit;
- the best persisted AI and local answer judgment for each prepared or
  temporary question;
- timestamped manual notes stored with the project.

`继续录音` creates a new phase in the selected project and restores the earlier
transcript and question state into the live workbench. It never overwrites an
earlier audio file. The microphone must be detected and explicitly selected
again before continuation, which prevents silently falling back to the wrong
input device.

## Privacy boundary

- Recording and Whisper transcription stay local.
- Raw audio and session state are stored under `data/sessions/` by default and
  are ignored by Git.
- Project notes are append-only JSONL under `data/projects/<project-id>/` and
  are also ignored by Git.
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
SEMINAR_CREDENTIAL_ENV=/absolute/path/to/private-credentials.env
```

`.env.local` is ignored by Git so local paths and service configuration remain
on the machine.

## Live temporary questions

During recording, use `临时问题` to save a rough question or ask the assistant
to identify a missing question. The request is persisted before enrichment,
then processed asynchronously:

1. Rewrite it as equivalent Chinese and English spoken questions.
2. Search the complete recovered/current transcript for prior answers while
   keeping the AI formulation prompt bounded to recent context.
3. Optionally search OpenAlex and Crossref for citable academic context.
4. Add the question to the same rolling answer-coverage analysis as preset
   questions.

The live card separates timestamped lecture evidence from external literature
candidates. No result is preferable to showing a weak bibliographic match.
Question lifecycle events and sourced results are written to
`temporary_questions.jsonl` in the session directory. Markdown export includes
the bilingual wording, creation timestamp, search status, sources, and later
answer evidence. Google Scholar is not scraped.

## Checks

```bash
cd backend && uv run pytest
cd frontend && npm run build
```

The optional `e2e/smoke.cjs` test expects both services to be running with demo
mode enabled and a locally installed Playwright package.
