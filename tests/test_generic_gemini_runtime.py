from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.voxcpm2.generic_gemini_runtime import validate_completed_outputs


def _touch(path: Path, payload: bytes = b"result") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_gemini_output_contract_requires_real_primary_video(tmp_path: Path) -> None:
    output = tmp_path / "output"
    mixed = output / "final_upload.mp4"
    russian = output / "russian_only.mp4"
    named = output / "Русское название — русский дубляж.mp4"
    _touch(mixed)
    _touch(russian)
    _touch(named)
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "phase": "completed",
                "translation_mode": "gemini",
                "telegram_outputs": [
                    {"path": str(named), "primary": True, "video": True}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    manifest = validate_completed_outputs(tmp_path)
    assert manifest["phase"] == "completed"


def test_gemini_output_contract_rejects_missing_video(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir(parents=True)
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "phase": "completed",
                "translation_mode": "gemini",
                "telegram_outputs": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="главный MP4"):
        validate_completed_outputs(tmp_path)
