"""Regression contracts for the project-wide quality/cost policy."""
from pathlib import Path

from services import gemini_max_quality as quality


def test_heavy_quality_policy_forces_36_high_and_large_v3(monkeypatch):
    for name in (
        "GEMINI_MODEL", "GEMINI_MAX_MODEL", "LIVEDUB_INFO_MODEL",
        "LIVEDUB_QUICK_QA_MODEL", "LIVEDUB_LONG_QA_MODEL", "LIVEDUB_QA_VERIFY_MODEL",
        "SHORTS_FACTORY_MODEL", "GEMINI_FORCE_THINKING_LEVEL",
        "LIVEDUB_QUICK_QA_THINKING", "LIVEDUB_LONG_QA_THINKING",
        "LIVEDUB_QA_VERIFY_THINKING", "LIVEDUB_INFO_THINKING",
        "WHISPER_MODEL", "WHISPER_ENG_SUBTITLES_MODEL", "SHORTS_FACTORY_WHISPER_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    diagnostic = quality.configure_max_quality_env()
    import os
    for name in (
        "GEMINI_MODEL", "GEMINI_MAX_MODEL", "LIVEDUB_INFO_MODEL",
        "LIVEDUB_QUICK_QA_MODEL", "LIVEDUB_LONG_QA_MODEL", "LIVEDUB_QA_VERIFY_MODEL",
        "SHORTS_FACTORY_MODEL",
    ):
        assert os.environ[name] == "gemini-3.6-flash"
    for name in (
        "GEMINI_FORCE_THINKING_LEVEL", "LIVEDUB_QUICK_QA_THINKING",
        "LIVEDUB_LONG_QA_THINKING", "LIVEDUB_QA_VERIFY_THINKING", "LIVEDUB_INFO_THINKING",
    ):
        assert os.environ[name] == "high"
    for name in ("WHISPER_MODEL", "WHISPER_ENG_SUBTITLES_MODEL", "SHORTS_FACTORY_WHISPER_MODEL"):
        assert os.environ[name] == "large-v3"
    assert "semantic=gemini-3.6-flash/high" in diagnostic


def test_model_aware_thinking_is_owned_by_core_config_helper():
    from core.globals import _effective_thinking_level
    assert _effective_thinking_level("gemini-3.6-flash", "minimal") == "high"
    assert _effective_thinking_level("gemini-3.5-flash-lite", "high") == "minimal"
    assert _effective_thinking_level("gemini-3.5-flash", "high") == "minimal"
    assert _effective_thinking_level("gemini-custom-audio-model", "medium") == "medium"


def test_utility_work_uses_35_quota_without_semantic_fallback():
    src = Path("services/gemini_max_quality.py").read_text(encoding="utf-8")
    assert '_LIGHT_MODEL = "gemini-3.5-flash-lite"' in src
    assert '_LIGHT_FALLBACK_MODEL = "gemini-3.5-flash"' in src
    assert 'os.environ["GEMINI_LIGHT_ALLOW_MAIN_FALLBACK"] = "0"' in src
    assert 'os.environ["LIVEDUB_PUBLICATION_FALLBACK_MODELS"] = ""' in src
    assert 'os.environ["LIVEDUB_PUBLICATION_ALLOW_STRONG_FALLBACK"] = "0"' in src
    assert "gemini-3.1" not in src and "gemini-2.5" not in src


def test_publication_metadata_directly_owns_36_high_quality_route():
    publication = Path("services/livedub_publication_core.py").read_text(encoding="utf-8")
    resilience = Path("services/gemini36_factory_resilience.py").read_text(encoding="utf-8")
    assert '_PUBLICATION_MODEL = "gemini-3.6-flash"' in publication
    assert 'thinking_level="high"' in publication
    assert "GEMINI_LIGHT_MODEL" not in publication
    assert "temperature=" not in publication
    assert "_verify_publication_quality_route" in resilience
    assert "publication.publication_models =" not in resilience


def test_pre_main_manifest_owns_quality_before_core_clients():
    package = Path("services/__init__.py").read_text(encoding="utf-8")
    manifest = Path("services/runtime_manifest.py").read_text(encoding="utf-8")
    owner = Path("services/pre_main_policy.py").read_text(encoding="utf-8")
    assert "configure_max_quality_env()" not in package
    assert '"pre-main-quality-policy"' in manifest
    assert '"services.pre_main_policy"' in manifest
    assert owner.index("configure_gemini_qa_policy()") < owner.index("configure_max_quality_env()")
    assert owner.index("configure_max_quality_env()") < owner.index("configure_gemini_policy()")
    assert "from core.globals" not in owner
    assert "import core.globals" not in owner


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
    assert "gemini-3.1" not in src and "gemini-2.5" not in src
