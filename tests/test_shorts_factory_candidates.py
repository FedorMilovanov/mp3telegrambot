from services.shorts_factory_candidates import validate_factory_plan


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
            },
            {
                "start_seconds": 120,
                "end_seconds": 500,
                "title_ru": "Слишком Длинный Shorts",
                "quality_score": 99,
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
            }
        ],
        "long_candidates": [],
    }

    plan = validate_factory_plan(raw, duration=1000)

    assert plan["shorts_candidates"] == []
