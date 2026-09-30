from __future__ import annotations

import asyncio
import json
import os
import struct
from contextlib import asynccontextmanager
from datetime import date as date_type
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .models import (
    AttachSeminarRequest,
    AttachSeminarResponse,
    ContinueProjectRequest,
    ExportResponse,
    IntakeConfirmRequest,
    IntakeConfirmResponse,
    IntakeParseRequest,
    IntakeParseResponse,
    ProjectDetail,
    ProjectNote,
    ProjectNoteRequest,
    ProjectSummary,
    QuestionDefinition,
    SeminarPreset,
    SessionSnapshot,
    StartSessionRequest,
    TemporaryQuestionRequest,
)
from . import intake
from .analyzer import keyword_hits
from .intake import (
    _clean_glossary,
    assess_relevance,
    parse_announcement,
    propose_questions,
    validate_preset_id,
)
from .presets import CONFIG_DIR, QUICK_RECORD_PRESET_ID, load_presets, reload_presets
from .projects import ProjectStore, _read_jsonl
from .session import SessionManager


presets = load_presets()
manager = SessionManager(presets)


def project_store() -> ProjectStore:
    return ProjectStore(manager.root, presets, manager.active_session_ids())


@asynccontextmanager
async def lifespan(_: FastAPI):
    warmup_task: asyncio.Task[None] | None = None
    if os.environ.get("SEMINAR_PREWARM_WHISPER", "1") == "1":
        warmup_task = asyncio.create_task(asyncio.to_thread(manager.transcriber.warmup))
    yield
    if warmup_task is not None and not warmup_task.done():
        await warmup_task


app = FastAPI(title="Seminar Copilot", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["content-type"],
)

@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "asr_backend": manager.transcriber.backend_name,
        "whisper_model": manager.transcriber.model,
        "whisper_state": manager.transcriber.state,
        "whisper_error": manager.transcriber.last_error,
        "demo_enabled": os.environ.get("SEMINAR_ENABLE_DEMO") == "1",
        "preset_count": len(presets),
    }


@app.get("/api/presets", response_model=list[SeminarPreset])
def get_presets() -> list[SeminarPreset]:
    return list(presets.values())


@app.post("/api/intake/parse", response_model=IntakeParseResponse)
async def intake_parse(request: IntakeParseRequest) -> IntakeParseResponse:
    parsed = await intake.parse_announcement(request.text)
    relevance = await intake.assess_relevance(parsed)
    speaker_sources, speaker_notes = intake.search_speaker_works(
        parsed.speaker, parsed.speaker_affiliation
    )
    topic_sources, topic_notes = intake.search_topic_works(parsed)
    sources = list(speaker_sources) + [
        source
        for source in topic_sources
        if source.title.lower() not in {s.title.lower() for s in speaker_sources}
    ]
    questions = await intake.propose_questions(parsed, relevance, sources)
    return IntakeParseResponse(
        parsed=parsed,
        relevance=relevance,
        research_sources=sources,
        research_notes=speaker_notes + topic_notes,
        questions=questions,
        question_method="deepseek" if questions else "none",
    )


@app.post("/api/intake/confirm", response_model=IntakeConfirmResponse)
def intake_confirm(request: IntakeConfirmRequest) -> IntakeConfirmResponse:
    if not request.questions:
        raise HTTPException(status_code=400, detail="至少需要一个备讲问题")
    preset_id = intake.build_preset_id(
        request.parsed.speaker,
        request.parsed.title,
        request.parsed.date,
        set(presets),
    )
    if not intake.validate_preset_id(preset_id):
        raise HTTPException(status_code=400, detail="无法生成合法的讲座标识")
    intake.write_intake_preset(request, preset_id, CONFIG_DIR)
    intake.write_dossier(request, preset_id, intake.DATA_DIR)
    reload_presets(presets)
    return IntakeConfirmResponse(preset_id=preset_id)


@app.get("/api/projects", response_model=list[ProjectSummary])
def get_projects() -> list[ProjectSummary]:
    return project_store().list_projects()


