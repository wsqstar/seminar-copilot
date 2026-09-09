from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .models import RecoveredSessionState, TranscriptSegment


SESSION_TIME = re.compile(r"-(\d{8}-\d{6})-[a-z0-9]+$")


@dataclass
class RecoverySource:
    root: Path
    session_id: str
    started_at: datetime
    audio_seconds: float
    transcript: list[TranscriptSegment]
    analysis_rows: list[dict]
    temporary_question_rows: list[dict]


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _session_start(session_id: str) -> datetime | None:
    match = SESSION_TIME.search(session_id)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d-%H%M%S")
    except ValueError:
        return None


def find_recovery_sources(
    root: Path,
    preset_id: str,
    now: datetime,
    *,
    max_age: timedelta = timedelta(hours=2),
    max_gap: timedelta = timedelta(minutes=15),
) -> list[RecoverySource]:
    candidates: list[RecoverySource] = []
    for session_root in root.glob(f"{preset_id}-*"):
        manifest_path = session_root / "manifest.json"
        transcript_path = session_root / "transcript.jsonl"
        raw_path = session_root / "audio.pcm"
        if not manifest_path.exists() or not transcript_path.exists() or not raw_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if manifest.get("preset_id") != preset_id or manifest.get("status") != "recording":
            continue
        session_id = str(manifest.get("id") or session_root.name)
        started_at = _session_start(session_id)
        if started_at is None or now - started_at > max_age or started_at >= now:
            continue
        sample_rate = int(manifest.get("sample_rate") or 16_000)
        audio_seconds = raw_path.stat().st_size / (2 * sample_rate)
        transcript_rows = _read_jsonl(transcript_path)
        if audio_seconds < 8 or not transcript_rows:
            continue
        try:
            transcript = [TranscriptSegment.model_validate(row) for row in transcript_rows]
        except Exception:
            continue
        candidates.append(
            RecoverySource(
                root=session_root,
                session_id=session_id,
                started_at=started_at,
                audio_seconds=audio_seconds,
                transcript=transcript,
                analysis_rows=_read_jsonl(session_root / "analysis.jsonl"),
                temporary_question_rows=_read_jsonl(
                    session_root / "temporary_questions.jsonl"
                ),
            )
        )

    candidates.sort(key=lambda item: item.started_at)
    if not candidates:
        return []

    chain = [candidates[-1]]
    for candidate in reversed(candidates[:-1]):
        next_source = chain[0]
        candidate_end = candidate.started_at + timedelta(seconds=candidate.audio_seconds)
        gap = next_source.started_at - candidate_end
        if gap > max_gap:
            break
        chain.insert(0, candidate)

    latest_end = chain[-1].started_at + timedelta(seconds=chain[-1].audio_seconds)
    if now - latest_end > max_gap:
        return []
    return chain


def build_recovered_states(
    sources: list[RecoverySource], current_started_at: datetime
) -> tuple[list[RecoveredSessionState], float]:
    states: list[RecoveredSessionState] = []
    offset = 0.0
    for index, source in enumerate(sources):
        next_start = (
            sources[index + 1].started_at
            if index + 1 < len(sources)
            else current_started_at
        )
        source_end = source.started_at + timedelta(seconds=source.audio_seconds)
        gap = max(0.0, (next_start - source_end).total_seconds())
        states.append(
            RecoveredSessionState(
                session_id=source.session_id,
                started_at=source.started_at.isoformat(timespec="seconds"),
                audio_seconds=round(source.audio_seconds, 3),
                timeline_offset_seconds=round(offset, 3),
                gap_after_seconds=round(gap, 3),
                transcript=source.transcript,
                ai_analysis_runs=len(source.analysis_rows),
            )
        )
        offset += source.audio_seconds + gap
    return states, round(offset, 3)
