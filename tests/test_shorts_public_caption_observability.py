import logging

from converters.md_telegraph import visible_length
from pipelines.shorts import _finalize_short_caption_for_delivery


def test_final_public_caption_is_logged_after_trim(caplog) -> None:
    raw = "Заголовок - Пол Вошер\n\n" + ("Очень длинный текст. " * 90)

    with caplog.at_level(logging.INFO, logger="pipelines.shorts"):
        final = _finalize_short_caption_for_delivery(
            raw,
            media_id="video-1",
            index=2,
            total=3,
            start="1:00",
            end="2:00",
        )

    assert visible_length(final) <= 1024
    assert final != raw
    assert "Shorts public caption: media_id=video-1 index=2/3" in caplog.text
    assert f"final_visible_len={visible_length(final)}" in caplog.text
    assert f"caption={final!r}" in caplog.text


def test_untrimmed_public_caption_is_logged_exactly(caplog) -> None:
    raw = "Сомнение — Это Прямое Оскорбление Характера Бога - Пол Вошер"

    with caplog.at_level(logging.INFO, logger="pipelines.shorts"):
        final = _finalize_short_caption_for_delivery(
            raw,
            media_id="video-2",
            index=1,
            total=1,
        )

    assert final == raw
    assert f"caption={raw!r}" in caplog.text
