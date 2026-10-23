from pathlib import Path

from tools.workflow_actions import minimum_major


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_title_second_chance_directly_owns_38_high():
    src = _read("services/livedub_info_presentation_policy.py")
    assert "DEFAULT_INFO_MODEL" in src
    assert 'thinking_level="high"' in src
    assert "temperature=" not in src
    assert 'thinking_level="minimal"' not in src
    assert "get_light_model" not in src


def test_env_example_keeps_user_copy_off_utility_lane():
    from core.model_catalog import MODEL_PRIMARY

    env = _read(".env.example")
    assert f"# LIVEDUB_INFO_MODEL={MODEL_PRIMARY}" in env
    assert "# LIVEDUB_INFO_FALLBACK_MODELS=" in env
    assert "# LIVEDUB_PUBLICATION_FALLBACK_MODELS=" in env
    assert "# LIVEDUB_PUBLICATION_ALLOW_STRONG_FALLBACK=0" in env
    assert "Для лёгкой publication-карточки" not in env
    assert "ENG Quick переводит title через GEMINI_LIGHT_MODEL" not in env


def test_ci_uses_node24_actions_and_runs_repository_verifier():
    ci = _read(".github/workflows/ci.yml")
    # Floor, not equality: Dependabot may move these to the next major, and the
    # requirement is "runs on a supported Node runtime", not "is exactly v6".
    for action, floor in (
        ("actions/checkout", 6),
        ("actions/setup-python", 6),
        ("actions/upload-artifact", 4),
    ):
        assert minimum_major(ci, action) >= floor, action
    assert "python tools/verify_repo.py" in ci


def test_factory_retry_messages_match_analysis_audio_contract():
    retry_cache = _read("services/shorts_factory_retry_cache.py")
    capacity = _read("services/shorts_factory_capacity_runtime.py")

    assert "использую уже проверенное analysis-аудио" in retry_cache
    assert "загружаю analysis-аудио" in capacity
    assert "загруженном analysis-аудио" in capacity
    assert "shorts_factory_overload_runtime" not in retry_cache
    assert "shorts_factory_overload_runtime" not in capacity


def test_user_visible_publication_sources_have_no_minimal_or_sampling_route():
    for path in (
        "services/livedub_info.py",
        "services/livedub_publication.py",
        "services/livedub_publication_core.py",
        "services/livedub_info_presentation_policy.py",
    ):
        src = _read(path)
        assert 'thinking_level="minimal"' not in src, path
        assert "temperature=" not in src, path
