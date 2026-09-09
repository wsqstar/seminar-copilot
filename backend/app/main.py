from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .models import (
    ExportResponse,
    SeminarPreset,
    SessionSnapshot,
    StartSessionRequest,
    TemporaryQuestionRequest,
)
from .presets import load_presets
from .session import SessionManager


presets = load_presets()
manager = SessionManager(presets)


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


@app.post("/api/sessions", response_model=SessionSnapshot)
async def create_session(request: StartSessionRequest) -> SessionSnapshot:
    if not request.recording_permission_confirmed:
        raise HTTPException(status_code=400, detail="请先确认已获录音许可")
    try:
        session = manager.create(request.preset_id, request.external_ai_enabled)
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到讲座预设") from None
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
