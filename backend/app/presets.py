from __future__ import annotations

import json
from datetime import date as date_type
from pathlib import Path

from .models import SeminarPreset


CONFIG_DIR = Path(__file__).resolve().parents[1] / "config" / "seminars"

# 速录模式：先录音，事后通过 /api/projects/{id}/attach 补讲座信息。
QUICK_RECORD_PRESET_ID = "quick-record"


def quick_record_preset() -> SeminarPreset:
    return SeminarPreset(
        id=QUICK_RECORD_PRESET_ID,
        title="现场速录（待补充信息）",
        speaker="待补充",
        date=date_type.today().isoformat(),
        source_path=None,
        glossary=[],
        language="auto",
        questions=[],
    )


def load_presets() -> dict[str, SeminarPreset]:
    presets: dict[str, SeminarPreset] = {}
    for path in sorted(CONFIG_DIR.glob("*.json")):
        preset = SeminarPreset.model_validate(json.loads(path.read_text(encoding="utf-8")))
        if preset.id in presets:
            raise ValueError(f"duplicate preset id: {preset.id}")
        presets[preset.id] = preset
    presets.setdefault(QUICK_RECORD_PRESET_ID, quick_record_preset())
    return presets


def reload_presets(current: dict[str, SeminarPreset]) -> dict[str, SeminarPreset]:
    fresh = load_presets()
    current.clear()
    current.update(fresh)
    return current
