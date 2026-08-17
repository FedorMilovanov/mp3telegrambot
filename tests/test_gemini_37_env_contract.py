from pathlib import Path


def test_env_example_matches_gemini_37_quality_contract() -> None:
    env = Path(".env.example").read_text(encoding="utf-8")

    assert "GEMINI_MODEL=gemini-3.7-flash" in env
    assert "# GEMINI_MAX_MODEL=gemini-3.7-flash" in env
    assert "# LIVEDUB_INFO_MODEL=gemini-3.7-flash" in env
    assert "# LIVEDUB_QUICK_QA_MODEL=gemini-3.7-flash" in env
    assert "# LIVEDUB_LONG_QA_MODEL=gemini-3.7-flash" in env
    assert "# LIVEDUB_QA_VERIFY_MODEL=gemini-3.7-flash" in env

    assert "# GEMINI_LIGHT_MODEL=gemini-3.5-flash-lite" in env
    assert "# GEMINI_LIGHT_FALLBACK_MODELS=\n" in env
    assert "GEMINI_LIGHT_FALLBACK_MODELS=gemini-3.5-flash" not in env
    assert "# GEMINI_LIGHT_ALLOW_MAIN_FALLBACK=0" in env

    assert "# LIVEDUB_INFO_FALLBACK_MODELS=\n" in env
    assert "LIVEDUB_INFO_FALLBACK_MODELS=gemini-3.5" not in env
    assert "# LOCAL_BOT_API_REQUIRED_TIMEOUT_SEC=300" in env
    assert "LOCAL_BOT_API_GETME_TIMEOUT_SEC" not in env
