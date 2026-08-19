#!/usr/bin/env python3
"""AUDIT R39 — исправления из второго глубокого ревью (Telegraph/markdown,
handlers+оркестратор, Gemini/transcript/search, persistence/PDF/LiveDub).
"""
import json
from pathlib import Path


# ── Gemini-F1: метафоричное название больше НЕ обнуляется ───────────────────
def test_r39_metaphorical_title_not_zeroed():
    from core.json_parser import _parse_gemini_response
    resp = json.dumps({
        "real_title": "Трус и лжец", "format": "sermon",
        "main_topic": "Повествование о духовном преображении и покаянии грешника",
        "timestamps": [{"time": "0:00", "topic": "Вступление и молитва общины"}],
    })
    p = _parse_gemini_response(resp, 600)
    assert p.get("real_title") == "Трус и лжец"
    assert p.get("title_topic_warning")
    assert not p.get("real_title_ai_rejected")


# ── Gemini-F3: cue-подстрока внутри слова не теряется ──────────────────────
def test_r39_vtt_substring_word_not_dropped():
    import services.youtube_transcript as yt
    chunk = ["kingdom", "of", "heaven"]
    yt._append_caption_delta(chunk, "king")
    assert "king" in chunk
    chunk2 = ["kingdom", "of", "heaven"]
    yt._append_caption_delta(chunk2, "kingdom of heaven")
    assert chunk2 == ["kingdom", "of", "heaven"]


# ── Gemini-F4: обе длительности unknown = нейтрально, не полный буст ────────
def test_r39_dur_score_both_unknown_neutral():
    from services.search import _score_candidate_match
    mw = {"свидетельство", "трус", "лжец"}
    perfect = _score_candidate_match(mw, mw, set(), duration=1800, item_duration=1800)[0]
    unknown = _score_candidate_match(mw, mw, set(), duration=0, item_duration=0)[0]
    assert unknown < perfect


# ── Telegraph-F2: safe_trim_caption уважает лимит UTF-16 ────────────────────
def test_r39_caption_trim_utf16_limit():
    from converters.md_telegraph import safe_trim_caption, visible_length
    for cap in ["🙏" * 1000, "<b>📜 " + "слово " * 90 + "🙏✝️</b>"]:
        assert visible_length(safe_trim_caption(cap, 1024)) <= 1024


# ── Persist-F2: QA/info не падают на списке строк вместо dict ───────────────
def test_r39_qa_info_survive_string_shapes():
    from services.livedub_qa import format_qa_report
    from services.livedub_info import format_livedub_info_message
    assert format_qa_report({"score": 90, "issues": ["10:05 wrong", "bad"]})
    msg = format_livedub_info_message({
        "scripture_references": ["John 3:16", {"ref": "Ин 1:1", "text_ru": "В начале"}],
        "telegram_description": "t", "hashtags": [], "key_theological_terms": [],
    })
    assert isinstance(msg, str)


# ── Persist-F1: элементы JSON-списков в архиве приводятся к str ───────────────
def test_r39_archive_rows_coerce_list_elements():
    from core.generated_pages import _RECORD_KEYS, _rows_to_dicts
    row = tuple("[1,2]" if k == "hashtags" else "" for k in _RECORD_KEYS)
    out = _rows_to_dicts([row])[0]
    assert out["hashtags"] == ["1", "2"]


# ── Структурные проверки (без ffmpeg/сети) ─────────────────────────────────
def test_r39_telegraph_destructive_move_removed():
    for f in ("services/telegraph.py", "services/telegraph_pages.py"):
        src = Path(f).read_text(encoding="utf-8")
        assert "переношу последнюю секцию" not in src


def test_r39_mp3_reencode_guarded_against_self():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert src.count("mp3_path.name != mp3_64_path.name") >= 2


def test_r39_process_global_fallback_marker_is_removed():
    pipeline = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    telegraph = Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    caption = Path("converters/caption.py").read_text(encoding="utf-8")
    assert "import services.telegraph_pages as _tp_module" not in pipeline
    assert "_gemini_last_was_fallback" not in pipeline
    assert "_gemini_last_was_fallback" not in telegraph
    assert "_gemini_was_fallback" not in caption


def test_r39_pdf_font_loop_skips_non_files():
    src = Path("services/pdf_generator.py").read_text(encoding="utf-8")
    assert "not f.is_file()" in src
