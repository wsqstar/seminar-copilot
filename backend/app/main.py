from __future__ import annotations

import asyncio
import os
import struct
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .models import (
    ContinueProjectRequest,
    ExportResponse,
    ProjectDetail,
    ProjectNote,
    ProjectNoteRequest,
    ProjectSummary,
    SeminarPreset,
    SessionSnapshot,
    StartSessionRequest,
    TemporaryQuestionRequest,
)
from .presets import load_presets
from .projects import ProjectStore
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
        "whisper_model": manager.transcriber.model,
        "whisper_state": manager.transcriber.state,
        "whisper_error": manager.transcriber.last_error,
        "demo_enabled": os.environ.get("SEMINAR_ENABLE_DEMO") == "1",
        "preset_count": len(presets),
    }


@app.get("/api/presets", response_model=list[SeminarPreset])
def get_presets() -> list[SeminarPreset]:
    return list(presets.values())


@app.get("/api/projects", response_model=list[ProjectSummary])
def get_projects() -> list[ProjectSummary]:
    return project_store().list_projects()


@app.get("/api/projects/{project_id}", response_model=ProjectDetail)
def get_project(project_id: str) -> ProjectDetail:
    try:
        return project_store().get_project(project_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到历史项目") from None


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
            project_id, preset_id, request.external_ai_enabled
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
        session = manager.create(request.preset_id, request.external_ai_enabled)
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
