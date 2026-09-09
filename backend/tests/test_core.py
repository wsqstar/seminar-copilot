from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

from app.analyzer import TemporaryQuestionDraft, keyword_hits, quote_is_grounded
from app.models import QuestionDefinition, SeminarPreset, TranscriptSegment
from app.presets import load_presets
from app.recovery import build_recovered_states, find_recovery_sources
from app.session import SeminarSession, export_filename, format_seconds, slugify
from app.research import bibliographic_relevance, search_transcript


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


def test_recent_orphan_sessions_form_a_recovery_chain(tmp_path: Path) -> None:
    preset_id = "lecture-2026-09-09"
    starts = [datetime(2026, 9, 9, 14, 0), datetime(2026, 9, 9, 14, 1)]
    for index, started_at in enumerate(starts):
        session_id = f"{preset_id}-{started_at:%Y%m%d-%H%M%S}-abc{index}"
        root = tmp_path / session_id
        root.mkdir()
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "id": session_id,
                    "preset_id": preset_id,
                    "status": "recording",
                    "sample_rate": 16_000,
                }
            ),
            encoding="utf-8",
        )
        (root / "audio.pcm").write_bytes(b"\0\0" * 16_000 * 50)
        (root / "transcript.jsonl").write_text(
            json.dumps({"id": "seg-1", "start": 3, "end": 8, "text": f"phase {index}"}) + "\n",
            encoding="utf-8",
        )

    now = datetime(2026, 9, 9, 14, 2)
    sources = find_recovery_sources(tmp_path, preset_id, now)
    states, offset = build_recovered_states(sources, now)

    assert len(states) == 2
    assert states[0].gap_after_seconds == 10
    assert states[1].gap_after_seconds == 10
    assert offset == 120


def test_deep_updates_never_downgrade_an_existing_answer(tmp_path: Path) -> None:
    preset = SeminarPreset(
        id="test",
        title="Test",
        speaker="Speaker",
        date="2026-09-09",
        questions=[QuestionDefinition(id="q1", question="Question")],
    )
    session = SeminarSession(preset, tmp_path, object(), object(), False)  # type: ignore[arg-type]
    session.questions[0].status = "answered"
    session.questions[0].answer = "Grounded earlier answer"

    session._apply_deep_updates(
        [{"question_id": "q1", "status": "unanswered", "answer": ""}],
        "new short window",
        [],
    )

    assert session.questions[0].status == "answered"
    assert session.questions[0].answer == "Grounded earlier answer"
    session._raw_file.close()


def test_transcript_search_returns_timestamped_evidence() -> None:
    segments = [
        (
            TranscriptSegment(
                id="seg-1",
                start=12,
                end=18,
                text="We compare siblings from the same family to control early-life conditions.",
            ),
            100,
        )
    ]
    results = search_transcript(
        "How does the sibling comparison work?",
        ["siblings", "early-life conditions"],
        segments,
    )
    assert results[0].source_type == "transcript"
    assert results[0].title == "讲座转录 01:52"


def test_bibliographic_relevance_rejects_generic_topic_noise() -> None:
    query = "multiple migration residence history exposure misclassification mortality"
    assert bibliographic_relevance(
        query, "Multiple migration and residence histories in mortality research"
    ) > 0.7
    assert bibliographic_relevance(
        query, "The evolving use of administrative health data"
    ) < 0.7


def test_temporary_question_is_persisted_before_enrichment(tmp_path: Path) -> None:
    class FakeAnalyzer:
        async def formulate_temporary_question(self, *_: object) -> TemporaryQuestionDraft:
            return TemporaryQuestionDraft(
                question_zh="兄弟姐妹比较仍有哪些选择偏差？",
                question_en="What selection bias remains in the sibling comparison?",
                why_it_matters="Clarifies the identification boundary.",
                keywords=["sibling", "selection bias"],
                expected_slots=["remaining selection", "model boundary"],
                search_query_en="sibling fixed effects migration selection bias longevity",
            )

    async def scenario() -> None:
        preset = SeminarPreset(
            id="temporary-test",
            title="Test",
            speaker="Speaker",
            date="2026-09-09",
            questions=[],
        )
        session = SeminarSession(
            preset, tmp_path, object(), FakeAnalyzer(), False  # type: ignore[arg-type]
        )
        session.transcript.append(
            TranscriptSegment(
                id="seg-1",
                start=1,
                end=5,
                text="We compare siblings but individual selection can remain.",
            )
        )
        state = session.add_temporary_question("selection bias?", False)
        first_row = json.loads(
            session._temporary_questions_path.read_text(encoding="utf-8").splitlines()[0]
        )
        assert first_row["event"] == "created"
        assert first_row["question"]["id"] == state.id
        await asyncio.gather(*list(session._question_tasks))
        assert state.question_en.startswith("What selection bias")
        assert state.research_status == "complete"
        assert len(session._temporary_questions_path.read_text(encoding="utf-8").splitlines()) == 2
        session._raw_file.close()

    asyncio.run(scenario())
