from pathlib import Path

from core.model_catalog import MODEL_PRIMARY, MODEL_UTILITY


def test_env_example_matches_gemini_38_quality_contract() -> None:
    env = Path(".env.example").read_text(encoding="utf-8")

    # Values come from the catalog, so a model bump never edits this test.
    assert f"GEMINI_MODEL={MODEL_PRIMARY}" in env
    assert f"# SHORTS_FACTORY_MODEL={MODEL_PRIMARY}" in env
    assert f"# LIVEDUB_INFO_MODEL={MODEL_PRIMARY}" in env
    assert f"# LIVEDUB_QUICK_QA_MODEL={MODEL_PRIMARY}" in env
    assert f"# LIVEDUB_LONG_QA_MODEL={MODEL_PRIMARY}" in env
    assert f"# LIVEDUB_QA_VERIFY_MODEL={MODEL_PRIMARY}" in env

    # The GEMINI_LIGHT_* / GEMINI_MAX_MODEL / GEMINI_FORCE_THINKING_LEVEL
    # knobs were deleted: nothing read them, the policy overwrote them on every
    # start. The absence is the contract now — see test_env_policy_contract.py.
    for removed in ("GEMINI_LIGHT_MODEL=", "GEMINI_LIGHT_FALLBACK_MODELS=",
                    "GEMINI_LIGHT_ALLOW_MAIN_FALLBACK=", "GEMINI_MAX_MODEL=",
                    "GEMINI_FORCE_THINKING_LEVEL="):
        assert removed not in env, f"{removed} вернулся в .env.example"
    assert f"MP3BOT_UTILITY_MODEL={MODEL_UTILITY}" in env

    assert "# LIVEDUB_INFO_FALLBACK_MODELS=\n" in env
    assert "LIVEDUB_INFO_FALLBACK_MODELS=gemini-3.5" not in env
    assert "# LOCAL_BOT_API_REQUIRED_TIMEOUT_SEC=300" in env
    assert "LOCAL_BOT_API_GETME_TIMEOUT_SEC" not in env


def test_factory_source_matches_gemini_38_quality_contract() -> None:
    factory = Path("services/shorts_factory_candidates.py").read_text(encoding="utf-8")
    runtime = Path("services/shorts_factory_capacity_runtime.py").read_text(
        encoding="utf-8"
    )

    assert "DEFAULT_SHORTS_FACTORY_MODEL = MODEL_PRIMARY" in factory
    assert "SHORTS FACTORY MAX requires {DEFAULT_SHORTS_FACTORY_MODEL}" in factory
    assert "gemini-3.7-flash" not in factory
    assert "SHORTS FACTORY MAX requires {DEFAULT_SHORTS_FACTORY_MODEL}" in factory
    assert "Gemini 3.7" not in runtime
    assert "Gemini 3.8" in runtime


def test_livedub_env_tests_enforce_current_quality_policy() -> None:
    tests = Path("tests/livedub_qa_cases.py").read_text(encoding="utf-8")

    assert 'assert "LIVEDUB_QUICK_QA_THINKING=high" in env' in tests
    assert 'assert "LIVEDUB_QUICK_QA_THINKING=minimal" in env' not in tests
    # The live case now pins the catalog role instead of the removed knobs.
    live = Path("tests/test_livedub_qa.py").read_text(encoding="utf-8")
    assert "MP3BOT_UTILITY_MODEL" in live
