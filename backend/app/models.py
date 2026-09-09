from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


QuestionStatus = Literal["unanswered", "mention", "partial", "answered"]
ResearchStatus = Literal["not_requested", "pending", "complete", "limited", "error"]


class QuestionDefinition(BaseModel):
    id: str
    question: str
    why_it_matters: str = ""
    keywords: list[str] = Field(default_factory=list)
    expected_slots: list[str] = Field(default_factory=list)


class SeminarPreset(BaseModel):
    id: str
    title: str
    speaker: str
    date: str
    source_path: str | None = None
    glossary: list[str] = Field(default_factory=list)
    questions: list[QuestionDefinition]


class StartSessionRequest(BaseModel):
    preset_id: str
    external_ai_enabled: bool = False
    recording_permission_confirmed: bool


class ContinueProjectRequest(BaseModel):
    external_ai_enabled: bool = False
    recording_permission_confirmed: bool


class Evidence(BaseModel):
    segment_id: str
    start: float
    end: float
    quote: str
    relation: Literal["keyword_match", "direct_answer", "background", "contradiction"]
    confidence: float = Field(ge=0.0, le=1.0)
    source_session: str | None = None


class ResearchSource(BaseModel):
    source_type: Literal["transcript", "openalex", "crossref"]
    title: str
    url: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    snippet: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class QuestionState(BaseModel):
    id: str
    question: str
    why_it_matters: str = ""
    expected_slots: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    status: QuestionStatus = "unanswered"
    answer: str = ""
    missing: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)
    question_en: str = ""
    temporary: bool = False
    created_at_audio_second: float | None = None
    research_status: ResearchStatus = "not_requested"
    research_summary: str = ""
    research_sources: list[ResearchSource] = Field(default_factory=list)


class TemporaryQuestionRequest(BaseModel):
    draft: str = Field(default="", max_length=1000)
    search_external: bool = True


class TranscriptSegment(BaseModel):
    id: str
    start: float
    end: float
    text: str
    final: bool = True


class RecoveredSessionState(BaseModel):
    session_id: str
    started_at: str
    audio_seconds: float
    timeline_offset_seconds: float
    gap_after_seconds: float = 0.0
    overlap_after_seconds: float = 0.0
    transcript: list[TranscriptSegment] = Field(default_factory=list)
    ai_analysis_runs: int = 0


class SessionSnapshot(BaseModel):
    id: str
    project_id: str
    status: Literal["recording", "stopping", "stopped", "error"]
    preset: SeminarPreset
    elapsed_seconds: float
    committed_until: float
    asr_state: str
    analyzer_state: str
    external_ai_enabled: bool
    recovered_sessions: list[RecoveredSessionState] = Field(default_factory=list)
    timeline_offset_seconds: float = 0.0
    transcript: list[TranscriptSegment]
    provisional_text: str = ""
    questions: list[QuestionState]
    followups: list[str] = Field(default_factory=list)
    last_error: str = ""
    export_path: str | None = None


class ExportResponse(BaseModel):
    path: str


class ProjectNoteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    audio_second: float | None = Field(default=None, ge=0.0)


class ProjectNote(BaseModel):
    id: str
    text: str
    created_at: str
    audio_second: float | None = None


class ProjectPhase(BaseModel):
    session_id: str
    started_at: str
    status: Literal["recording", "stopped", "interrupted", "empty"]
    audio_seconds: float
    timeline_offset_seconds: float
    gap_after_seconds: float = 0.0
    overlap_after_seconds: float = 0.0
    transcript_segments: int = 0
    analysis_runs: int = 0
    temporary_questions: int = 0
    has_audio: bool = False
    audio_url: str | None = None


class HistoricalTranscriptSegment(BaseModel):
    session_id: str
    start: float
    end: float
    text: str


class ProjectSummary(BaseModel):
    id: str
    preset_id: str
    title: str
    speaker: str
    date: str
    status: Literal["recording", "stopped", "interrupted", "empty"]
    phase_count: int
    audio_seconds: float
    transcript_segments: int
    analysis_runs: int
    temporary_questions: int
    note_count: int
    updated_at: str


class ProjectDetail(ProjectSummary):
    phases: list[ProjectPhase]
    transcript: list[HistoricalTranscriptSegment]
    questions: list[QuestionState]
    notes: list[ProjectNote]
