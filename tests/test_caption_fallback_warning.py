from __future__ import annotations

from converters.caption import build_caption


def test_fallback_warning_does_not_claim_entire_analysis_used_reserve_model() -> None:
    caption = build_caption(
        "Автор",
        "Название",
        120,
        5.0,
        ai_data={
            "real_author": "Автор",
            "real_title": "Название",
            "_gemini_was_fallback": True,
        },
    )

    assert "Один из дополнительных разделов создан на резервной модели" in caption
    assert "Разбор создан на резервной модели" not in caption
    assert "основной разбор и таймкоды могли быть созданы основной моделью" in caption
