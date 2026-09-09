from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.analyzer import keyword_hits, quote_is_grounded
from app.models import QuestionDefinition
from app.presets import load_presets
from app.session import export_filename, format_seconds, slugify


def test_preset_is_loadable_and_has_question_slots() -> None:
    presets = load_presets()
    preset = presets["jiaxin-shi-2026-09-09"]
    assert len(preset.questions) == 6
    assert all(question.keywords for question in preset.questions)
    assert all(question.expected_slots for question in preset.questions)


def test_keyword_match_is_case_insensitive() -> None:
    question = QuestionDefinition(
        id="q1",
        question="How is selection estimated?",
        keywords=["selection mechanism", "within-family"],
    )
    assert keyword_hits(question, "The SELECTION mechanism explains most of it.") == [
        "selection mechanism"
    ]


def test_quote_grounding_requires_exact_normalized_span() -> None:
    transcript = "[01:00-01:12] Destination effects are larger than origin effects."
    assert quote_is_grounded("Destination effects are larger than origin effects", transcript)
    assert not quote_is_grounded("Migration causally increases longevity", transcript)
    assert not quote_is_grounded("short", transcript)


def test_format_helpers() -> None:
    assert format_seconds(125.8) == "02:05"
    assert slugify("Jiaxin Shi") == "jiaxin-shi"
    assert export_filename(
        "2026-09-09", "Jiaxin Shi", datetime(2026, 9, 9, 14, 30, 12)
    ) == "2026-09-09-jiaxin-shi-143012-seminar-live-notes.md"


def test_preset_json_contains_no_api_credentials() -> None:
    config = Path(__file__).resolve().parents[1] / "config" / "seminars" / "jiaxin-shi-2026-09-09.json"
    text = config.read_text(encoding="utf-8").lower()
    assert "api_key" not in text
    json.loads(text)
