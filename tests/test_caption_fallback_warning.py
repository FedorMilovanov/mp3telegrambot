from __future__ import annotations

from converters.caption import build_caption


def test_legacy_fallback_marker_does_not_publish_reserve_model_warning() -> None:
    """Semantic model downgrade is forbidden, so stale ambient flags are inert."""
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

    assert "резервной модели" not in caption
    assert "Основной разбор" not in caption
    assert "Название" in caption
    assert "Автор" in caption
