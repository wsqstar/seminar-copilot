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
    QuestionDefinition,
    QuestionState,
    RecoveredSessionState,
    SeminarPreset,
    SessionSnapshot,
    TranscriptSegment,
)
from .recovery import (
    RecoverySource,
    build_recovered_states,
    find_project_sources,
    find_recovery_sources,
)
from .research import AcademicSearcher, search_transcript


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
        auto_questions_enabled: bool = False,
        recovered_sources: list[RecoverySource] | None = None,
        project_id: str | None = None,
    ) -> None:
        self.started_at = datetime.now()
        stamp = self.started_at.strftime("%Y%m%d-%H%M%S")
        self.id = f"{preset.id}-{stamp}-{uuid.uuid4().hex[:6]}"
        self.preset = preset
        self.project_id = project_id or preset.id
        self.root = root / self.id
        self.root.mkdir(parents=True, exist_ok=False)
        self.transcriber = transcriber
        self.analyzer = analyzer
        self.external_ai_enabled = external_ai_enabled
        self.auto_questions_enabled = auto_questions_enabled
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
                keywords=q.keywords,
                missing=list(q.expected_slots),
            )
            for q in preset.questions
        ]
        sources = recovered_sources or []
        self.recovered_sessions, self.timeline_offset_seconds = build_recovered_states(
            sources, self.started_at
        )
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
        self._temporary_questions_path = self.root / "temporary_questions.jsonl"
        self._raw_file = self._raw_path.open("ab", buffering=0)
        self._question_tasks: set[asyncio.Task[Any]] = set()
        self._restore_temporary_questions(sources)
        self._seed_recovered_state(sources)
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
            segments = await asyncio.to_thread(
                self.transcriber.transcribe, audio, prompt, self.preset.language
            )
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
                        self._apply_local_matches(
                            segment,
                            source_session=self.id,
                            evidence_offset=self.timeline_offset_seconds,
                        )
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

    def _apply_local_matches(
        self,
        segment: TranscriptSegment,
        *,
        source_session: str | None = None,
        evidence_offset: float = 0.0,
    ) -> None:
        definitions = {item.id: item for item in self.preset.questions}
        for state in self.questions:
            definition = definitions.get(state.id)
            if definition is None:
                definition = QuestionDefinition(
                    id=state.id,
                    question=state.question,
                    keywords=state.keywords,
                    expected_slots=state.expected_slots,
                )
            hits = keyword_hits(definition, segment.text)
            if not hits:
                continue
            evidence = Evidence(
                segment_id=segment.id,
                start=round(segment.start + evidence_offset, 2),
                end=round(segment.end + evidence_offset, 2),
                quote=segment.text,
                relation="keyword_match",
                confidence=min(0.65, 0.3 + 0.1 * len(hits)),
                source_session=source_session,
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
            self._apply_deep_updates(
                result.updates,
                transcript_text,
                window_segments,
                source_session=self.id,
                evidence_offset=self.timeline_offset_seconds,
            )
            # Auto follow-up suggestions only when the explicit switch is on.
            self.followups = result.followups if self.auto_questions_enabled else []
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

    def add_temporary_question(self, draft: str, search_external: bool) -> QuestionState:
        clean_draft = draft.strip()
        question_id = f"temp-{datetime.now():%H%M%S}-{uuid.uuid4().hex[:4]}"
        state = QuestionState(
            id=question_id,
            question=clean_draft or "正在根据讲座内容形成问题…",
            question_en=clean_draft if clean_draft and clean_draft.isascii() else "",
            temporary=True,
            created_at_audio_second=round(
                self.timeline_offset_seconds + self.elapsed_seconds, 2
            ),
            missing=["双语问题", "讲座证据", "外部学术依据"],
            research_status="pending",
            research_summary="正在检索讲座记录并整理学术依据。",
        )
        self.questions.append(state)
        self._append_jsonl(
            self._temporary_questions_path,
            {
                "event": "created",
                "at": datetime.now().isoformat(timespec="seconds"),
                "question": state.model_dump(),
                "draft": clean_draft,
                "search_external": search_external,
            },
        )
        task = asyncio.create_task(
            self._enrich_temporary_question(state, clean_draft, search_external)
        )
        self._question_tasks.add(task)
        task.add_done_callback(self._question_tasks.discard)
        return state

    async def _enrich_temporary_question(
        self, state: QuestionState, draft: str, search_external: bool
    ) -> None:
        try:
            all_segments = self._all_transcript_segments()
            prompt_segments = all_segments[-80:]
            transcript_text = "\n".join(
                f"[{format_seconds(offset + segment.start)}] {segment.text}"
                for segment, offset in prompt_segments
            )
            formulated = await self.analyzer.formulate_temporary_question(
                draft, transcript_text, self.questions, str(self.root)
            )
            state.question = formulated.question_zh
            state.question_en = formulated.question_en
            state.why_it_matters = formulated.why_it_matters
            state.expected_slots = formulated.expected_slots
            state.keywords = formulated.keywords
            state.missing = list(formulated.expected_slots)
            local_sources = search_transcript(
                f"{state.question} {state.question_en}",
                formulated.keywords,
                all_segments,
            )
            external_sources = []
            search_notes: list[str] = []
            if search_external:
                outcome = await asyncio.to_thread(
                    AcademicSearcher().search, formulated.search_query_en
                )
                external_sources = outcome.sources
                search_notes = outcome.notes
            state.research_sources = [*local_sources, *external_sources]
            if local_sources:
                state.research_summary = (
                    "讲座中已有相关内容；问题已加入持续回答检测。"
                )
            elif external_sources:
                state.research_summary = (
                    "讲座尚未直接回答；已找到可核验的相关学术作品供现场提问参考。"
                )
            else:
                state.research_summary = (
                    "讲座尚未直接回答，外部检索暂未返回可核验结果。"
                )
            state.research_status = (
                "complete" if state.research_sources else "limited"
            )
            if search_notes:
                state.research_summary += " " + "；".join(search_notes)
            definition = QuestionDefinition(
                id=state.id,
                question=state.question,
                keywords=formulated.keywords,
                expected_slots=state.expected_slots,
            )
            for segment, offset in all_segments:
                hits = keyword_hits(definition, segment.text)
                if hits:
                    self._apply_temporary_match(state, segment, offset, hits)
            self._append_jsonl(
                self._temporary_questions_path,
                {
                    "event": "enriched",
                    "at": datetime.now().isoformat(timespec="seconds"),
                    "question": state.model_dump(),
                    "search_query": formulated.search_query_en,
                },
            )
        except Exception as exc:
            state.research_status = "error"
            state.research_summary = f"问题已保存，但自动整理暂时失败：{exc}"
            self._append_jsonl(
                self._temporary_questions_path,
                {
                    "event": "error",
                    "at": datetime.now().isoformat(timespec="seconds"),
                    "question_id": state.id,
                    "error": str(exc),
                },
            )
        finally:
            await self.broadcast()

    def _all_transcript_segments(self) -> list[tuple[TranscriptSegment, float]]:
        segments: list[tuple[TranscriptSegment, float]] = []
        for phase in self.recovered_sessions:
            segments.extend(
                (segment, phase.timeline_offset_seconds)
                for segment in phase.transcript
            )
        segments.extend(
            (segment, self.timeline_offset_seconds) for segment in self.transcript
        )
        return segments

    def _apply_temporary_match(
        self,
        state: QuestionState,
        segment: TranscriptSegment,
        offset: float,
        hits: list[str],
    ) -> None:
        if all(item.segment_id != segment.id for item in state.evidence):
            state.evidence.append(
                Evidence(
                    segment_id=segment.id,
                    start=round(offset + segment.start, 2),
                    end=round(offset + segment.end, 2),
                    quote=segment.text,
                    relation="keyword_match",
                    confidence=min(0.7, 0.35 + 0.08 * len(hits)),
                    source_session=(
                        self.id
                        if offset == self.timeline_offset_seconds
                        else "recovered"
                    ),
                )
            )
            state.evidence = state.evidence[-5:]
        if state.status == "unanswered":
            state.status = "mention"
            state.confidence = state.evidence[-1].confidence

    def _apply_deep_updates(
        self,
        updates: list[dict],
        transcript_text: str,
        window_segments: list[TranscriptSegment],
        *,
        source_session: str | None = None,
        evidence_offset: float = 0.0,
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
            if STATUS_ORDER[status] < STATUS_ORDER[state.status]:
                continue
            if STATUS_ORDER[status] == STATUS_ORDER[state.status] and state.status != "unanswered" and not grounded:
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
                            start=round(source.start + evidence_offset, 2),
                            end=round(source.end + evidence_offset, 2),
                            quote=quote,
                            relation="direct_answer" if status in {"partial", "answered"} else "background",
                            confidence=state.confidence,
                            source_session=source_session,
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
            project_id=self.project_id,
            status=self.status,
            preset=self.preset,
            elapsed_seconds=round(self.elapsed_seconds, 2),
            committed_until=round(self.committed_until, 2),
            asr_state=self.asr_state,
            analyzer_state=self.analyzer_state,
            external_ai_enabled=self.external_ai_enabled,
            auto_questions_enabled=self.auto_questions_enabled,
            recovered_sessions=self.recovered_sessions,
            timeline_offset_seconds=self.timeline_offset_seconds,
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
            if question.question_en:
                lines.append(f"- English: {question.question_en}")
            if question.temporary:
                lines.extend(
                    [
                        f"- 临时问题创建时间：{format_seconds(question.created_at_audio_second or 0)}",
                        f"- 检索状态：`{question.research_status}`",
                        f"- 检索摘要：{question.research_summary or '无'}",
                    ]
                )
                for source in question.research_sources:
                    label = (
                        f"{source.title} ({source.year})"
                        if source.year
                        else source.title
                    )
                    if source.url:
                        lines.append(
                            f"- 参考来源 [{source.source_type}]：[{label}]({source.url})"
                        )
                    else:
                        lines.append(f"- 讲座证据：{source.snippet}")
            for evidence in question.evidence:
                lines.append(
                    f"- 证据 [{format_seconds(evidence.start)}-{format_seconds(evidence.end)}]：{evidence.quote}"
                )
            lines.append("")
        lines.extend(["## 完整稳定转录", ""])
        for recovered in self.recovered_sessions:
            lines.append(f"### 重启前阶段 `{recovered.session_id}`")
            lines.append("")
            for segment in recovered.transcript:
                start = recovered.timeline_offset_seconds + segment.start
                end = recovered.timeline_offset_seconds + segment.end
                lines.append(f"[{format_seconds(start)}-{format_seconds(end)}] {segment.text}")
            if recovered.gap_after_seconds > 0:
                lines.extend(
                    [
                        "",
                        f"> 录音重启缺口：约 {recovered.gap_after_seconds:.1f} 秒。",
                        "",
                    ]
                )
        if self.recovered_sessions:
            lines.extend(["### 当前阶段", ""])
        for segment in self.transcript:
            start = self.timeline_offset_seconds + segment.start
            end = self.timeline_offset_seconds + segment.end
            lines.append(
                f"[{format_seconds(start)}-{format_seconds(end)}] {segment.text}"
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
            "project_id": self.project_id,
            "preset_id": self.preset.id,
            "status": self.status,
            "sample_rate": SAMPLE_RATE,
            "external_ai_enabled": self.external_ai_enabled,
            "auto_questions_enabled": self.auto_questions_enabled,
            "language": self.preset.language,
            "source_path": self.preset.source_path,
            "export_path": self.export_path,
            "recovered_session_ids": [item.session_id for item in self.recovered_sessions],
            "timeline_offset_seconds": self.timeline_offset_seconds,
            "temporary_question_count": sum(
                1 for item in self.questions if item.temporary
            ),
        }
        (self.root / "manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _append_jsonl(path: Path, payload: dict) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _seed_recovered_state(self, sources: list[RecoverySource]) -> None:
        if not sources:
            return
        state_by_id = {item.session_id: item for item in self.recovered_sessions}
        for source in sources:
            recovered = state_by_id[source.session_id]
            transformed = [
                TranscriptSegment(
                    id=f"{source.session_id}:{segment.id}",
                    start=round(recovered.timeline_offset_seconds + segment.start, 2),
                    end=round(recovered.timeline_offset_seconds + segment.end, 2),
                    text=segment.text,
                    final=segment.final,
                )
                for segment in source.transcript
            ]
            for segment in transformed:
                self._apply_local_matches(segment, source_session=source.session_id)
            transcript_text = "\n".join(segment.text for segment in transformed)
            for row in source.analysis_rows:
                updates = row.get("updates", [])
                if isinstance(updates, list):
                    self._apply_deep_updates(
                        updates,
                        transcript_text,
                        transformed,
                        source_session=source.session_id,
                    )
                followups = row.get("followups", [])
                if isinstance(followups, list) and followups:
                    self.followups = [str(item) for item in followups if str(item).strip()]

    def _restore_temporary_questions(self, sources: list[RecoverySource]) -> None:
        restored: dict[str, QuestionState] = {}
        pending_inputs: dict[str, tuple[str, bool]] = {}
        for source in sources:
            for row in source.temporary_question_rows:
                payload = row.get("question")
                if not isinstance(payload, dict):
                    continue
                try:
                    question = QuestionState.model_validate(payload)
                except Exception:
                    continue
                if question.temporary:
                    restored[question.id] = question
                    if row.get("event") == "created":
                        pending_inputs[question.id] = (
                            str(row.get("draft") or ""),
                            bool(row.get("search_external", True)),
                        )
        self.questions.extend(restored.values())
        for question in restored.values():
            if question.research_status != "pending":
                continue
            draft, search_external = pending_inputs.get(question.id, (question.question, True))
            task = asyncio.create_task(
                self._enrich_temporary_question(question, draft, search_external)
            )
            self._question_tasks.add(task)
            task.add_done_callback(self._question_tasks.discard)


class SessionManager:
    def __init__(self, presets: dict[str, SeminarPreset]) -> None:
        default_root = Path(__file__).resolve().parents[2] / "data" / "sessions"
        self.root = Path(os.environ.get("SEMINAR_DATA_DIR", str(default_root))).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self.presets = presets
        self.transcriber = WhisperTranscriber()
        self.analyzer = DeepSeekAnalyzer()
        self.sessions: dict[str, SeminarSession] = {}

    def create(
        self,
        preset_id: str,
        external_ai_enabled: bool,
        auto_questions_enabled: bool = False,
    ) -> SeminarSession:
        preset = self.presets.get(preset_id)
        if preset is None:
            raise KeyError(preset_id)
        self._ensure_no_active_project(preset_id)
        now = datetime.now()
        recovered_sources = find_recovery_sources(self.root, preset_id, now)
        session = SeminarSession(
            preset=preset,
            root=self.root,
            transcriber=self.transcriber,
            analyzer=self.analyzer,
            external_ai_enabled=external_ai_enabled,
            auto_questions_enabled=auto_questions_enabled,
            recovered_sources=recovered_sources,
            project_id=preset_id,
        )
        self.sessions[session.id] = session
        session.start_background_loop()
        return session

    def continue_project(
        self,
        project_id: str,
        preset_id: str,
        external_ai_enabled: bool,
        auto_questions_enabled: bool = False,
    ) -> SeminarSession:
        preset = self.presets.get(preset_id)
        if preset is None:
            raise KeyError(preset_id)
        self._ensure_no_active_project(project_id)
        recovered_sources = find_project_sources(self.root, project_id)
        if not recovered_sources:
            raise KeyError(project_id)
        session = SeminarSession(
            preset=preset,
            root=self.root,
            transcriber=self.transcriber,
            analyzer=self.analyzer,
            external_ai_enabled=external_ai_enabled,
            auto_questions_enabled=auto_questions_enabled,
            recovered_sources=recovered_sources,
            project_id=project_id,
        )
        self.sessions[session.id] = session
        session.start_background_loop()
        return session

    def active_session_ids(self) -> set[str]:
        return {
            session.id
            for session in self.sessions.values()
            if session.status in {"recording", "stopping"}
        }

    def _ensure_no_active_project(self, project_id: str) -> None:
        if any(
            session.project_id == project_id
            and session.status in {"recording", "stopping"}
            for session in self.sessions.values()
        ):
            raise RuntimeError("该项目已有正在进行的录音")

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
