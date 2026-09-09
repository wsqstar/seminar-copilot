from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
import wave
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import WebSocket

from .analyzer import STATUS_ORDER, DeepSeekAnalyzer, keyword_hits, quote_is_grounded
from .asr import WhisperTranscriber
from .models import (
    Evidence,
    QuestionState,
    SeminarPreset,
    SessionSnapshot,
    TranscriptSegment,
)


SAMPLE_RATE = 16_000
ASR_WINDOW_SECONDS = 30.0
ASR_HOP_SECONDS = 5.0
ASR_STABILITY_SECONDS = 5.0
AI_WINDOW_SECONDS = 120.0
AI_HOP_SECONDS = 60.0


class SeminarSession:
    def __init__(
        self,
        preset: SeminarPreset,
        root: Path,
        transcriber: WhisperTranscriber,
        analyzer: DeepSeekAnalyzer,
        external_ai_enabled: bool,
    ) -> None:
        self.started_at = datetime.now()
        stamp = self.started_at.strftime("%Y%m%d-%H%M%S")
        self.id = f"{preset.id}-{stamp}-{uuid.uuid4().hex[:6]}"
        self.preset = preset
        self.root = root / self.id
        self.root.mkdir(parents=True, exist_ok=False)
        self.transcriber = transcriber
        self.analyzer = analyzer
        self.external_ai_enabled = external_ai_enabled
        self.status = "recording"
        self.samples = np.empty(0, dtype=np.float32)
        self.buffer_start_seconds = 0.0
        self.total_samples = 0
        self.committed_until = 0.0
        self.last_asr_at = 0.0
        self.last_ai_at = 0.0
        self.asr_state = "等待语音"
        self.analyzer_state = "外部分析已开启" if external_ai_enabled else "本地匹配"
        self.transcript: list[TranscriptSegment] = []
        self.provisional_text = ""
        self.questions = [
            QuestionState(
                id=q.id,
                question=q.question,
                why_it_matters=q.why_it_matters,
                expected_slots=q.expected_slots,
                missing=list(q.expected_slots),
            )
            for q in preset.questions
        ]
        self.followups: list[str] = []
        self.last_error = ""
        self.export_path: str | None = None
        self.sockets: set[WebSocket] = set()
        self._asr_busy = False
        self._ai_busy = False
        self._stop_event = asyncio.Event()
        self._loop_task: asyncio.Task[Any] | None = None
        self._raw_path = self.root / "audio.pcm"
        self._transcript_path = self.root / "transcript.jsonl"
        self._analysis_path = self.root / "analysis.jsonl"
        self._raw_file = self._raw_path.open("ab", buffering=0)
        self._write_manifest()

    @property
    def elapsed_seconds(self) -> float:
        return self.total_samples / SAMPLE_RATE

    def start_background_loop(self) -> None:
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self._run_loop())

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if (
                    self.elapsed_seconds >= 8
                    and self.elapsed_seconds - self.last_asr_at >= ASR_HOP_SECONDS
                    and not self._asr_busy
                ):
                    await self.transcribe_tick()
                if (
                    self.external_ai_enabled
                    and self.transcript
                    and self.elapsed_seconds - self.last_ai_at >= AI_HOP_SECONDS
                    and not self._ai_busy
                ):
                    asyncio.create_task(self.deep_analyze())
            except Exception as exc:
                self.last_error = str(exc)
                self.asr_state = "转录异常，可继续录音"
                await self.broadcast()
            await asyncio.sleep(0.25)

    def append_pcm(self, payload: bytes) -> None:
        if self.status != "recording" or len(payload) % 2:
            return
        pcm = np.frombuffer(payload, dtype="<i2")
        if pcm.size == 0:
            return
        self._raw_file.write(payload)
        normalized = pcm.astype(np.float32) / 32768.0
        self.samples = np.concatenate((self.samples, normalized))
        self.total_samples += pcm.size
        max_buffer = int(SAMPLE_RATE * 180)
        if self.samples.size > max_buffer:
            trim = self.samples.size - max_buffer
            self.samples = self.samples[trim:]
            self.buffer_start_seconds += trim / SAMPLE_RATE

    async def transcribe_tick(self, final: bool = False) -> None:
        if self._asr_busy or self.samples.size == 0:
            return
        self._asr_busy = True
        self.asr_state = "正在转录"
        await self.broadcast()
        try:
            window_samples = int(SAMPLE_RATE * ASR_WINDOW_SECONDS)
            audio = self.samples[-window_samples:].copy()
            window_start = self.elapsed_seconds - audio.size / SAMPLE_RATE
            prompt = ", ".join(self.preset.glossary)
            segments = await asyncio.to_thread(self.transcriber.transcribe, audio, prompt)
            stable_before = self.elapsed_seconds if final else self.elapsed_seconds - ASR_STABILITY_SECONDS
            provisional: list[str] = []
            committed_any = False
            for item in segments:
                start = max(0.0, window_start + item.start)
                end = max(start, window_start + item.end)
                if end <= stable_before + 0.01 and end > self.committed_until + 0.2:
                    segment = TranscriptSegment(
                        id=f"seg-{len(self.transcript) + 1:05d}",
                        start=round(start, 2),
                        end=round(end, 2),
                        text=item.text,
                    )
                    if not self._is_duplicate(segment.text):
                        self.transcript.append(segment)
                        self._append_jsonl(self._transcript_path, segment.model_dump())
                        self._apply_local_matches(segment)
                        committed_any = True
                    self.committed_until = max(self.committed_until, end)
                elif end > stable_before:
                    provisional.append(item.text)
            self.provisional_text = " ".join(provisional).strip()
            self.last_asr_at = self.elapsed_seconds
            self.asr_state = "字幕已更新" if committed_any else "等待稳定句子"
            self.last_error = ""
        except Exception as exc:
            self.last_error = f"ASR: {exc}"
            self.asr_state = "转录异常，可继续录音"
        finally:
            self._asr_busy = False
            await self.broadcast()

    def _is_duplicate(self, text: str) -> bool:
        normalized = re.sub(r"\W+", " ", text.lower()).strip()
        return any(
            re.sub(r"\W+", " ", item.text.lower()).strip() == normalized
            for item in self.transcript[-3:]
        )

    def _apply_local_matches(self, segment: TranscriptSegment) -> None:
        definitions = {item.id: item for item in self.preset.questions}
        for state in self.questions:
            hits = keyword_hits(definitions[state.id], segment.text)
            if not hits:
                continue
            evidence = Evidence(
                segment_id=segment.id,
                start=segment.start,
                end=segment.end,
                quote=segment.text,
                relation="keyword_match",
                confidence=min(0.65, 0.3 + 0.1 * len(hits)),
            )
            if all(item.segment_id != segment.id for item in state.evidence):
                state.evidence.append(evidence)
                state.evidence = state.evidence[-5:]
            if state.status == "unanswered":
                state.status = "mention"
                state.confidence = evidence.confidence

    async def deep_analyze(self) -> None:
        if self._ai_busy or not self.external_ai_enabled or not self.transcript:
            return
        self._ai_busy = True
        self.analyzer_state = "DeepSeek 正在判断"
        self.last_ai_at = self.elapsed_seconds
        await self.broadcast()
        try:
            cutoff = max(0.0, self.elapsed_seconds - AI_WINDOW_SECONDS)
            window_segments = [segment for segment in self.transcript if segment.end >= cutoff]
            transcript_text = "\n".join(
                f"[{format_seconds(segment.start)}-{format_seconds(segment.end)}] {segment.text}"
                for segment in window_segments
            )
            result = await self.analyzer.analyze(
                transcript_text,
                self.questions,
                str(self.root),
            )
            self._apply_deep_updates(result.updates, transcript_text, window_segments)
            self.followups = result.followups
            self._append_jsonl(
                self._analysis_path,
                {
                    "at_audio_second": round(self.elapsed_seconds, 2),
                    "window_start": round(cutoff, 2),
                    "updates": result.updates,
                    "followups": result.followups,
                },
            )
            self.analyzer_state = "问题状态已更新"
            self.last_error = ""
        except Exception as exc:
            self.last_error = f"AI: {exc}"
            self.analyzer_state = "DeepSeek 暂不可用，本地匹配继续"
        finally:
            self._ai_busy = False
            await self.broadcast()

    def _apply_deep_updates(
        self,
        updates: list[dict],
        transcript_text: str,
        window_segments: list[TranscriptSegment],
    ) -> None:
        states = {state.id: state for state in self.questions}
        for item in updates:
            state = states.get(str(item.get("question_id", "")))
            status = str(item.get("status", "unanswered"))
            if state is None or status not in STATUS_ORDER:
                continue
            quote = str(item.get("evidence_quote", "")).strip()
            grounded = quote_is_grounded(quote, transcript_text)
            if status == "answered" and not grounded:
                status = "partial"
            if STATUS_ORDER[status] < STATUS_ORDER[state.status] and status != "unanswered":
                continue
            state.status = status  # type: ignore[assignment]
            state.answer = str(item.get("answer", "")).strip()
            missing = item.get("missing", [])
            state.missing = [str(value) for value in (missing if isinstance(missing, list) else [missing]) if str(value).strip()]
            try:
                confidence = float(item.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            state.confidence = max(0.0, min(1.0, confidence if grounded else min(confidence, 0.4)))
            if grounded:
                source = next(
                    (segment for segment in window_segments if quote_is_grounded(quote, segment.text)),
                    window_segments[-1] if window_segments else None,
                )
                if source and all(e.quote != quote for e in state.evidence):
                    state.evidence.append(
                        Evidence(
                            segment_id=source.id,
                            start=source.start,
                            end=source.end,
                            quote=quote,
                            relation="direct_answer" if status in {"partial", "answered"} else "background",
                            confidence=state.confidence,
                        )
                    )
                    state.evidence = state.evidence[-5:]

    async def stop(self) -> None:
        if self.status == "stopped":
            return
        self.status = "stopping"
        await self.broadcast()
        self._stop_event.set()
        if self._loop_task:
            await self._loop_task
        await self.transcribe_tick(final=True)
        self._raw_file.close()
        self._write_wav()
        self.status = "stopped"
        self.asr_state = "录音已停止"
        self._write_manifest()
        await self.broadcast()

    def add_demo_content(self) -> None:
        examples = [
            (12.0, 28.0, "We compare siblings within the same family, which absorbs shared early-life family background."),
            (31.0, 54.0, "Most of the migrant mortality advantage is attributable to selection rather than the destination place itself."),
            (58.0, 84.0, "We estimate origin and destination county effects jointly, and the destination accounts for the larger share."),
        ]
        for start, end, text in examples:
            segment = TranscriptSegment(
                id=f"seg-{len(self.transcript) + 1:05d}", start=start, end=end, text=text
            )
            self.transcript.append(segment)
            self.committed_until = end
            self._apply_local_matches(segment)
        self.total_samples = int(90 * SAMPLE_RATE)
        self.provisional_text = "Longer-distance moves show a larger advantage, but the mechanism..."
        self.asr_state = "演示字幕已载入"

    def snapshot(self) -> SessionSnapshot:
        return SessionSnapshot(
            id=self.id,
            status=self.status,
            preset=self.preset,
            elapsed_seconds=round(self.elapsed_seconds, 2),
            committed_until=round(self.committed_until, 2),
            asr_state=self.asr_state,
            analyzer_state=self.analyzer_state,
            external_ai_enabled=self.external_ai_enabled,
            transcript=self.transcript,
            provisional_text=self.provisional_text,
            questions=self.questions,
            followups=self.followups,
            last_error=self.last_error,
            export_path=self.export_path,
        )

    async def broadcast(self) -> None:
        if not self.sockets:
            return
        payload = self.snapshot().model_dump_json()
        stale: list[WebSocket] = []
        for socket in self.sockets:
            try:
                await socket.send_text(payload)
            except Exception:
                stale.append(socket)
        for socket in stale:
            self.sockets.discard(socket)

    def export_markdown(self, export_dir: Path) -> Path:
        export_dir.mkdir(parents=True, exist_ok=True)
        filename = export_filename(
            self.preset.date, self.preset.speaker, self.started_at
        )
        target = export_dir / filename
        lines = [
            "---",
            "type: seminar_transcript_analysis",
            f"status: {'needs_review' if self.last_error or self.provisional_text else 'draft'}",
            f"date: {self.preset.date}",
            f'speaker: "{self.preset.speaker}"',
            f'seminar_title: "{self.preset.title}"',
            f'external_ai_enabled: {str(self.external_ai_enabled).lower()}',
            "---",
            "",
            f"# {self.preset.title}",
            "",
            "> 本页由现场录音和机器转录生成。时间戳可回到本地 WAV 核验；未经人工听校的专名、数字和引语不得视为定稿。",
            "",
            "## 问题覆盖",
            "",
        ]
        for question in self.questions:
            lines.extend(
                [
                    f"### {question.question}",
                    "",
                    f"- 状态：`{question.status}`；置信度：{question.confidence:.2f}",
                    f"- 当前答案：{question.answer or '尚无可核验答案。'}",
                    f"- 仍缺：{'；'.join(question.missing) if question.missing else '无'}",
                ]
            )
            for evidence in question.evidence:
                lines.append(
                    f"- 证据 [{format_seconds(evidence.start)}-{format_seconds(evidence.end)}]：{evidence.quote}"
                )
            lines.append("")
        lines.extend(["## 完整稳定转录", ""])
        for segment in self.transcript:
            lines.append(
                f"[{format_seconds(segment.start)}-{format_seconds(segment.end)}] {segment.text}"
            )
        if self.followups:
            lines.extend(["", "## 建议追问", ""])
            lines.extend(f"- {item}" for item in self.followups)
        target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        self.export_path = str(target)
        self._write_manifest()
        return target

    def _write_wav(self) -> None:
        wav_path = self.root / "recording.wav"
        with wave.open(str(wav_path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(SAMPLE_RATE)
            handle.writeframes(self._raw_path.read_bytes())

    def _write_manifest(self) -> None:
        payload = {
            "id": self.id,
            "preset_id": self.preset.id,
            "status": self.status,
            "sample_rate": SAMPLE_RATE,
            "external_ai_enabled": self.external_ai_enabled,
            "source_path": self.preset.source_path,
            "export_path": self.export_path,
        }
        (self.root / "manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _append_jsonl(path: Path, payload: dict) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


class SessionManager:
    def __init__(self, presets: dict[str, SeminarPreset]) -> None:
        default_root = Path(__file__).resolve().parents[2] / "data" / "sessions"
        self.root = Path(os.environ.get("SEMINAR_DATA_DIR", str(default_root))).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self.presets = presets
        self.transcriber = WhisperTranscriber()
        self.analyzer = DeepSeekAnalyzer()
        self.sessions: dict[str, SeminarSession] = {}

    def create(self, preset_id: str, external_ai_enabled: bool) -> SeminarSession:
        preset = self.presets.get(preset_id)
        if preset is None:
            raise KeyError(preset_id)
        session = SeminarSession(
            preset=preset,
            root=self.root,
            transcriber=self.transcriber,
            analyzer=self.analyzer,
            external_ai_enabled=external_ai_enabled,
        )
        self.sessions[session.id] = session
        session.start_background_loop()
        return session

    def get(self, session_id: str) -> SeminarSession:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session


def format_seconds(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def slugify(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return result or "speaker"


def export_filename(date: str, speaker: str, started_at: datetime) -> str:
    time_label = started_at.strftime("%H%M%S")
    return f"{date}-{slugify(speaker)}-{time_label}-seminar-live-notes.md"
