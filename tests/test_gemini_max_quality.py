"""Regression contracts for the project-wide maximum-quality policy."""
from pathlib import Path

from services import gemini_max_quality as quality


def test_heavy_quality_runtime_forces_37_high_with_36_high_fallback():
    src = Path("services/gemini_max_quality.py").read_text(encoding="utf-8")
    assert '_HEAVY_MODEL = "gemini-3.7-flash"' in src
    assert '_HEAVY_FALLBACK_MODEL = "gemini-3.6-flash"' in src
    assert '_REQUIRED_WHISPER_MODEL = "large-v3"' in src
    assert 'os.environ[name] = _HEAVY_MODEL' in src
    assert 'os.environ["GEMINI_QUALITY_FALLBACK_MODELS"] = _HEAVY_FALLBACK_MODEL' in src
    assert 'os.environ["SHORTS_FACTORY_FALLBACK_MODELS"] = _HEAVY_FALLBACK_MODEL' in src
    assert 'os.environ[name] = "high"' in src
    assert 'os.environ[name] = _REQUIRED_WHISPER_MODEL' in src
    assert "install_gemini37_quality_routes" in src


def test_model_aware_thinking_is_high_for_37_and_36_minimal_for_utility_35():
    def capture(*args, **kwargs):
        return args, kwargs

    _, primary = quality._apply_thinking_policy(
        capture,
        (),
        {"model_name": "gemini-3.7-flash", "thinking_level": "minimal"},
    )
    _, fallback = quality._apply_thinking_policy(
        capture,
        (),
        {"model_name": "gemini-3.6-flash", "thinking_level": "minimal"},
    )
    _, light = quality._apply_thinking_policy(
        capture,
        (),
        {"model_name": "gemini-3.5-flash-lite", "thinking_level": "high"},
    )
    _, light_fallback = quality._apply_thinking_policy(
        capture,
        (),
        {"model_name": "gemini-3.5-flash", "thinking_level": "high"},
    )

    assert primary["thinking_level"] == "high"
    assert fallback["thinking_level"] == "high"
    assert light["thinking_level"] == "minimal"
    assert light_fallback["thinking_level"] == "minimal"


def test_configure_env_promotes_all_semantic_routes(monkeypatch):
    quality.configure_max_quality_env()
    import os

    assert os.environ["GEMINI_MODEL"] == "gemini-3.7-flash"
    assert os.environ["GEMINI_MAX_MODEL"] == "gemini-3.7-flash"
    assert os.environ["GEMINI_QUALITY_FALLBACK_MODELS"] == "gemini-3.6-flash"
    assert os.environ["SHORTS_FACTORY_MODEL"] == "gemini-3.7-flash"
    assert os.environ["SHORTS_FACTORY_FALLBACK_MODELS"] == "gemini-3.6-flash"
    assert os.environ["LIVEDUB_INFO_MODEL"] == "gemini-3.7-flash"
    assert os.environ["LIVEDUB_INFO_FALLBACK_MODELS"] == "gemini-3.6-flash"
    assert os.environ["LIVEDUB_QUICK_QA_MODEL"] == "gemini-3.7-flash"
    assert os.environ["LIVEDUB_LONG_QA_MODEL"] == "gemini-3.7-flash"
    assert os.environ["LIVEDUB_QA_VERIFY_MODEL"] == "gemini-3.7-flash"
    assert os.environ["GEMINI_FORCE_THINKING_LEVEL"] == "high"
    assert os.environ["WHISPER_MODEL"] == "large-v3"


def test_utility_work_keeps_separate_35_quota_without_semantic_fallback():
    src = Path("services/gemini_max_quality.py").read_text(encoding="utf-8")
    assert '_LIGHT_MODEL = "gemini-3.5-flash-lite"' in src
    assert '_LIGHT_FALLBACK_MODEL = "gemini-3.5-flash"' in src
    assert 'os.environ["GEMINI_LIGHT_MODEL"] = _LIGHT_MODEL' in src
    assert 'os.environ["GEMINI_LIGHT_FALLBACK_MODELS"] = _LIGHT_FALLBACK_MODEL' in src
    assert 'os.environ["GEMINI_LIGHT_ALLOW_MAIN_FALLBACK"] = "0"' in src
    assert "gemini-3.1" not in src
    assert "gemini-2.5" not in src


def test_services_installs_max_quality_before_general_policy():
    src = Path("services/__init__.py").read_text(encoding="utf-8")
    assert "configure_max_quality_env" in src
    assert "install_max_quality_runtime" in src
    assert src.index("configure_max_quality_env()") < src.index("configure_gemini_policy()")
    assert src.index("install_max_quality_runtime()") < src.index("install_livedub_quality_runtime()")


def test_validated_entrypoint_guarantees_37_compat_before_factory_post_main():
    src = Path("bot_new.py").read_text(encoding="utf-8")
    assert "install_max_quality_runtime()" in src
    assert src.index("install_max_quality_runtime()") < src.index("bootstrap_post_main(_main_module)")


def test_gemini_37_env_migration_preserves_quality_chain_and_large_v3():
    src = Path("scripts/migrate-gemini-37.ps1").read_text(encoding="utf-8")
    assert 'GEMINI_MODEL" -Value "gemini-3.7-flash"' in src
    assert 'GEMINI_QUALITY_FALLBACK_MODELS" -Value "gemini-3.6-flash"' in src
    assert 'SHORTS_FACTORY_MODEL" -Value "gemini-3.7-flash"' in src
    assert 'SHORTS_FACTORY_FALLBACK_MODELS" -Value "gemini-3.6-flash"' in src
    assert 'GEMINI_FORCE_THINKING_LEVEL" -Value "high"' in src
    assert 'LIVEDUB_QUICK_QA_THINKING" -Value "high"' in src
    assert 'LIVEDUB_LONG_QA_THINKING" -Value "high"' in src
    assert 'LIVEDUB_INFO_THINKING" -Value "high"' in src
    assert 'GEMINI_LIGHT_MODEL" -Value "gemini-3.5-flash-lite"' in src
    assert 'GEMINI_LIGHT_FALLBACK_MODELS" -Value "gemini-3.5-flash"' in src
    assert 'GEMINI_LIGHT_ALLOW_MAIN_FALLBACK" -Value "0"' in src
    assert 'WHISPER_MODEL" -Value "large-v3"' in src
    assert 'WHISPER_ENG_SUBTITLES_MODEL" -Value "large-v3"' in src
    assert "gemini-3.1" not in src
    assert "gemini-2.5" not in src
