#!/usr/bin/env python3
"""AUDIT R35 (живой прогон 2026-07-10, оператор: «Где-то ## 2 хэштега»).

Клип показывал «##СилаБожья ##СокрушениеСердца …» — двойная решётка.
Причина: clips хранят теги через normalize_hashtag(), который ВОЗВРАЩАЕТ уже
с «#» («СилаБожья» → «#СилаБожья»), а подпись клипа затем добавляла ещё
одну решётку f"#{t}". Shorts не страдали, т.к. делают lstrip('#') заранее.

Фикс: общий _hashtags_line() строит теги идемпотентно (lstrip('#') + один
префикс) для clip/montage/highlights.
"""
from core.text_utils import normalize_hashtag
from services.render_clips_montage import (
    _hashtags_line,
    build_clip_caption,
    build_highlights_caption,
    build_montage_caption,
)


def test_normalize_hashtag_returns_with_hash():
    # Фиксируем предпосылку бага: normalize_hashtag ВОЗВРАЩАЕТ с «#».
    assert normalize_hashtag("СилаБожья") == "#СилаБожья"


def test_hashtags_line_idempotent():
    assert _hashtags_line(["#A", "B", " ##C ", "#"]) == "#A #B #C"
    assert _hashtags_line([]) == ""
    # ограничение в 4 тега сохраняется
    assert _hashtags_line(["#a", "#b", "#c", "#d", "#e"]) == "#a #b #c #d"


def test_clip_caption_no_double_hash():
    # Так теги реально лежат в clip-кандидате — уже с «#».
    tags = [normalize_hashtag(t) for t in ["СилаБожья", "СокрушениеСердца", "ПреображениеЖизни", "ЖивойБог"]]
    assert tags == ["#СилаБожья", "#СокрушениеСердца", "#ПреображениеЖизни", "#ЖивойБог"]
    cap = build_clip_caption(
        {"title": "Сокрушительное присутствие Бога", "hashtags": tags,
         "start_seconds": 2172, "end_seconds": 2490, "kind": "sermon_highlight"},
        performer="", real_author="Пол Вошер", real_event="", format_name="sermon",
        yt_url="https://youtu.be/x",
    )
    assert "##" not in cap
    assert "#СилаБожья" in cap


def test_montage_and_highlights_no_double_hash():
    tags = ["#Вера", "#Покаяние"]
    m = build_montage_caption(
        theme="Покаяние", title="Тема", performer="", real_author="Пол Вошер",
        format_name="sermon", fragment_count=3, hashtags=tags, yt_url="https://youtu.be/x",
    )
    h = build_highlights_caption(
        title="Лучшее", performer="", real_author="Пол Вошер", format_name="sermon",
        fragment_count=5, hashtags=tags, yt_url="https://youtu.be/x",
    )
    assert "##" not in m and "#Вера" in m
    assert "##" not in h and "#Покаяние" in h
