from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.analyzer import TemporaryQuestionDraft, keyword_hits, quote_is_grounded
from app.main import _requested_byte_range
from app.models import QuestionDefinition, SeminarPreset, TranscriptSegment
from app.presets import load_presets
from app.projects import ProjectStore
from app.recovery import build_recovered_states, find_project_sources, find_recovery_sources
from app.session import (
    SessionManager,
    SeminarSession,
    export_filename,
    format_seconds,
    slugify,
)
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


def test_audio_byte_ranges_support_seeking() -> None:
    assert _requested_byte_range(None, 1_000) == (0, 999)
    assert _requested_byte_range("bytes=44-143", 1_000) == (44, 143)
    assert _requested_byte_range("bytes=900-", 1_000) == (900, 999)
    assert _requested_byte_range("bytes=-100", 1_000) == (900, 999)


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


def test_project_history_groups_phases_and_persists_notes(tmp_path: Path) -> None:
    preset = SeminarPreset(
        id="history-test",
        title="A complete seminar project",
        speaker="Speaker",
        date="2026-09-09",
        questions=[QuestionDefinition(id="q1", question="Was it answered?")],
    )
    session_root = tmp_path / "sessions"
    session_root.mkdir()
    starts = [datetime(2026, 9, 9, 14, 0), datetime(2026, 9, 9, 14, 1)]
    for index, started_at in enumerate(starts):
        session_id = f"history-test-{started_at:%Y%m%d-%H%M%S}-abc{index}"
        root = session_root / session_id
        root.mkdir()
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "id": session_id,
                    "preset_id": "history-test",
                    "status": "stopped" if index else "recording",
                    "sample_rate": 16_000,
                }
            ),
            encoding="utf-8",
        )
        (root / "audio.pcm").write_bytes(b"\0\0" * 16_000 * 30)
        (root / "transcript.jsonl").write_text(
            json.dumps(
                {
                    "id": f"seg-{index}",
                    "start": 2,
                    "end": 6,
                    "text": "This directly answers the question." if index else "Introduction",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        if index:
            (root / "analysis.jsonl").write_text(
                json.dumps(
                    {
                        "updates": [
                            {
                                "question_id": "q1",
                                "status": "answered",
                                "answer": "Yes.",
                                "evidence_quote": "This directly answers the question.",
                                "missing": [],
                                "confidence": 0.9,
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

    store = ProjectStore(session_root, {preset.id: preset})
    projects = store.list_projects()
    assert len(projects) == 1
    assert projects[0].phase_count == 2
    assert projects[0].audio_seconds == 60
    assert projects[0].status == "stopped"

    note = store.add_note("history-test", "Remember this limitation.", 35)
    detail = store.get_project("history-test")
    assert detail.note_count == 1
    assert detail.notes[0].id == note.id
    assert len(detail.transcript) == 2
    assert detail.transcript[1].start == 62
    assert detail.questions[0].status == "answered"
    assert detail.questions[0].answer == "Yes."
    assert detail.questions[0].evidence[0].start == 62
    assert len(find_project_sources(session_root, "history-test")) == 2

    async def continue_scenario() -> None:
        manager = SessionManager.__new__(SessionManager)
        manager.root = session_root
        manager.presets = {preset.id: preset}
        manager.transcriber = object()
        manager.analyzer = object()
        manager.sessions = {}
        continued = manager.continue_project("history-test", preset.id, False)
        assert continued.project_id == "history-test"
        assert len(continued.recovered_sessions) == 2
        with pytest.raises(RuntimeError, match="已有正在进行的录音"):
            manager.continue_project("history-test", preset.id, False)
        await continued.stop()

    asyncio.run(continue_scenario())
