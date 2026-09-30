from __future__ import annotations

import os
import threading
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class AsrSegment:
    start: float
    end: float
    text: str


class WhisperTranscriber:
    """Lazy, process-resident MLX Whisper adapter."""

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
