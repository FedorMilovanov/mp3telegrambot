"""Regression tests for R40 audit-closure round.

Covers the deterministic, pure-function fixes shipped in R40:
  * text_utils — typo-list scoping (Strange Fire kept, bare "Слово Божьего"
    dropped), month-date no longer nukes fields, mixed-script ReDoS pre-gate,
    hashtag emoji-only rejection.
  * source_packs — word-boundary keyword matching (ангел ≠ евангелие) and the
    removal of the generic «дети» trigger for the infant-death pack.
  * globals — credential masking by pattern (proxy user:pass@, bot token,
    AIza key) including the previously-unmasked non-str ``record.msg`` path.
  * playlist — ``_build_media_url`` guards a non-dict/None playlist entry.
"""

import logging

import pytest

from core.text_utils import (
    normalize_common_typos,
    is_meta_garbage,
    find_mixed_greek_cyrillic_tokens,
    normalize_hashtag,
)
from core.source_packs import select_source_pack_topics
from core.globals import mask_credentials, _TokenMaskFilter
from pipelines.playlist import _build_media_url


# ── text_utils: T2 typo-list scoping ─────────────────────────────────────────
def test_r40_strange_fire_correction_retained():
    # Intentional, tested, theologically-correct title/term correction.
    assert normalize_common_typos("Странный огонь") == "Чуждый огонь"
    assert normalize_common_typos("странный огонь") == "чуждый огонь"


def test_r40_slovo_bozhego_anchored_kept_bare_dropped():
    # Anchored form is unambiguous (genitive required after "авторитет").
    assert normalize_common_typos("авторитет Слово Божьего") == "авторитет Слова Божьего"
    # Bare form was context-blind and corrupted a legitimate nominative subject.
    assert (
        normalize_common_typos("Слово Божьего пророка звучало над народом")
        == "Слово Божьего пророка звучало над народом"
    )


# ── text_utils: T5 month-date must not delete a whole field ───────────────────
def test_r40_month_date_is_not_meta_garbage():
    # A legitimate event date inside a real field must survive (whole-line drop).
    assert not is_meta_garbage("Конференция May 5, 2024 в Москве была важной")
    # Publication timestamps ("… at H:MM") are still stripped.
    assert is_meta_garbage("March 15 at 18:55")


# ── text_utils: T1 mixed Greek/Cyrillic pre-gate ──────────────────────────────
def test_r40_mixed_script_detector_pregate():
    assert find_mixed_greek_cyrillic_tokens("μεлеτάω и ὑπόκрисис") == ["μεлеτάω", "ὑπόκрисис"]
    # Single-script inputs exit before the per-char scan and find nothing.
    assert find_mixed_greek_cyrillic_tokens("обычный русский текст без греческого") == []
    assert find_mixed_greek_cyrillic_tokens("only greek μελετάω lemma here") == []
    # A long snake_case run (single word-token, no Greek) must not hang / match.
    assert find_mixed_greek_cyrillic_tokens("a_" * 5000) == []


# ── text_utils: T6 hashtag emoji-only rejection ───────────────────────────────
def test_r40_hashtag_emoji_or_punct_only_rejected():
    assert normalize_hashtag("  #  🎯 ") == ""
    assert normalize_hashtag("🔥") == ""
    assert normalize_hashtag("###") == ""
    # Normal tags still work.
    assert normalize_hashtag("личная_встреча") == "#ЛичнаяВстреча"
    assert normalize_hashtag("НовоеТворение") == "#НовоеТворение"


# ── source_packs: word-boundary keyword matching ──────────────────────────────
def test_r40_evangelism_does_not_trigger_angelology():
    topics = dict(select_source_pack_topics([], "евангелие евангелизм благовестие"))
    # "ангел" no longer substring-matches "евАНГЕЛие"/"евангелизм".
    assert "angelology" not in topics
    assert "evangelism_gospel" in topics


def test_r40_real_angel_topic_still_matches():
    topics = dict(select_source_pack_topics([], "ангелы и небесное воинство"))
    assert "angelology" in topics


def test_r40_generic_children_no_infant_death_pack():
    # The pack is about the eternal fate of *deceased infants*; a sermon merely
    # mentioning children (Луки 18) must not pull it in.
    topics = dict(select_source_pack_topics([], "Иисус и дети в Царстве Небесном"))
    assert "infant_salvation_children" not in topics


def test_r40_specific_infant_keyword_retained():
    topics = dict(select_source_pack_topics([], "спасение младенец"))
    assert "infant_salvation_children" in topics


# ── globals: credential masking ───────────────────────────────────────────────
def test_r40_mask_credentials_patterns():
    assert mask_credentials("http://user:pass@10.0.0.1:8080") == "http://***:***@10.0.0.1:8080"
    assert "user" not in mask_credentials("proxy socks5://alice:s3cr3t@host:1080")
    assert mask_credentials("bot123456:AAH" + "x" * 33 + " sent") == "bot*** sent"
    assert mask_credentials("key AIza" + "B" * 35) == "key ***"
    # Port-only URLs (no userinfo) are untouched — no false masking.
    assert mask_credentials("http://host:8080/path") == "http://host:8080/path"


def test_r40_log_filter_masks_nonstr_msg():
    # logger.error(exc) puts a non-str object in record.msg with no args — this
    # previously bypassed the mask entirely.
    f = _TokenMaskFilter()
    exc = RuntimeError("proxy http://u:p@10.0.0.1:8080 died")
    rec = logging.LogRecord("t", logging.ERROR, __file__, 1, exc, None, None)
    f.filter(rec)
    msg = rec.getMessage()
    assert "u:p@" not in msg
    assert "***:***@" in msg


# ── playlist: guard a non-dict entry ──────────────────────────────────────────
@pytest.mark.parametrize("bad", [None, "not-a-dict", 123, ["list"]])
def test_r40_build_media_url_guards_non_dict(bad):
    assert _build_media_url(bad) is None


def test_r40_build_media_url_normal_entries():
    assert _build_media_url({"id": "abc123"}) == "https://www.youtube.com/watch?v=abc123"
    assert _build_media_url({"url": "https://rutube.ru/video/x/"}) == "https://rutube.ru/video/x/"
    assert _build_media_url({}) is None  # no id, no url