@app.get("/api/projects/{project_id}", response_model=ProjectDetail)
def get_project(project_id: str) -> ProjectDetail:
    try:
        return project_store().get_project(project_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到历史项目") from None


@app.get("/api/projects/{project_id}/active-session", response_model=SessionSnapshot)
def active_session_for_project(project_id: str) -> SessionSnapshot:
    """Return the in-memory live session for a project so a refreshed browser
    can rejoin the recording instead of hitting a dead end."""
    for session in manager.sessions.values():
        if session.project_id == project_id and session.status in {"recording", "stopping"}:
            return session.snapshot()
    raise HTTPException(status_code=404, detail="该项目没有正在进行的录音")


@app.post("/api/projects/{project_id}/continue", response_model=SessionSnapshot)
async def continue_project(
    project_id: str, request: ContinueProjectRequest
) -> SessionSnapshot:
    if not request.recording_permission_confirmed:
        raise HTTPException(status_code=400, detail="请先确认已获录音许可")
    store = project_store()
    try:
        preset_id = store.resolve_preset_id(project_id)
        session = manager.continue_project(
            project_id, preset_id, request.external_ai_enabled, request.auto_questions_enabled
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到历史项目") from None
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return session.snapshot()


@app.post("/api/projects/{project_id}/notes", response_model=ProjectNote)
def add_project_note(project_id: str, request: ProjectNoteRequest) -> ProjectNote:
    try:
        return project_store().add_note(
            project_id, request.text, request.audio_second
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到历史项目") from None


@app.get("/api/projects/{project_id}/sessions/{session_id}/audio.wav")
def get_project_audio(
    project_id: str, session_id: str, request: Request
) -> StreamingResponse:
    try:
        pcm_path, sample_rate = project_store().session_audio_path(
            project_id, session_id
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到录音阶段") from None
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="该阶段没有可播放音频") from None

    pcm_bytes = pcm_path.stat().st_size
    byte_rate = sample_rate * 2
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + pcm_bytes,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        byte_rate,
        2,
        16,
        b"data",
        pcm_bytes,
    )

    total_bytes = 44 + pcm_bytes
    start, end = _requested_byte_range(request.headers.get("range"), total_bytes)

    def stream() -> Iterator[bytes]:
        if start < 44:
            yield header[start : min(end + 1, 44)]
        pcm_start = max(0, start - 44)
        pcm_end = end - 44
        if pcm_end < 0:
            return
        remaining = pcm_end - pcm_start + 1
        with pcm_path.open("rb") as source:
            source.seek(pcm_start)
            while remaining > 0:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    partial = start > 0 or end < total_bytes - 1
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Content-Disposition": f'inline; filename="{session_id}.wav"',
    }
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{total_bytes}"

    return StreamingResponse(
        stream(),
        media_type="audio/wav",
        status_code=206 if partial else 200,
        headers=headers,
    )


def _append_jsonl(path: Path, payload: dict) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _retro_keyword_updates(
    questions: list[QuestionDefinition],
    transcript_rows: list[dict],
    max_per_question: int = 3,
) -> list[dict]:
    """对已结束的会话做关键词回扫，产出可回放的 analysis 更新。"""
    updates: list[dict] = []
    for definition in questions:
        hits_used = 0
        for row in transcript_rows:
            text = str(row.get("text") or "").strip()
            if not text:
                continue
            hits = keyword_hits(definition, text)
            if not hits:
                continue
            updates.append(
                {
                    "question_id": definition.id,
                    "status": "mention",
                    "evidence_quote": text,
                    "answer": "",
                    "missing": list(definition.expected_slots),
                    "confidence": min(0.65, 0.3 + 0.1 * len(hits)),
                }
            )
            hits_used += 1
            if hits_used >= max_per_question:
                break
    return updates


@app.post("/api/projects/{project_id}/attach", response_model=AttachSeminarResponse)
async def attach_seminar_info(
    project_id: str, request: AttachSeminarRequest
) -> AttachSeminarResponse:
    """为速录项目补充讲座信息：解析通知文本、生成预设与问题并回扫录音。"""
    # 录音中的会话可能尚未落盘，存在性以内存会话与磁盘项目任一为准
    live_sessions = [
        session
        for session in manager.sessions.values()
        if session.project_id == project_id and session.status in {"recording", "stopping"}
    ]
    detail = None
    try:
        detail = project_store().get_project(project_id)
    except KeyError:
        if not live_sessions:
            raise HTTPException(404, "项目不存在") from None
    if detail is not None and detail.preset_id != QUICK_RECORD_PRESET_ID:
        raise HTTPException(400, "该项目已绑定讲座信息，仅速录项目可补充")
    if any(session.preset.id != QUICK_RECORD_PRESET_ID for session in live_sessions):
        raise HTTPException(400, "该项目已绑定讲座信息，仅速录项目可补充")
    if not validate_preset_id(project_id):
        raise HTTPException(400, "项目 id 无法作为预设 id")

    parsed = await parse_announcement(request.text)
    relevance = await assess_relevance(parsed)
    questions = await propose_questions(parsed, relevance, [])
    preset = SeminarPreset(
        id=project_id,
        title=parsed.title or "未命名讲座",
        speaker=parsed.speaker or "未知演讲者",
        date=parsed.date or date_type.today().isoformat(),
        source_path=f"config/seminars/{project_id}.json",
        glossary=_clean_glossary(
            [
                *parsed.topic_keywords,
                *(keyword for question in questions for keyword in question.keywords),
            ]
        ),
        language="auto",
        questions=questions,
    )
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (CONFIG_DIR / f"{project_id}.json").write_text(
        json.dumps(preset.model_dump(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    presets[project_id] = preset

    matched_ids: set[str] = set()
    live_ids: set[str] = set()
    for session in live_sessions:
        live_ids.add(session.id)
        session.attach_seminar_info(preset)
        for state in session.questions:
            if state.evidence:
                matched_ids.add(state.id)
        await session.broadcast()

    # 已落盘的会话：改写 manifest 并追加合成分析行（引用为原句，可过 grounded 校验）
    for session_dir in sorted(manager.root.iterdir()):
        if not session_dir.is_dir() or session_dir.name in live_ids:
            continue
        manifest_path = session_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("project_id") != project_id:
            continue
        transcript_rows = _read_jsonl(session_dir / "transcript.jsonl")
        updates = _retro_keyword_updates(questions, transcript_rows)
        if updates:
            _append_jsonl(
                session_dir / "analysis.jsonl",
                {
                    "at_audio_second": manifest.get("audio_seconds", 0.0),
                    "window_start": 0.0,
                    "updates": updates,
                    "followups": [],
                    "source": "attach-seminar-info",
                },
            )
            matched_ids.update(str(item["question_id"]) for item in updates)
        manifest["preset_id"] = project_id
        manifest["language"] = preset.language
        manifest["source_path"] = preset.source_path
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    return AttachSeminarResponse(
        preset_id=project_id,
        preset=preset,
        questions=questions,
        question_method="deepseek" if questions else "none",
        matched_questions=len(matched_ids),
    )


def _requested_byte_range(value: str | None, total_bytes: int) -> tuple[int, int]:
    if not value or not value.startswith("bytes="):
        return 0, total_bytes - 1
    raw = value.removeprefix("bytes=").split(",", 1)[0].strip()
    try:
        start_text, end_text = raw.split("-", 1)
        if not start_text:
            length = min(total_bytes, int(end_text))
            return total_bytes - length, total_bytes - 1
        start = int(start_text)
        end = min(total_bytes - 1, int(end_text)) if end_text else total_bytes - 1
        if start < 0 or start > end or start >= total_bytes:
            raise ValueError
        return start, end
    except (TypeError, ValueError):
        raise HTTPException(status_code=416, detail="无效的音频字节范围") from None


@app.post("/api/sessions", response_model=SessionSnapshot)
async def create_session(request: StartSessionRequest) -> SessionSnapshot:
    if not request.recording_permission_confirmed:
        raise HTTPException(status_code=400, detail="请先确认已获录音许可")
    try:
        session = manager.create(
            request.preset_id, request.external_ai_enabled, request.auto_questions_enabled
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到讲座预设") from None
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return session.snapshot()


@app.get("/api/sessions/{session_id}", response_model=SessionSnapshot)
def get_session(session_id: str) -> SessionSnapshot:
    try:
        return manager.get(session_id).snapshot()
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到录音会话") from None


@app.post("/api/sessions/{session_id}/analyze", response_model=SessionSnapshot)
async def analyze_now(session_id: str) -> SessionSnapshot:
    try:
        session = manager.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到录音会话") from None
    if not session.external_ai_enabled:
        raise HTTPException(status_code=400, detail="当前会话未开启 DeepSeek 分析")
    await session.deep_analyze()
    return session.snapshot()


@app.post("/api/sessions/{session_id}/temporary-questions", response_model=SessionSnapshot)
async def add_temporary_question(
    session_id: str, request: TemporaryQuestionRequest
) -> SessionSnapshot:
    try:
        session = manager.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到录音会话") from None
    if session.status != "recording":
        raise HTTPException(status_code=409, detail="录音结束后不能新增现场问题")
    session.add_temporary_question(request.draft, request.search_external)
    await session.broadcast()
    return session.snapshot()


@app.post("/api/sessions/{session_id}/stop", response_model=SessionSnapshot)
async def stop_session(session_id: str) -> SessionSnapshot:
    try:
        session = manager.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到录音会话") from None
    await session.stop()
    return session.snapshot()


@app.post("/api/sessions/{session_id}/export", response_model=ExportResponse)
def export_session(session_id: str) -> ExportResponse:
    try:
        session = manager.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到录音会话") from None
    default_export = str(Path.home() / "Documents" / "SeminarCopilot" / "exports")
    export_dir = Path(os.environ.get("SEMINAR_OBSIDIAN_EXPORT_DIR", default_export)).expanduser()
    target = session.export_markdown(export_dir)
    return ExportResponse(path=str(target))


@app.post("/api/sessions/{session_id}/demo", response_model=SessionSnapshot)
async def load_demo(session_id: str) -> SessionSnapshot:
    if os.environ.get("SEMINAR_ENABLE_DEMO") != "1":
        raise HTTPException(status_code=404, detail="演示模式未开启")
    try:
        session = manager.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到录音会话") from None
    session.add_demo_content()
    await session.broadcast()
    return session.snapshot()


@app.websocket("/ws/sessions/{session_id}")
async def session_audio(websocket: WebSocket, session_id: str) -> None:
    try:
        session = manager.get(session_id)
    except KeyError:
        await websocket.close(code=4404, reason="session not found")
        return
    await websocket.accept()
    session.sockets.add(websocket)
    await websocket.send_text(session.snapshot().model_dump_json())
    try:
        while True:
            message = await websocket.receive()
            if message.get("bytes") is not None:
                session.append_pcm(message["bytes"])
            elif message.get("text") == "ping":
                await websocket.send_text(session.snapshot().model_dump_json())
    except WebSocketDisconnect:
        session.sockets.discard(websocket)
    except Exception:
        session.sockets.discard(websocket)
        await websocket.close(code=1011)
