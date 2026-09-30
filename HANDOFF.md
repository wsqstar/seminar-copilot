# Project Handoff

Updated: 2026-09-09

## Current goal

Deliver a same-day local web workbench that records a seminar, produces live
timestamped transcripts, tracks whether prepared questions have been answered,
and exports an evidence-linked Markdown note.

## Accepted scope

- React/Vite frontend and FastAPI backend.
- Browser microphone capture as 16 kHz PCM.
- Thirty-second rolling MLX Whisper transcription with five-second updates.
- Local keyword matching on committed transcript segments.
- Optional DeepSeek Harness analysis over a rolling 120-second window.
- One in-flight deep analysis; stale pending requests are not queued.
- Session WAV, JSONL transcript, question state, and Markdown export.
- Crash recovery discovers recent same-preset orphan sessions, rebuilds prior
  question judgments, and presents a continuous timeline with explicit gaps.
- Temporary questions are persisted immediately, rewritten as bilingual spoken
  questions, checked against the full transcript, and optionally supported by
  OpenAlex/Crossref results.
- Historical project management groups all recording phases for one seminar,
  exposes audio/transcript/question/note views, and can continue an interrupted
  or completed project without overwriting prior recordings.
- Manual notes are append-only project records and can be timestamped from the
  live recording or added during later review.
- Paste-based intake: pasting a seminar announcement parses the speaker, title,
  and date (dsh with a heuristic fallback), scores relevance against
  `backend/config/research_profile.md`, searches OpenAlex/Crossref for the
  speaker and topic, proposes reviewable questions, then writes a preset JSON,
  a dossier under `data/dossiers/<preset_id>/`, and reloads presets without a
  restart. Nothing is written until the user confirms the review screen.
- Whisper language is per-preset (`language`, default `"auto"` = whisper
  detects each window; pin e.g. `"en"`/`"zh"` if needed), overridable via
  `SEMINAR_WHISPER_LANGUAGE`. Keep preset glossaries to real domain terms:
  junk notice-title tokens in the initial prompt cause hallucination loops.
  Intake topic keywords (paste → parse → confirm) are now filtered by
  `_clean_glossary` in `backend/app/intake.py` — notice boilerplate
  (讲座预告/图片/报名…) and >12-char Chinese fragments are dropped before the
  preset is written, so select-all pastes no longer pollute the glossary.
- Auto-generated follow-up questions are behind the explicit
  `auto_questions_enabled` switch (start/continue panels); suggestions appear
  in the follow-up bar and can be clicked to adopt as tracked questions. The
  backend must run outside the workspace file sandbox (dsh headless writes
  ~/.dsh) or AI ticks fail with EPERM.

## Deferred

- Speaker diarization: unnecessary for the single-speaker lecture MVP.
- Broad web research: live retrieval is intentionally limited to auditable
  OpenAlex/Crossref bibliographic candidates.
- Automatic dossier edits: live ASR is provisional and requires review.
- Automatic spoken or chat questions: external communication remains manual.

## Verified state

- [done] Backend tests pass without loading Metal or contacting an external
  model: temporary-question persistence, full-transcript search, and source
  relevance filtering are covered on 2026-09-09.
- [done] Frontend production build succeeds on 2026-09-09.
- [done] Playwright smoke test covers preset loading, demo state, stop/export,
  console errors, horizontal overflow at 1440 px and 390 px, and a fake-device
  microphone flow through AudioWorklet, WebSocket, stop, and valid 16 kHz WAV.
- [done] Cached `whisper-small-mlx` transcribed a real 30-second audio sample;
  the warm run took 0.595 seconds on the local M5 Mac.
- [done] DeepSeek Harness integration returned grounded structured states for
  the three-segment demo: three partial and three unanswered questions, plus
  two follow-up candidates.
- [done] A restart-recovery path preserves prior transcript and AI JSONL,
  prevents question-state downgrades, and includes recovered phases in web and
  Markdown output.
- [done] Temporary-question events are written before asynchronous enrichment;
  local transcript evidence and relevant OpenAlex/Crossref results remain
  distinguished in storage and the UI.
- [done] Historical project reconstruction, cross-phase question aggregation,
  append-only notes, continuation source loading, and seekable WAV streaming
  are covered by backend tests on 2026-09-09.
- [done] The history UI builds with project selection, four evidence views,
  explicit microphone selection for continuation, and live timestamped notes
  on 2026-09-09.
- [done] Intake tests cover heuristic parsing, keyword relevance, unique and
  safe preset ids, preset+dossier persistence, and preset reload; the
  frontend production build succeeds with the intake screen on 2026-09-09.

## Next checkpoint

- Run `./scripts/dev.sh` at least two minutes before the lecture so the selected
  Whisper model can load and compile.
- Confirm the intended input device with the refresh button and make a short
  test recording before the speaker starts.
