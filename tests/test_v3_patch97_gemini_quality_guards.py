from pathlib import Path


def test_audio_analysis_is_primary_model_only_even_with_legacy_lite_env(monkeypatch):
    from services import gemini_analyze

    monkeypatch.delenv("AUDIO_ANALYSIS_FALLBACK_MODE", raising=False)
    assert gemini_analyze._audio_fallback_models("gemini-3.7-flash") == [
        "gemini-3.7-flash"
    ]

    monkeypatch.setenv("AUDIO_ANALYSIS_FALLBACK_MODE", "lite")
    assert gemini_analyze._audio_fallback_models("gemini-3.7-flash") == [
        "gemini-3.7-flash"
    ]


def test_audio_quota_is_not_promoted_to_model_global_exhaustion():
    """Gemini API limits are project-scoped, not model-global across keys."""
    audio_src = Path("services/gemini_analyze.py").read_text(encoding="utf-8")
    pages_src = Path("services/telegraph_pages.py").read_text(encoding="utf-8")

    assert "mark_model_exhausted" not in audio_src
    assert "limits are project-scoped" in audio_src
    # Telegraph is the explicit remaining legacy owner tracked by issue #159;
    # keep this assertion until that follow-up source refactor lands.
    assert "mark_model_exhausted(model_name, _client_err)" in pages_src


def test_pipeline_skips_ai_pages_when_audio_analysis_missing():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "AI analysis is missing/invalid" in src
    assert "_feat_study_analysis = False" in src
    assert "_feat_reflection_application = False" in src


def test_short_rutube_matches_require_stricter_score():
    src = Path("services/search.py").read_text(encoding="utf-8")
    assert "duration and duration < 300" in src
    assert "0.75" in src
