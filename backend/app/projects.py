from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .analyzer import STATUS_ORDER, quote_is_grounded
from .models import (
    Evidence,
    HistoricalTranscriptSegment,
    ProjectDetail,
    ProjectNote,
    ProjectPhase,
    ProjectSummary,
    QuestionState,
    SeminarPreset,
    TranscriptSegment,
)


SESSION_TIME = re.compile(r"-(\d{8}-\d{6})-[a-z0-9]+$")
PROJECT_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,159}$")


@dataclass(frozen=True)
class SessionRecord:
    root: Path
    session_id: str
    project_id: str
    preset_id: str
    started_at: datetime
    manifest_status: str
    sample_rate: int
    audio_seconds: float
    transcript_rows: list[dict]
    analysis_rows: list[dict]
    temporary_question_rows: list[dict]


def safe_project_id(project_id: str) -> str:
    if not PROJECT_ID.fullmatch(project_id):
        raise KeyError(project_id)
    return project_id


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


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


def _started_at(session_id: str) -> datetime | None:
    match = SESSION_TIME.search(session_id)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d-%H%M%S")
    except ValueError:
        return None


def load_session_records(session_root: Path) -> list[SessionRecord]:
    records: list[SessionRecord] = []
    for root in session_root.iterdir():
        if not root.is_dir():
            continue
        manifest = _read_json(root / "manifest.json")
        session_id = str(manifest.get("id") or root.name)
        preset_id = str(manifest.get("preset_id") or "")
        started_at = _started_at(session_id)
        if not preset_id or started_at is None:
            continue
        sample_rate = int(manifest.get("sample_rate") or 16_000)
        pcm_path = root / "audio.pcm"
        audio_seconds = (
            pcm_path.stat().st_size / (2 * sample_rate) if pcm_path.exists() else 0.0
        )
        transcript_rows = _read_jsonl(root / "transcript.jsonl")
        analysis_rows = _read_jsonl(root / "analysis.jsonl")
        temporary_rows = _read_jsonl(root / "temporary_questions.jsonl")
        if audio_seconds < 1 and not transcript_rows:
            continue
        records.append(
            SessionRecord(
                root=root,
                session_id=session_id,
                project_id=str(manifest.get("project_id") or preset_id),
                preset_id=preset_id,
                started_at=started_at,
                manifest_status=str(manifest.get("status") or "recording"),
                sample_rate=sample_rate,
                audio_seconds=audio_seconds,
                transcript_rows=transcript_rows,
                analysis_rows=analysis_rows,
                temporary_question_rows=temporary_rows,
            )
        )
    return sorted(records, key=lambda item: item.started_at)


