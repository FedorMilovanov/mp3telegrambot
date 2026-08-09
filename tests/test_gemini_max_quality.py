"""Regression contracts for the project-wide maximum-quality policy."""
from pathlib import Path


def test_max_quality_runtime_forces_36_high_on_shared_quality_helpers():
    src = Path("services/gemini_max_quality.py").read_text(encoding="utf-8")
    assert '_PRIMARY_MODEL = "gemini-3.6-flash"' in src
    assert '_REQUIRED_WHISPER_MODEL = "large-v3"' in src
    assert 'os.environ[name] = _PRIMARY_MODEL' in src
    assert 'os.environ[name] = "high"' in src
    assert 'os.environ[name] = _REQUIRED_WHISPER_MODEL' in src
    assert 'positional[3] = "high"' in src
    assert 'options["thinking_level"] = "high"' in src
    assert "make_text_config_smart = max_text_smart" in src
    assert "make_audio_config = max_audio" in src
    assert "make_text_config = max_text_legacy" in src


def test_max_quality_policy_disables_weaker_model_fallbacks():
    src = Path("services/gemini_max_quality.py").read_text(encoding="utf-8")
    assert '"GEMINI_LIGHT_FALLBACK_MODELS"' in src
    assert '"LIVEDUB_INFO_FALLBACK_MODELS"' in src
    assert '"LIVEDUB_PUBLICATION_FALLBACK_MODELS"' in src
    assert 'os.environ[name] = ""' in src
    assert "gemini-3.5-flash" not in src
    assert "gemini-3.5-flash-lite" not in src
    assert "gemini-3.1" not in src
    assert "gemini-2.5" not in src


def test_publication_metadata_is_36_high_not_lite_minimal():
    policy = Path("services/gemini_max_quality.py").read_text(encoding="utf-8")
    publication = Path("services/livedub_publication_core.py").read_text(
        encoding="utf-8"
    )
    assert "publication.publication_models = publication_models" in policy
    assert "publication._economy_config = publication_config" in policy
    assert 'globals_module._build_thinking_config("high")' in policy
    assert '"max_output_tokens": 1400' in policy
    # The historical implementation may remain import-safe on disk, but the
    # production runtime must supersede it before requests are sent.
    assert "install_max_quality_runtime" in Path("services/__init__.py").read_text(
        encoding="utf-8"
    )
    assert "async def _generate_light" in publication


def test_services_installs_max_quality_before_and_after_imports():
    src = Path("services/__init__.py").read_text(encoding="utf-8")
    assert "configure_max_quality_env" in src
    assert "install_max_quality_runtime" in src
    assert src.index("configure_max_quality_env()") < src.index("configure_gemini_policy()")
    assert src.index("install_max_quality_runtime()") < src.index("install_livedub_quality_runtime()")


def test_env_migration_is_36_high_large_v3_without_model_downgrade():
    src = Path("scripts/migrate-gemini-36.ps1").read_text(encoding="utf-8")
    assert 'GEMINI_MODEL" -Value "gemini-3.6-flash"' in src
    assert 'GEMINI_LIGHT_MODEL" -Value "gemini-3.6-flash"' in src
    assert 'SHORTS_FACTORY_MODEL" -Value "gemini-3.6-flash"' in src
    assert 'GEMINI_FORCE_THINKING_LEVEL" -Value "high"' in src
    assert 'LIVEDUB_QUICK_QA_THINKING" -Value "high"' in src
    assert 'LIVEDUB_LONG_QA_THINKING" -Value "high"' in src
    assert 'LIVEDUB_INFO_THINKING" -Value "high"' in src
    assert 'WHISPER_MODEL" -Value "large-v3"' in src
    assert 'WHISPER_ENG_SUBTITLES_MODEL" -Value "large-v3"' in src
    assert 'GEMINI_LIGHT_FALLBACK_MODELS" -Value ""' in src
    assert 'LIVEDUB_INFO_FALLBACK_MODELS" -Value ""' in src
    assert 'THINKING" -Value "minimal"' not in src
    assert "gemini-3.5" not in src
    assert "gemini-3.1" not in src
    assert "gemini-2.5" not in src
