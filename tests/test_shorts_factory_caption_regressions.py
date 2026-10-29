from __future__ import annotations

import html

from core.text_utils import title_case_fragment
from services.render_clips_montage import build_clip_caption
from services.shorts_factory_candidates import _clean_tags
from services.shorts_factory_publication import canonical_public_hashtags


def test_factory_combined_hashtags_remain_separate_tags() -> None:
    cleaned = _clean_tags(["#РечьБога #Буря #ВеличиеТворца #Иов38 #НаукаИВера"])

    assert cleaned == ["РечьБога", "Буря", "ВеличиеТворца", "Иов38"]
    assert canonical_public_hashtags(cleaned) == [
        "#РечьБога",
        "#Буря",
        "#ВеличиеТворца",
        "#Иов38",
    ]


def test_factory_hashtag_phrases_are_normalized_without_gluing_items() -> None:
    assert _clean_tags(["речь бога", "величие творца"]) == ["РечьБога", "ВеличиеТворца"]


def test_factory_overlong_hashtag_is_dropped_instead_of_truncated() -> None:
    malformed = "речьбогабурявеличиетворцаион38наукаиерабог"

    assert len(malformed) > 40
    assert _clean_tags([malformed, "Иов38"]) == ["Иов38"]


def test_clip_title_is_bold_and_dynamic_html_is_escaped() -> None:
    raw_title = "Бог & <разум>"
    caption = build_clip_caption(
        {
            "title": raw_title,
            "hashtags": [],
            "start_seconds": 2198,
            "end_seconds": 3071,
        },
        performer="",
        real_author="Джон & Джейн",
        real_event="",
        format_name="sermon",
    )

    expected_title = html.escape(title_case_fragment(raw_title), quote=False)
    assert caption.split("\n\n", 1)[0] == (f"<b>{expected_title}</b> — Джон &amp; Джейн ✂️ [36:38 — 51:11]")
