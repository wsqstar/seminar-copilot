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
    transcript: list[TranscriptSegment] = Field(default_factory=list)
    ai_analysis_runs: int = 0


class SessionSnapshot(BaseModel):
    id: str
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
