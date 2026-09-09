from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from dataclasses import dataclass

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


class DeepSeekAnalyzer:
    def __init__(self) -> None:
        self.binary = os.environ.get("SEMINAR_DSH_BIN") or shutil.which("dsh") or "dsh"

    async def analyze(
        self,
        transcript: str,
        questions: list[QuestionState],
        cwd: str,
    ) -> DeepAnalysisResult:
        prompt = self._build_prompt(transcript, questions)
        env = os.environ.copy()
        env["DSH_PERMISSION_MODE"] = "read-only"
        process = await asyncio.create_subprocess_exec(
            self.binary,
            "--profile",
            "headless",
            prompt,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=90)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError("DeepSeek analysis exceeded 90 seconds") from None
        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(message or f"dsh exited with {process.returncode}")

        raw = stdout.decode("utf-8", errors="replace").strip()
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

    @staticmethod
    def _parse_json(raw: str) -> dict:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"DeepSeek returned invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise RuntimeError("DeepSeek output must be a JSON object")
        return value

    @staticmethod
    def _build_prompt(transcript: str, questions: list[QuestionState]) -> str:
        compact_questions = [
            {
                "id": question.id,
                "question": question.question,
                "expected_slots": question.expected_slots,
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
