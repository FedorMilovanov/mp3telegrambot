import pytest

from services.shorts_factory_candidates import (
    DEFAULT_SHORTS_FACTORY_MODEL,
    FACTORY_PLAN_RESPONSE_SCHEMA,
    shorts_factory_model,
    validate_factory_plan,
)


def test_factory_plan_enforces_duration_ranges_and_overlap():
    raw = {
        "metadata": {
            "language": "en",
            "format": "sermon",
            "title_ru": "Испытание Веры",
            "author_ru": "Автор",
        },
        "shorts_candidates": [
            {
                "start_seconds": 10,
                "end_seconds": 100,
                "title_ru": "Сильный Первый Фрагмент",
                "quality_score": 95,
                "boundary_verified": True,
            },
            {
                "start_seconds": 20,
                "end_seconds": 105,
                "title_ru": "Дублирующий Фрагмент",
                "quality_score": 70,
                "boundary_verified": True,
            },
            {
                "start_seconds": 120,
                "end_seconds": 500,
                "title_ru": "Слишком Длинный Shorts",
                "quality_score": 99,
                "boundary_verified": True,
            },
        ],
        "long_candidates": [
            {
                "start_seconds": 600,
                "end_seconds": 1200,
                "title_ru": "Законченный Длинный Фрагмент",
                "quality_score": 90,
                "boundary_verified": True,
            },
            {
                "start_seconds": 610,
                "end_seconds": 1180,
                "title_ru": "Повтор Того Же Фрагмента",
                "quality_score": 60,
                "boundary_verified": True,
            },
        ],
    }

    plan = validate_factory_plan(raw, duration=1800)

    assert [item["title"] for item in plan["shorts_candidates"]] == [
        "Сильный Первый Фрагмент"
    ]
    assert [item["title"] for item in plan["long_candidates"]] == [
        "Законченный Длинный Фрагмент"
    ]
    assert plan["shorts_candidates"][0]["duration_seconds"] == 90
    assert plan["long_candidates"][0]["duration_seconds"] == 600


def test_factory_plan_rejects_out_of_source_bounds():
    raw = {
        "shorts_candidates": [
            {
                "start_seconds": 950,
                "end_seconds": 1050,
                "title_ru": "За Пределами Источника",
                "quality_score": 100,
                "boundary_verified": True,
            }
        ],
        "long_candidates": [],
    }

    plan = validate_factory_plan(raw, duration=1000)

    assert plan["shorts_candidates"] == []


def test_factory_plan_rejects_unverified_boundaries_by_default():
    raw = {
        "shorts_candidates": [
            {
                "start_seconds": 10,
                "end_seconds": 90,
                "title_ru": "Непроверенный Фрагмент",
                "quality_score": 100,
                "boundary_verified": False,
            }
        ],
        "long_candidates": [],
    }

    plan = validate_factory_plan(raw, duration=300)

    assert plan["shorts_candidates"] == []


def test_factory_model_defaults_to_pro_without_flash_fallback(monkeypatch):
    for name in ("SHORTS_FACTORY_MODEL", "GEMINI_PRO_MODEL", "GEMINI_MAX_MODEL"):
        monkeypatch.delenv(name, raising=False)

    assert DEFAULT_SHORTS_FACTORY_MODEL == "gemini-3.1-pro-preview"
    assert shorts_factory_model() == "gemini-3.1-pro-preview"


def test_generic_flash_max_does_not_downgrade_factory(monkeypatch):
    monkeypatch.delenv("SHORTS_FACTORY_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_PRO_MODEL", raising=False)
    monkeypatch.setenv("GEMINI_MAX_MODEL", "gemini-3.6-flash")

    assert shorts_factory_model() == DEFAULT_SHORTS_FACTORY_MODEL


def test_factory_model_refuses_explicit_flash_route(monkeypatch):
    monkeypatch.setenv("SHORTS_FACTORY_MODEL", "gemini-3.6-flash")

    with pytest.raises(RuntimeError, match="requires a Pro model"):
        shorts_factory_model()


def test_factory_model_refuses_lite_route(monkeypatch):
    monkeypatch.setenv("SHORTS_FACTORY_MODEL", "gemini-3.5-flash-lite")

    with pytest.raises(RuntimeError, match="requires a Pro model"):
        shorts_factory_model()


def test_factory_response_schema_requires_complete_plan_shape():
    assert FACTORY_PLAN_RESPONSE_SCHEMA["required"] == [
        "metadata",
        "shorts_candidates",
        "long_candidates",
    ]
    candidate = FACTORY_PLAN_RESPONSE_SCHEMA["properties"]["shorts_candidates"]["items"]
    assert "boundary_verified" in candidate["required"]
    assert "quality_score" in candidate["required"]
