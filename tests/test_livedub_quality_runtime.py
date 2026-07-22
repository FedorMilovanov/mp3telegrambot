"""Regression contracts for Gemini/LiveDub Russian delivery."""
from pathlib import Path


def _runtime_source() -> str:
    return Path("services/livedub_quality_runtime.py").read_text(encoding="utf-8")


def test_services_package_configures_policy_and_proxy_before_clients():
    package = Path("services/__init__.py").read_text(encoding="utf-8")
    assert "configure_gemini_policy" in package
    assert "configure_gemini_network" in package
    assert "services.livedub_audio_dedupe" in package
    assert "install_livedub_quality_runtime" in package


def test_quality_cascade_keeps_strong_models():
    src = _runtime_source()
    assert '_PRIMARY_MODEL = "gemini-3.6-flash"' in src
    assert '_STRONG_FALLBACK_MODEL = "gemini-3.5-flash"' in src
    assert '_LIGHT_MODEL = "gemini-3.5-flash-lite"' in src
    assert "GEMINI_MODEL" in src


def test_former_main_default_is_auto_migrated_to_36():
    src = _runtime_source()
    assert "current_main == _STRONG_FALLBACK_MODEL" in src
    assert 'os.environ["GEMINI_MODEL"] = _PRIMARY_MODEL' in src
    assert 'os.environ.setdefault("LIVEDUB_QUICK_QA_MODEL", _PRIMARY_MODEL)' in src


def test_light_work_uses_current_flash_lite():
    src = _runtime_source()
    assert 'os.environ["GEMINI_LIGHT_MODEL"] = _LIGHT_MODEL' in src
    assert "gemini-3.5-flash-lite" in src


def test_gemini_31_is_retired_not_an_active_fallback():
    src = _runtime_source()
    assert '"gemini-3.1-flash-lite"' in src.split("_RETIRED_MODELS", 1)[1]
    assert 'f"{_STRONG_FALLBACK_MODEL},{_LIGHT_MODEL}"' in src
    script = Path("scripts/migrate-gemini-36.ps1").read_text(encoding="utf-8")
    assert "gemini-3.1" not in script


def test_dead_local_proxy_falls_back_to_system_tun():
    src = _runtime_source()
    assert "_proxy_reachable" in src
    assert "_clear_dead_proxy" in src
    assert "system TUN (local proxy" in src


def test_yandex_tts_fallback_is_enabled_but_explicitly_marked():
    src = _runtime_source()
    assert 'os.environ.setdefault("LIVEDUB_TTS_FALLBACK", "1")' in src
    mix = Path("services/livedub_mix.py").read_text(encoding="utf-8")
    assert '.voice_style_tts' in mix


def test_retired_models_are_filtered():
    src = _runtime_source()
    assert "_RETIRED_MODELS" in src
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


def test_env_migration_script_sets_quality_first_models():
    script = Path("scripts/migrate-gemini-36.ps1").read_text(encoding="utf-8")
    assert 'GEMINI_MODEL" -Value "gemini-3.6-flash' in script
    assert 'LIVEDUB_INFO_MODEL" -Value "gemini-3.6-flash' in script
    assert 'LIVEDUB_QUICK_QA_MODEL" -Value "gemini-3.6-flash' in script
    assert 'GEMINI_LIGHT_MODEL" -Value "gemini-3.5-flash-lite' in script
    assert 'LIVEDUB_INFO_FALLBACK_MODELS" -Value "gemini-3.5-flash,gemini-3.5-flash-lite' in script
    assert ".bak-gemini36-" in script