class ProjectStore:
    def __init__(
        self,
        session_root: Path,
        presets: dict[str, SeminarPreset],
        active_session_ids: set[str] | None = None,
    ) -> None:
        self.session_root = session_root
        self.presets = presets
        self.active_session_ids = active_session_ids or set()
        self.project_root = session_root.parent / "projects"
        self.project_root.mkdir(parents=True, exist_ok=True)

    def list_projects(self) -> list[ProjectSummary]:
        summaries = [self._build(project_id, records, detail=False) for project_id, records in self._groups().items()]
        return sorted(summaries, key=lambda item: item.updated_at, reverse=True)

    def get_project(self, project_id: str) -> ProjectDetail:
        safe_project_id(project_id)
        records = self._groups().get(project_id)
        if not records:
            raise KeyError(project_id)
        return self._build(project_id, records, detail=True)

    def add_note(self, project_id: str, text: str, audio_second: float | None) -> ProjectNote:
        self.get_project(project_id)
        note = ProjectNote(
            id=f"note-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:5]}",
            text=text.strip(),
            created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            audio_second=round(audio_second, 2) if audio_second is not None else None,
        )
        root = self._project_path(project_id)
        root.mkdir(parents=True, exist_ok=True)
        with (root / "notes.jsonl").open("a", encoding="utf-8") as output:
            output.write(note.model_dump_json() + "\n")
        return note

    def resolve_preset_id(self, project_id: str) -> str:
        return self.get_project(project_id).preset_id

    def session_audio_path(self, project_id: str, session_id: str) -> tuple[Path, int]:
        safe_project_id(project_id)
        records = self._groups().get(project_id, [])
        record = next((item for item in records if item.session_id == session_id), None)
        if record is None:
            raise KeyError(session_id)
        path = record.root / "audio.pcm"
        if not path.exists() or path.stat().st_size == 0:
            raise FileNotFoundError(path)
        return path, record.sample_rate

    def _groups(self) -> dict[str, list[SessionRecord]]:
        groups: dict[str, list[SessionRecord]] = {}
        for record in load_session_records(self.session_root):
            groups.setdefault(record.project_id, []).append(record)
        return groups

    def _project_path(self, project_id: str) -> Path:
        return self.project_root / safe_project_id(project_id)

    def _notes(self, project_id: str) -> list[ProjectNote]:
        notes: list[ProjectNote] = []
        for row in _read_jsonl(self._project_path(project_id) / "notes.jsonl"):
            try:
                notes.append(ProjectNote.model_validate(row))
            except Exception:
                continue
        return notes

    def _phase_status(self, record: SessionRecord) -> str:
        if record.session_id in self.active_session_ids:
            return "recording"
        if record.manifest_status == "stopped":
            return "stopped"
        return "interrupted" if record.audio_seconds > 0 else "empty"

    def _build(
        self, project_id: str, records: list[SessionRecord], *, detail: bool
    ) -> ProjectSummary | ProjectDetail:
        preset_id = records[0].preset_id
        preset = self.presets.get(preset_id)
        title = preset.title if preset else preset_id
        speaker = preset.speaker if preset else "Unknown speaker"
        date = preset.date if preset else records[0].started_at.date().isoformat()
        notes = self._notes(project_id)
        phases: list[ProjectPhase] = []
        transcript: list[HistoricalTranscriptSegment] = []
        offsets: dict[str, float] = {}
        project_started_at = records[0].started_at
        for index, record in enumerate(records):
            offset = max(0.0, (record.started_at - project_started_at).total_seconds())
            offsets[record.session_id] = offset
            next_start = records[index + 1].started_at if index + 1 < len(records) else None
            record_end = record.started_at + timedelta(seconds=record.audio_seconds)
            gap_after = max(0.0, (next_start - record_end).total_seconds()) if next_start else 0.0
            overlap_after = max(0.0, (record_end - next_start).total_seconds()) if next_start else 0.0
            phases.append(
                ProjectPhase(
                    session_id=record.session_id,
                    started_at=record.started_at.astimezone().isoformat(timespec="seconds"),
                    status=self._phase_status(record),
                    audio_seconds=round(record.audio_seconds, 3),
                    timeline_offset_seconds=round(offset, 3),
                    gap_after_seconds=round(gap_after, 3),
                    overlap_after_seconds=round(overlap_after, 3),
                    transcript_segments=len(record.transcript_rows),
                    analysis_runs=len(record.analysis_rows),
                    temporary_questions=len(
                        {
                            str(row.get("question", {}).get("id"))
                            for row in record.temporary_question_rows
                            if isinstance(row.get("question"), dict)
                            and row.get("question", {}).get("id")
                        }
                    ),
                    has_audio=record.audio_seconds > 0,
                    audio_url=(
                        f"/api/projects/{project_id}/sessions/{record.session_id}/audio.wav"
                        if record.audio_seconds > 0
                        else None
                    ),
                )
            )
            if detail:
                for row in record.transcript_rows:
                    try:
                        segment = TranscriptSegment.model_validate(row)
                    except Exception:
                        continue
                    transcript.append(
                        HistoricalTranscriptSegment(
                            session_id=record.session_id,
                            start=round(offset + segment.start, 2),
                            end=round(offset + segment.end, 2),
                            text=segment.text,
                        )
                    )
        transcript.sort(key=lambda item: (item.start, item.end, item.session_id))

        statuses = [phase.status for phase in phases]
        project_status = (
            "recording"
            if "recording" in statuses
            else "stopped"
            if statuses and statuses[-1] == "stopped"
            else "interrupted"
            if statuses
            else "empty"
        )
        temporary_questions = self._temporary_questions(records)
        summary_fields = dict(
            id=project_id,
            preset_id=preset_id,
            title=title,
            speaker=speaker,
            date=date,
            status=project_status,
            phase_count=len(phases),
            audio_seconds=round(sum(item.audio_seconds for item in phases), 3),
            transcript_segments=sum(item.transcript_segments for item in phases),
            analysis_runs=sum(item.analysis_runs for item in phases),
            temporary_questions=len(temporary_questions),
            note_count=len(notes),
            updated_at=max(
                [record.started_at.astimezone().isoformat(timespec="seconds") for record in records]
                + [note.created_at for note in notes]
            ),
        )
        if not detail:
            return ProjectSummary(**summary_fields)
        return ProjectDetail(
            **summary_fields,
            phases=phases,
            transcript=transcript,
            questions=self._question_states(
                preset, records, temporary_questions, offsets
            ),
            notes=notes,
        )

    @staticmethod
    def _temporary_questions(records: list[SessionRecord]) -> dict[str, QuestionState]:
        latest: dict[str, QuestionState] = {}
        for record in records:
            for row in record.temporary_question_rows:
                payload = row.get("question")
                if not isinstance(payload, dict):
                    continue
                try:
                    question = QuestionState.model_validate(payload)
                except Exception:
                    continue
                if question.temporary:
                    latest[question.id] = question
        return latest

    @staticmethod
    def _question_states(
        preset: SeminarPreset | None,
        records: list[SessionRecord],
        temporary: dict[str, QuestionState],
        offsets: dict[str, float],
    ) -> list[QuestionState]:
        states: dict[str, QuestionState] = {}
        if preset:
            states = {
                item.id: QuestionState(
                    id=item.id,
                    question=item.question,
                    why_it_matters=item.why_it_matters,
                    expected_slots=item.expected_slots,
                    keywords=item.keywords,
                    missing=list(item.expected_slots),
                )
                for item in preset.questions
            }
        states.update(
            {question_id: question.model_copy(deep=True) for question_id, question in temporary.items()}
        )
        for record in records:
            transcript: list[TranscriptSegment] = []
            for row in record.transcript_rows:
                try:
                    transcript.append(TranscriptSegment.model_validate(row))
                except Exception:
                    continue
            transcript_text = "\n".join(item.text for item in transcript)
            for row in record.analysis_rows:
                for update in row.get("updates", []):
                    if not isinstance(update, dict):
                        continue
                    state = states.get(str(update.get("question_id") or ""))
                    status = str(update.get("status") or "unanswered")
                    if state is None or status not in STATUS_ORDER:
                        continue
                    try:
                        confidence = max(
                            0.0, min(1.0, float(update.get("confidence", 0)))
                        )
                    except (TypeError, ValueError):
                        confidence = 0.0
                    quote = str(update.get("evidence_quote") or "").strip()
                    source = next(
                        (
                            item
                            for item in transcript
                            if quote and quote_is_grounded(quote, item.text)
                        ),
                        None,
                    )
                    minimum_confidence = {
                        "unanswered": 0.0,
                        "mention": 0.2,
                        "partial": 0.35,
                        "answered": 0.55,
                    }[status]
                    if status != "unanswered" and (
                        source is None or confidence < minimum_confidence
                    ):
                        continue
                    if STATUS_ORDER[status] < STATUS_ORDER[state.status]:
                        continue
                    if (
                        status == state.status
                        and confidence <= state.confidence
                    ):
                        continue
                    state.status = status  # type: ignore[assignment]
                    state.answer = str(update.get("answer") or "").strip()
                    missing = update.get("missing", [])
                    state.missing = (
                        [str(item) for item in missing]
                        if isinstance(missing, list)
                        else []
                    )
                    state.confidence = confidence
                    if source:
                        state.evidence = [
                            Evidence(
                                segment_id=source.id,
                                start=round(
                                    offsets.get(record.session_id, 0.0)
                                    + source.start,
                                    2,
                                ),
                                end=round(
                                    offsets.get(record.session_id, 0.0)
                                    + source.end,
                                    2,
                                ),
                                quote=quote,
                                relation=(
                                    "direct_answer"
                                    if status in {"partial", "answered"}
                                    else "background"
                                ),
                                confidence=state.confidence,
                                source_session=record.session_id,
                            )
                        ]
        return list(states.values())
