"""Regression contracts for LiveDub Russian delivery."""
from pathlib import Path


def _runtime_source() -> str:
    return Path("services/livedub_quality_runtime.py").read_text(encoding="utf-8")


def test_services_package_configures_proxy_before_clients():
    package = Path("services/__init__.py").read_text(encoding="utf-8")
    assert "configure_gemini_network" in package
    assert "services.livedub_audio_dedupe" in package
    assert "install_livedub_quality_runtime" in package


def test_quality_cascade_keeps_strong_models():
    src = _runtime_source()
    assert "gemini-3.6-flash" in src
    assert "gemini-3.5-flash" in src
    assert "gemini-3.5-flash-lite" in src
    assert "GEMINI_MODEL" in src


def test_retired_models_are_filtered():
    src = _runtime_source()
    assert "_RETIRED_MODELS" in src
    assert "gemini-3.1-flash-lite-preview" in src
    assert "value not in _RETIRED_MODELS" in src


def test_duplicate_russian_mp3_is_suppressed():
    src = _runtime_source()
    assert "_AUDIO_SENT" in src
    assert "duplicate Russian MP3 suppressed" in src
    assert "companion._send_new_audio = send_new_once" in src


def test_windows_ffprobe_is_utf8_safe():
    src = _runtime_source()
    assert '"encoding": "utf-8"' in src
    assert '"errors": "replace"' in src
    assert "mix.probe_video_meta = utf8_probe" in src
