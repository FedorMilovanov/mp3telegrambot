from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dub_health_checks_clean_entrypoints_only() -> None:
    source = (ROOT / "handlers" / "dub_health.py").read_text(encoding="utf-8")
    assert "tools.voxcpm2.generic_clean_gemini_runtime" in source
    assert "tools.voxcpm2.generic_clean_direct_runtime" in source
    assert "tools.voxcpm2.generic_clean_audio_repair_runtime" in source
    assert "Clean Direct NoChew + независимый QA" in source
    assert "NoChew Quality v4.2 + аудиоремонт" not in source
    assert "generic_direct_checked_runtime" not in source
    assert 'semantic_tts_guard_v4.install()" in contract_text' not in source


def test_dub_health_keeps_fourteen_checks() -> None:
    source = (ROOT / "handlers" / "dub_health.py").read_text(encoding="utf-8")
    # Three recipe checks, one clean production contract, Whisper, SoundFile,
    # ffmpeg, ffprobe, CPU Python, archive, Gemini keys, storage, worker, UTF-8.
    assert source.count("checks.append(") == 12
    assert 'for binary in ("ffmpeg", "ffprobe")' in source
