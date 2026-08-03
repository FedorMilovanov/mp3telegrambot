"""Regression tests for R47 — live-screenshot findings on Shorts subtitles/video.

  1. Karaoke word list can contain a bare-punctuation token (e.g. "?»") as its
     own array element (Whisper sometimes emits it separately from the word
     it belongs to). Neither chunking nor line-wrap guarded against starting
     a new line with one, so a live Short showed:
       "«Тебе нужен Спаситель" / "?» Тебе нужен"
     — the closing quote+question-mark orphaned onto its own line. Fixed with
     a merge step (mirrors the existing hyphenated-particle merge).

  2. Static promo-slide Shorts (audio-only sermon cover) showed only the left
     portion of the slide with a skewed blur background. Root cause: cropdetect
     (_detect_black_bars) runs against the source regardless of whether it's a
     real video frame or a static design graphic, and its result still fed the
     "show the whole picture" full_frame_vertical branch. A slide's large dark/
     colored design blocks are exactly what cropdetect(limit=32) — tuned to
     catch dark/grey bars, not just pure black — can misread as letterboxing,
     cropping real content asymmetrically. Fixed by ignoring the cropdetect
     result whenever the static-slide auto-switch fires.
"""

from pathlib import Path

from services.shorts_video import _merge_orphan_punctuation, _wrap_chunk_to_lines


# ── 1. Orphan punctuation merge ──────────────────────────────────────────────
def test_r47_merges_trailing_punctuation_only_token():
    words = [
        {"word": "«Тебе", "start": 0.0, "end": 0.3},
        {"word": "нужен", "start": 0.3, "end": 0.6},
        {"word": "Спаситель", "start": 0.6, "end": 1.1},
        {"word": "?»", "start": 1.1, "end": 1.2},
        {"word": "Тебе", "start": 1.3, "end": 1.5},
        {"word": "нужен", "start": 1.5, "end": 1.8},
    ]
    merged = _merge_orphan_punctuation(words)
    assert [w["word"] for w in merged] == [
        "«Тебе", "нужен", "Спаситель?»", "Тебе", "нужен",
    ]
    # timing of the merged word extends to the punctuation token's end
    assert merged[2]["end"] == 1.2


def test_r47_line_wrap_no_longer_orphans_punctuation():
    words = [
        {"word": "«Тебе", "start": 0.0, "end": 0.3},
        {"word": "нужен", "start": 0.3, "end": 0.6},
        {"word": "Спаситель", "start": 0.6, "end": 1.1},
        {"word": "?»", "start": 1.1, "end": 1.2},
        {"word": "Тебе", "start": 1.3, "end": 1.5},
        {"word": "нужен", "start": 1.5, "end": 1.8},
    ]
    merged = _merge_orphan_punctuation(words)
    text = _wrap_chunk_to_lines(merged, max_chars=20)
    lines = text.split("\\N")
    # no line may start with a bare punctuation fragment
    for line in lines:
        first_word = line.split()[0] if line.split() else ""
        assert not (first_word and first_word[0] in "?!.,»\""), (
            f"line starts with orphaned punctuation: {line!r}"
        )


def test_r47_ordinary_words_unaffected_by_merge():
    words = [{"word": "Привет", "start": 0, "end": 0.3}, {"word": "мир", "start": 0.3, "end": 0.6}]
    assert [w["word"] for w in _merge_orphan_punctuation(words)] == ["Привет", "мир"]


def test_r47_multiple_trailing_punctuation_tokens_all_merge():
    words = [
        {"word": "слово", "start": 0, "end": 0.3},
        {"word": ".", "start": 0.3, "end": 0.35},
        {"word": "..", "start": 0.35, "end": 0.4},
    ]
    out = _merge_orphan_punctuation(words)
    assert [w["word"] for w in out] == ["слово..."]


def test_r47_leading_punctuation_with_no_prior_word_does_not_crash():
    # edge case: punctuation-only token as the very first element (nothing to
    # merge into) — must not raise, and stays as its own token.
    words = [{"word": "—", "start": 0, "end": 0.1}, {"word": "начало", "start": 0.1, "end": 0.4}]
    out = _merge_orphan_punctuation(words)
    assert [w["word"] for w in out] == ["—", "начало"]


def test_r47_merge_wired_into_both_karaoke_and_plain_pipelines():
    src = Path("services/shorts_video_impl.py").read_text(encoding="utf-8")
    assert src.count("_merge_orphan_punctuation(all_words)") == 2
    # must run after the existing hyphenated-particle merge in both call sites
    for anchor in src.split("_merge_orphan_punctuation(all_words)")[:-1]:
        assert "_merge_hyphenated_particles(all_words)" in anchor[-200:]


# ── 2. Static-slide cropdetect override ──────────────────────────────────────
def test_r47_static_slide_ignores_black_bars_cropdetect():
    """R47: cropdetect не годится для дизайн-графики — статичная заставка
    показывается ЦЕЛИКОМ, без обрезки по «чёрным полосам»."""
    src = Path("services/shorts_video_impl.py").read_text(encoding="utf-8")
    idx = src.find('visual_mode = "full_frame_vertical"')
    assert idx != -1
    window = src[idx: idx + 1200]
    assert 'black_bars = ""' in window


def test_r47_black_bars_still_detected_for_real_video_crop_zoom():
    # the crop_zoom (real video) path must still use black_bars detection —
    # only the static-slide auto-switch clears it.
    src = Path("services/shorts_video_impl.py").read_text(encoding="utf-8")
    assert "black_bars = await _detect_black_bars(" in src
    assert 'bc = f"{black_bars}," if black_bars else ""' in src
