from __future__ import annotations

import json
from pathlib import Path

from .models import SeminarPreset


CONFIG_DIR = Path(__file__).resolve().parents[1] / "config" / "seminars"


def load_presets() -> dict[str, SeminarPreset]:
    presets: dict[str, SeminarPreset] = {}
    for path in sorted(CONFIG_DIR.glob("*.json")):
        preset = SeminarPreset.model_validate(json.loads(path.read_text(encoding="utf-8")))
        if preset.id in presets:
            raise ValueError(f"duplicate preset id: {preset.id}")
        presets[preset.id] = preset
    return presets
