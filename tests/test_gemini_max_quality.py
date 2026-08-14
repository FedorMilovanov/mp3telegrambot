"""Regression contracts for the project-wide quality/cost policy."""
from pathlib import Path

from services import gemini_max_quality as quality


def test_heavy_quality_runtime_forces_36_high_on_shared_helpers():
    src = Path("services/gemini_max_quality.py").read_text(encoding="utf-8")
    assert '_HEAVY_MODEL = "gemini-3.6-flash"' in src
    assert '_REQUIRED_WHISPER_MODEL = "large-v3"' in src
    assert 'os.environ[name] = _HEAVY_MODEL' in src
    assert 'os.environ[name] = "high"' in src
    assert 'os.environ[name] = _REQUIRED_WHISPER_MODEL' in src
    assert "_apply_thinking_policy" in src
    assert "make_text_config_smart = max_text_smart" in src
    assert "make_audio_config = max_audio" in src
    assert "make_text_config = max_text_legacy" in src


def test_model_aware_thinking_is_high_for_36_minimal_for_utility_35():
    def capture(*args, **kwargs):
        return args, kwargs

    _, heavy = quality._apply_thinking_policy(
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

    assert heavy["thinking_level"] == "high"
    assert light["thinking_level"] == "minimal"
    assert light_fallback["thinking_level"] == "minimal"


def test_utility_work_uses_35_quota_without_semantic_fallback():
    src = Path("services/gemini_max_quality.py").read_text(encoding="utf-8")
    assert '_LIGHT_MODEL = "gemini-3.5-flash-lite"' in src
    assert '_LIGHT_FALLBACK_MODEL = "gemini-3.5-flash"' in src
    assert 'os.environ["GEMINI_LIGHT_MODEL"] = _LIGHT_MODEL' in src
    assert 'os.environ["GEMINI_LIGHT_FALLBACK_MODELS"] = _LIGHT_FALLBACK_MODEL' in src
    assert 'os.environ["GEMINI_LIGHT_ALLOW_MAIN_FALLBACK"] = "0"' in src
    assert 'os.environ["LIVEDUB_PUBLICATION_FALLBACK_MODELS"] = ""' in src
    assert 'os.environ["LIVEDUB_PUBLICATION_ALLOW_STRONG_FALLBACK"] = "0"' in src
    assert "gemini-3.1" not in src
    assert "gemini-2.5" not in src


def test_publication_metadata_directly_owns_36_high_quality_route():
    publication = Path("services/livedub_publication_core.py").read_text(
        encoding="utf-8"
    )
    resilience = Path("services/gemini36_factory_resilience.py").read_text(
        encoding="utf-8"
    )
    assert '_PUBLICATION_MODEL = "gemini-3.6-flash"' in publication
    assert 'thinking_level="high"' in publication
    assert "GEMINI_LIGHT_MODEL" not in publication
    assert "temperature=" not in publication
    assert "_verify_publication_quality_route" in resilience
    assert "publication.publication_models =" not in resilience


def test_services_installs_max_quality_before_general_policy():
    src = Path("services/__init__.py").read_text(encoding="utf-8")
    assert "configure_max_quality_env" in src
    assert "install_max_quality_runtime" in src
    assert src.index("configure_max_quality_env()") < src.index("configure_gemini_policy()")
    assert src.index("install_max_quality_runtime()") < src.index("install_livedub_quality_runtime()")


def test_env_migration_preserves_semantic_36_utility_35_split():
    src = Path("scripts/migrate-gemini-36.ps1").read_text(encoding="utf-8")
    assert 'GEMINI_MODEL" -Value "gemini-3.6-flash"' in src
    assert 'SHORTS_FACTORY_MODEL" -Value "gemini-3.6-flash"' in src
    assert 'GEMINI_FORCE_THINKING_LEVEL" -Value "high"' in src
    assert 'LIVEDUB_QUICK_QA_THINKING" -Value "high"' in src
    assert 'LIVEDUB_LONG_QA_THINKING" -Value "high"' in src
    assert 'LIVEDUB_INFO_THINKING" -Value "high"' in src
    assert 'GEMINI_LIGHT_MODEL" -Value "gemini-3.5-flash-lite"' in src
    assert 'GEMINI_LIGHT_FALLBACK_MODELS" -Value "gemini-3.5-flash"' in src
    assert 'GEMINI_LIGHT_ALLOW_MAIN_FALLBACK" -Value "0"' in src
    assert 'SHORTS_FACTORY_GEMINI_AUDIO_BITRATE_KBPS" -Value "128"' in src
    assert 'SHORTS_FACTORY_GEMINI_AUDIO_SAMPLE_RATE" -Value "48000"' in src
    assert 'GEMINI_SERVICE_TIER" -Value "priority"' in src
    assert 'WHISPER_MODEL" -Value "large-v3"' in src
    assert 'WHISPER_ENG_SUBTITLES_MODEL" -Value "large-v3"' in src
    assert "gemini-3.1" not in src
    assert "gemini-2.5" not in src
