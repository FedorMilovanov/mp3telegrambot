from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.voxcpm2.generic_gemini_runtime import (
    clean_manual_caption_line,
    parse_creator_vtt_preserving_text,
    validate_completed_outputs,
)


def _touch(path: Path, payload: bytes = b"result") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_manual_captions_preserve_meaningful_square_brackets(tmp_path: Path) -> None:
    vtt = tmp_path / "manual.en.vtt"
    vtt.write_text(
        """WEBVTT

00:00:00.000 --> 00:00:02.000
<c>[John 3:16] God so loved the world.</c>

00:00:02.000 --> 00:00:03.000
[Music]
""",
        encoding="utf-8",
    )
    cues = parse_creator_vtt_preserving_text(vtt)
    assert len(cues) == 1
    assert cues[0].text == "[John 3:16] God so loved the world."
    assert clean_manual_caption_line("[Applause]") == ""


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
