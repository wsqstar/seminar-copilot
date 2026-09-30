from __future__ import annotations

import os
import tempfile
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class AsrSegment:
    start: float
    end: float
    text: str


class WhisperTranscriber:
    """Lazy, process-resident MLX Whisper adapter."""

    backend_name = "mlx"

    def __init__(self) -> None:
        self.model = os.environ.get(
            "SEMINAR_WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo"
        )
        self._lock = threading.Lock()
        self.state = "cold"
        self.last_error = ""

    def warmup(self) -> None:
        if self.state == "ready":
            return
        self.transcribe(np.zeros(16_000 * 3, dtype=np.float32), "warmup")

    def transcribe(
        self, audio: np.ndarray, prompt: str, language: str | None = None
    ) -> list[AsrSegment]:
        if audio.size == 0:
            return []
        # Import lazily so API/tests can start on hosts without a Metal device.
        import mlx_whisper

        # SEMINAR_WHISPER_LANGUAGE overrides the preset; "auto"/empty means detect.
        lang = os.environ.get("SEMINAR_WHISPER_LANGUAGE") or language or "auto"
        lang = None if lang.strip().lower() in {"", "auto"} else lang.strip()

        with self._lock:
            self.state = "loading"
            try:
                result = mlx_whisper.transcribe(
                    audio.astype(np.float32, copy=False),
                    path_or_hf_repo=self.model,
                    language=lang,
                    temperature=0.0,
                    verbose=None,
                    condition_on_previous_text=True,
                    initial_prompt=prompt or None,
                    word_timestamps=False,
                )
            except Exception as exc:
                self.state = "error"
                self.last_error = str(exc)
                raise
            self.state = "ready"
            self.last_error = ""
        return [
            AsrSegment(
                start=float(segment["start"]),
                end=float(segment["end"]),
                text=str(segment["text"]).strip(),
            )
            for segment in result.get("segments", [])
            if str(segment.get("text", "")).strip()
        ]


class BailianTranscriber:
    """阿里云百炼（DashScope）短语音同步识别适配器。

    面向无本地算力的用户：把每个转录窗口写成临时 wav，调用
    dashscope.audio.asr.Recognition（paraformer 系列，单次 ≤60s）
    同步识别。glossary prompt 在 paraformer 上不可用，直接忽略；
    术语纠偏依赖后期 attach / 分析环节。依赖可选安装：
    `uv sync --extra bailian`，密钥走 DASHSCOPE_API_KEY 环境变量。
    """

    backend_name = "bailian"

    def __init__(self) -> None:
        self.model = os.environ.get("SEMINAR_BAILIAN_MODEL", "paraformer-v2")
        self._lock = threading.Lock()
        self.state = "cold"
        self.last_error = ""

    def warmup(self) -> None:
        if self.state == "ready":
            return
        if not self._api_key():
            self.state = "error"
            self.last_error = "未配置 DASHSCOPE_API_KEY，百炼语音识别不可用"
            return
        try:
            self.transcribe(np.zeros(16_000 * 3, dtype=np.float32), "warmup")
        except Exception:
            pass  # state/last_error 已在 transcribe 内记录

    def transcribe(
        self, audio: np.ndarray, prompt: str, language: str | None = None
    ) -> list[AsrSegment]:
        if audio.size == 0:
            return []
        if not self._api_key():
            self.state = "error"
            self.last_error = "未配置 DASHSCOPE_API_KEY，百炼语音识别不可用"
            raise RuntimeError(self.last_error)
        try:
            from dashscope.audio.asr import Recognition
        except ImportError as exc:
            self.state = "error"
            self.last_error = "未安装 dashscope：uv sync --extra bailian"
            raise RuntimeError(self.last_error) from exc

        wav_path = _write_temp_wav(audio)
        try:
            kwargs: dict[str, Any] = {
                "model": self.model,
                "format": "wav",
                "sample_rate": 16_000,
            }
            hints = _bailian_language_hints(language)
            if hints:
                kwargs["language_hints"] = hints
            with self._lock:
                self.state = "loading"
                try:
                    result = Recognition(**kwargs).call(str(wav_path))
                except Exception as exc:
                    self.state = "error"
                    self.last_error = str(exc)
                    raise
            status = getattr(result, "status_code", 200)
            if status != 200:
                message = getattr(result, "message", "") or getattr(result, "code", "")
                raise RuntimeError(f"百炼识别失败（HTTP {status}）：{message}")
            segments = _extract_sentences(result)
            self.state = "ready"
            self.last_error = ""
            return segments
        finally:
            wav_path.unlink(missing_ok=True)

    @staticmethod
    def _api_key() -> str:
        return os.environ.get("DASHSCOPE_API_KEY", "").strip()


def _write_temp_wav(audio: np.ndarray) -> Path:
    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    fd, name = tempfile.mkstemp(prefix="seminar-asr-", suffix=".wav")
    with os.fdopen(fd, "wb") as handle:
        with wave.open(handle, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16_000)
            wav.writeframes(pcm16.tobytes())
    return Path(name)


def _bailian_language_hints(language: str | None) -> list[str]:
    lang = os.environ.get("SEMINAR_ASR_LANGUAGE") or language or "auto"
    lang = lang.strip().lower()
    if lang in {"zh", "zh-cn", "chinese"}:
        return ["zh"]
    if lang in {"en", "en-us", "english"}:
        return ["en"]
    return []  # auto：paraformer-v2 在中英文间自动判别


def _extract_sentences(result: Any) -> list[AsrSegment]:
    sentences: Any = None
    get_sentence = getattr(result, "get_sentence", None)
    if callable(get_sentence):
        try:
            sentences = get_sentence()
        except Exception:
            sentences = None
    if sentences is None:
        output = getattr(result, "output", None) or {}
        if isinstance(output, dict):
            sentences = output.get("sentence") or output.get("sentences") or []
    segments: list[AsrSegment] = []
    for item in sentences or []:
        if not isinstance(item, dict):
            item = vars(item) if hasattr(item, "__dict__") else {}
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        start = float(item.get("begin_time", 0) or 0) / 1000.0
        end = float(item.get("end_time", 0) or 0) / 1000.0
        segments.append(AsrSegment(start=start, end=max(end, start), text=text))
    return segments


def create_transcriber() -> WhisperTranscriber | BailianTranscriber:
    """按 SEMINAR_ASR_BACKEND 选择 ASR 后端（默认 mlx 本地 Whisper）。"""

    backend = os.environ.get("SEMINAR_ASR_BACKEND", "mlx").strip().lower()
    if backend in {"bailian", "dashscope", "aliyun"}:
        return BailianTranscriber()
    if backend in {"mlx", "whisper", ""}:
        return WhisperTranscriber()
    raise ValueError(f"未知 ASR 后端: {backend}（可选 mlx / bailian）")

