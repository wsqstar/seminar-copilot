from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


QuestionStatus = Literal["unanswered", "mention", "partial", "answered"]


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


class QuestionState(BaseModel):
    id: str
    question: str
    why_it_matters: str = ""
    expected_slots: list[str] = Field(default_factory=list)
    status: QuestionStatus = "unanswered"
    answer: str = ""
    missing: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)


class TranscriptSegment(BaseModel):
    id: str
    start: float
    end: float
    text: str
    final: bool = True


class SessionSnapshot(BaseModel):
    id: str
    status: Literal["recording", "stopping", "stopped", "error"]
    preset: SeminarPreset
    elapsed_seconds: float
    committed_until: float
    asr_state: str
    analyzer_state: str
    external_ai_enabled: bool
    transcript: list[TranscriptSegment]
    provisional_text: str = ""
    questions: list[QuestionState]
    followups: list[str] = Field(default_factory=list)
    last_error: str = ""
    export_path: str | None = None


class ExportResponse(BaseModel):
    path: str
