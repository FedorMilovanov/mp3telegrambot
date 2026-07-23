"""Regression contracts for the project-wide maximum-thinking policy."""
from pathlib import Path


def test_max_quality_runtime_forces_high_on_shared_quality_helpers():
    src = Path("services/gemini_max_quality.py").read_text(encoding="utf-8")
    assert 'os.environ["GEMINI_FORCE_THINKING_LEVEL"] = "high"' in src
    assert 'os.environ["LIVEDUB_QUICK_QA_THINKING"] = "high"' in src
    assert 'os.environ["LIVEDUB_LONG_QA_THINKING"] = "high"' in src
    assert 'positional[3] = "high"' in src
    assert 'options["thinking_level"] = "high"' in src
    assert "make_text_config_smart = max_text_smart" in src
    assert "make_audio_config = max_audio" in src
    assert "make_text_config = max_text_legacy" in src


def test_publication_metadata_has_explicit_minimal_lite_exception():
    src = Path("services/livedub_publication_core.py").read_text(encoding="utf-8")
    assert '_build_thinking_config("minimal")' in src
    assert '"lite" not in model.casefold()' in src
    assert 'LIVEDUB_PUBLICATION_ALLOW_STRONG_FALLBACK' in src
    assert "make_text_config_smart" not in src


def test_services_installs_max_quality_before_and_after_imports():
    src = Path("services/__init__.py").read_text(encoding="utf-8")
    assert "configure_max_quality_env" in src
    assert "install_max_quality_runtime" in src
    assert src.index("configure_max_quality_env()") < src.index("configure_gemini_policy()")
    assert src.index("install_max_quality_runtime()") < src.index("install_livedub_quality_runtime()")


def test_env_migration_never_requests_minimal_thinking_for_quality_tasks():
    src = Path("scripts/migrate-gemini-36.ps1").read_text(encoding="utf-8")
    assert 'GEMINI_FORCE_THINKING_LEVEL" -Value "high"' in src
    assert 'LIVEDUB_QUICK_QA_THINKING" -Value "high"' in src
    assert 'LIVEDUB_LONG_QA_THINKING" -Value "high"' in src
    assert 'LIVEDUB_INFO_THINKING" -Value "high"' in src
    assert 'THINKING" -Value "minimal"' not in src
