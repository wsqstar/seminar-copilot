from __future__ import annotations

import json
import re
from dataclasses import dataclass

from . import llm
from .models import QuestionDefinition, QuestionState


STATUS_ORDER = {"unanswered": 0, "mention": 1, "partial": 2, "answered": 3}


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value.lower()).strip()


def quote_is_grounded(quote: str, transcript: str) -> bool:
    quote_norm = normalize_text(quote)
    transcript_norm = normalize_text(transcript)
    return len(quote_norm) >= 12 and quote_norm in transcript_norm


def keyword_hits(question: QuestionDefinition, text: str) -> list[str]:
    haystack = normalize_text(text)
    return [keyword for keyword in question.keywords if normalize_text(keyword) in haystack]


@dataclass(frozen=True)
class DeepAnalysisResult:
    updates: list[dict]
    followups: list[str]
    raw: str


@dataclass(frozen=True)
class TemporaryQuestionDraft:
    question_zh: str
    question_en: str
    why_it_matters: str
    keywords: list[str]
    expected_slots: list[str]
    search_query_en: str


class DeepSeekAnalyzer:
    async def analyze(
        self,
        transcript: str,
        questions: list[QuestionState],
        cwd: str,
    ) -> DeepAnalysisResult:
        prompt = self._build_prompt(transcript, questions)
        raw = await llm.complete(prompt, 90, cwd)
        payload = self._parse_json(raw)
        updates = payload.get("updates", [])
        followups = payload.get("followups", [])
        if not isinstance(updates, list) or not isinstance(followups, list):
            raise RuntimeError("DeepSeek output does not match the expected schema")
        return DeepAnalysisResult(
            updates=[item for item in updates if isinstance(item, dict)],
            followups=[str(item).strip() for item in followups if str(item).strip()][:3],
            raw=raw,
        )

    async def formulate_temporary_question(
        self,
        draft: str,
        transcript: str,
        questions: list[QuestionState],
        cwd: str,
    ) -> TemporaryQuestionDraft:
        unresolved = [
            {"question": item.question, "status": item.status, "missing": item.missing}
            for item in questions
            if item.status != "answered"
        ]
        prompt = (
            "You help a researcher ask one precise, evidence-aware question during an academic seminar. "
            "Do not use tools and do not invent facts. Preserve the user's intent when a draft is supplied. "
            "If the draft is empty, identify one consequential gap not already duplicated by the unresolved questions. "
            "The Chinese and English questions must be natural equivalents, concise enough to ask aloud, and include "
            "the comparison, mechanism, identification boundary, or evidence needed to answer them. "
            "Return exactly one JSON object without markdown with schema: "
            '{"question_zh":"...","question_en":"...","why_it_matters":"...",'
            '"keywords":["..."],"expected_slots":["..."],"search_query_en":"..."}. '
            "Use 3-8 keywords and 2-5 expected answer slots. Make search_query_en a compact "
            "bibliographic query with 5-10 discriminative terms, not a full sentence and not a list "
            "of generic academic words.\n\n"
            f"USER DRAFT:\n{draft or '(none)'}\n\n"
            f"UNRESOLVED QUESTIONS:\n{json.dumps(unresolved, ensure_ascii=False)}\n\n"
            f"RECENT TRANSCRIPT:\n{transcript[-12000:]}"
        )
        payload = self._parse_json(await llm.complete(prompt, 75, cwd))
        question_zh = str(payload.get("question_zh") or draft).strip()
        question_en = str(payload.get("question_en") or draft).strip()
        if not question_zh or not question_en:
            raise RuntimeError("DeepSeek returned an incomplete bilingual question")
        return TemporaryQuestionDraft(
            question_zh=question_zh,
            question_en=question_en,
            why_it_matters=str(payload.get("why_it_matters") or "").strip(),
            keywords=[
                str(item).strip()
                for item in payload.get("keywords", [])
                if str(item).strip()
            ][:8],
            expected_slots=[
                str(item).strip()
                for item in payload.get("expected_slots", [])
                if str(item).strip()
            ][:5],
            search_query_en=str(payload.get("search_query_en") or question_en).strip(),
        )

    @staticmethod
    def _parse_json(raw: str) -> dict:
        return llm.extract_json(raw)

    @staticmethod
    def _build_prompt(transcript: str, questions: list[QuestionState]) -> str:
        compact_questions = [
            {
                "id": question.id,
                "question": question.question,
                "expected_slots": question.expected_slots,
                "keywords": question.keywords,
                "current_status": question.status,
                "current_missing": question.missing,
            }
            for question in questions
        ]
        return (
            "You are a low-latency seminar evidence judge. Do not use tools. "
            "Assess only what the transcript explicitly supports. A topic mention is not an answer. "
            "Use answered only when the essential expected slots are covered; otherwise use partial or mention. "
            "Every evidence_quote must be copied verbatim from the transcript. "
            "Return exactly one compact JSON object, without markdown, with schema: "
            '{"updates":[{"question_id":"...","status":"unanswered|mention|partial|answered",'
            '"answer":"...","evidence_quote":"...","missing":["..."],"confidence":0.0}],'
            '"followups":["..."]}. Keep at most two concise follow-up questions.\n\n'
            f"QUESTIONS:\n{json.dumps(compact_questions, ensure_ascii=False)}\n\n"
            f"TRANSCRIPT WINDOW:\n{transcript}"
        )
