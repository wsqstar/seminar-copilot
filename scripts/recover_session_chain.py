#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import wave
from datetime import datetime
from pathlib import Path


STATUS_ORDER = {"unanswered": 0, "mention": 1, "partial": 2, "answered": 3}
SAMPLE_RATE = 16_000


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def session_start(path: Path) -> datetime:
    match = re.search(r"-(\d{8}-\d{6})-[0-9a-f]+$", path.name)
    if match is None:
        raise ValueError(f"Cannot parse session timestamp from {path.name}")
    return datetime.strptime(match.group(1), "%Y%m%d-%H%M%S")


def pcm_duration(path: Path) -> float:
    return path.stat().st_size / (SAMPLE_RATE * 2)


def write_wav(pcm_path: Path, wav_path: Path) -> None:
    with wave.open(str(wav_path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(SAMPLE_RATE)
        target.writeframes(pcm_path.read_bytes())


def best_question_states(analysis_rows: list[dict]) -> dict[str, dict]:
    best: dict[str, dict] = {}
    for row in analysis_rows:
        for update in row.get("updates", []):
            question_id = str(update.get("question_id", ""))
            status = str(update.get("status", "unanswered"))
            if not question_id or status not in STATUS_ORDER:
                continue
            previous = best.get(question_id)
            if previous is None or STATUS_ORDER[status] >= STATUS_ORDER[previous["status"]]:
                best[question_id] = update
    return best


def atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Attach an interrupted seminar session to its continuation.")
    parser.add_argument("--previous", required=True, type=Path)
    parser.add_argument("--current", required=True, type=Path)
    args = parser.parse_args()

    previous = args.previous.resolve()
    current = args.current.resolve()
    previous_manifest = json.loads((previous / "manifest.json").read_text(encoding="utf-8"))
    current_manifest = json.loads((current / "manifest.json").read_text(encoding="utf-8"))
    if previous_manifest["preset_id"] != current_manifest["preset_id"]:
        raise SystemExit("Refusing to merge sessions from different seminar presets")

    recovery_dir = current / "recovered_previous" / previous.name
    recovery_dir.mkdir(parents=True, exist_ok=True)
    for name in ("manifest.json", "audio.pcm", "transcript.jsonl", "analysis.jsonl"):
        source = previous / name
        if source.exists():
            shutil.copy2(source, recovery_dir / name)

    recovered_wav = recovery_dir / "recovered.wav"
    write_wav(previous / "audio.pcm", recovered_wav)

    duration = pcm_duration(previous / "audio.pcm")
    previous_start = session_start(previous)
    current_start = session_start(current)
    estimated_gap = max(0.0, (current_start - previous_start).total_seconds() - duration)
    transcript_rows = read_jsonl(previous / "transcript.jsonl")
    analysis_rows = read_jsonl(previous / "analysis.jsonl")
    best_states = best_question_states(analysis_rows)

    payload = {
        "status": "recovered_previous_session_attached",
        "previous_session": previous.name,
        "current_session": current.name,
        "previous_audio_seconds": round(duration, 3),
        "estimated_gap_seconds": round(estimated_gap, 3),
        "previous_transcript_segments": len(transcript_rows),
        "previous_ai_analysis_runs": len(analysis_rows),
        "previous_best_question_states": best_states,
        "recovery_directory": str(recovery_dir),
        "current_recording_untouched": True,
    }
    atomic_json(current / "recovery_manifest.json", payload)

    lines = [
        "# Interrupted-session recovery",
        "",
        f"- Previous session: `{previous.name}`",
        f"- Current continuation: `{current.name}`",
        f"- Recovered audio: {duration:.3f} seconds",
        f"- Estimated restart gap: {estimated_gap:.3f} seconds",
        f"- Recovered transcript: {len(transcript_rows)} stable segments",
        f"- Recovered AI judgments: {len(analysis_rows)} runs",
        "- Current recording files were not modified.",
        "",
        "## Best attained question states before restart",
        "",
    ]
    for question_id, state in best_states.items():
        lines.extend(
            [
                f"### {question_id}",
                f"- Status: `{state.get('status', 'unanswered')}`",
                f"- Answer: {state.get('answer') or 'No answer recorded.'}",
                f"- Evidence: {state.get('evidence_quote') or 'No grounded quote recorded.'}",
                f"- Missing: {'; '.join(state.get('missing', [])) or 'None'}",
                "",
            ]
        )
    (current / "RECOVERY.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
