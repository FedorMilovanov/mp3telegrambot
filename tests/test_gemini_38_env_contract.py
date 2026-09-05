from pathlib import Path


def test_env_example_matches_gemini_38_quality_contract() -> None:
    env = Path(".env.example").read_text(encoding="utf-8")

    assert "GEMINI_MODEL=gemini-3.8-flash" in env
    assert "# GEMINI_MAX_MODEL=gemini-3.8-flash" in env
    assert "# LIVEDUB_INFO_MODEL=gemini-3.8-flash" in env
    assert "# LIVEDUB_QUICK_QA_MODEL=gemini-3.8-flash" in env
    assert "# LIVEDUB_LONG_QA_MODEL=gemini-3.8-flash" in env
    assert "# LIVEDUB_QA_VERIFY_MODEL=gemini-3.8-flash" in env

    assert "# GEMINI_LIGHT_MODEL=gemini-3.5-flash-lite" in env
    assert "# GEMINI_LIGHT_FALLBACK_MODELS=\n" in env
    assert "GEMINI_LIGHT_FALLBACK_MODELS=gemini-3.5-flash" not in env
    assert "# GEMINI_LIGHT_ALLOW_MAIN_FALLBACK=0" in env

    assert "# LIVEDUB_INFO_FALLBACK_MODELS=\n" in env
    assert "LIVEDUB_INFO_FALLBACK_MODELS=gemini-3.5" not in env
    assert "# LOCAL_BOT_API_REQUIRED_TIMEOUT_SEC=300" in env
    assert "LOCAL_BOT_API_GETME_TIMEOUT_SEC" not in env


def test_livedub_env_tests_enforce_current_quality_policy() -> None:
    tests = Path("tests/livedub_qa_cases.py").read_text(encoding="utf-8")

    assert 'assert "LIVEDUB_QUICK_QA_THINKING=high" in env' in tests
    assert 'assert "LIVEDUB_QUICK_QA_THINKING=minimal" in env' not in tests
    assert 'assert "GEMINI_LIGHT_FALLBACK_MODELS=" in env' in tests
    assert 'assert "GEMINI_LIGHT_FALLBACK_MODELS=gemini-3.5-flash" not in env' in tests
    assert 'assert "GEMINI_LIGHT_ALLOW_MAIN_FALLBACK=0" in env' in tests
    assert 'assert "GEMINI_LIGHT_ALLOW_MAIN_FALLBACK=1" in env' not in tests
