"""AUDIT R4 (2026-07-05): Telegraph publishing/archive regressions.

Covers: full-name Scripture refs linkified as timestamps, [N/M] nav bracket
strip, FLOOD_WAIT retry, recursive CONTENT_TOO_BIG split, exact video_id
lookup, upsert wiping archived URLs, composed title >256, export label
collision.
"""
import json
import sqlite3
from pathlib import Path


def test_full_name_scripture_refs_not_linkified():
    """«в Иоанна 3:16» превращалось в ссылку на 3:16 минуты видео — guard
    знал только аббревиатуры (Ин., Рим.)."""
    from converters.md_telegraph import _linkify_inline_timestamps

    for text in (
        "Павел напоминает в Иоанна 3:16 Бог возлюбил мир.",
        "Он сказал об этом Римлянам 8:28 не является таймкодом.",
        "Смотри Псалом 22:1 в контексте страдания.",
    ):
        out = _linkify_inline_timestamps(
            [{"tag": "p", "children": [text]}], "https://youtu.be/abc", duration=4000,
        )
        assert "?t=" not in json.dumps(out), f"стих линкифицирован: {text}"

    # настоящий mid-text таймкод по-прежнему линкуется
    out = _linkify_inline_timestamps(
        [{"tag": "p", "children": ["Мысль завершена. 15:07 Новая мысль."]}],
        "https://youtu.be/abc", duration=4000,
    )
    assert "?t=907" in json.dumps(out)


def test_nav_brackets_survive_postprocess_bracket_strip():
    """Комментарий обещал не трогать [N/M], а регэксп трогал — навигация
    multipart-страниц публиковалась как «Назад: 1/3»."""
    import re
    src = Path("converters/md_telegraph.py").read_text(encoding="utf-8")
    m = re.search(r"re\.sub\(r'\\\[\(\?!.+?\](?:.|\n)*?text\)", src)
    assert m is not None or r"\[(?!\d+\s*/\s*\d+\])" in src
    # функциональная проверка самого паттерна
    pat = re.compile(r'\[(?!\d+\s*/\s*\d+\])([^\]]*?)\](?!\()')
    assert pat.sub(r'\1', "⬅ Назад: [1/3]  ➡ Дальше: [3/3]") == "⬅ Назад: [1/3]  ➡ Дальше: [3/3]"
    assert pat.sub(r'\1', "мусор [вставка] и [text](url)") == "мусор вставка и [text](url)"


def test_telegraph_post_retries_flood_wait():
    src = Path("services/telegraph.py").read_text(encoding="utf-8")
    post_once = src.split("async def _post_once", 1)[1].split("await _post_once(title, nodes)", 1)[0]
    assert "FLOOD_WAIT_" in post_once
    assert "asyncio.sleep" in post_once


def test_telegraph_split_is_recursive_and_logs_partial():
    src = Path("services/telegraph.py").read_text(encoding="utf-8")
    assert "_publish_chunk" in src
    assert "depth + 1" in src
    assert "опубликован НЕ полностью" in src


def test_generated_page_lookup_is_exact_sql(tmp_path):
    """Скан 500 последних терял записи старше — /repairpage VIDEO_ID
    отвечал «Не найдено в архиве» для существующей строки."""
    from core.generated_pages import get_generated_page_record, save_generated_page_record

    base = tmp_path
    for i in range(3):
        save_generated_page_record({
            "video_id": f"vid{i}", "title": f"T{i}", "author": "A",
            "synopsis_url": f"https://telegra.ph/s{i}",
        }, base_dir=base)
    rec = get_generated_page_record("vid1", base_dir=base)
    assert rec and rec["title"] == "T1"
    # точный SELECT, а не скан
    src = Path("core/generated_pages.py").read_text(encoding="utf-8")
    assert "WHERE video_id = ? LIMIT 1" in src


def test_archive_upsert_preserves_existing_urls(tmp_path):
    """Повторная обработка с упавшим Study затирала сохранённый study_url
    пустой строкой — «долговечный архив» терял ссылки."""
    from core.generated_pages import get_generated_page_record, save_generated_page_record

    base = tmp_path
    save_generated_page_record({
        "video_id": "v1", "title": "T", "author": "A",
        "synopsis_url": "https://telegra.ph/syn",
        "study_url": "https://telegra.ph/study",
    }, base_dir=base)
    # повторный прогон: study не собрался (пустая строка)
    save_generated_page_record({
        "video_id": "v1", "title": "T2", "author": "A",
        "synopsis_url": "https://telegra.ph/syn2",
        "study_url": "",
    }, base_dir=base)
    rec = get_generated_page_record("v1", base_dir=base)
    assert rec["synopsis_url"] == "https://telegra.ph/syn2", "непустой URL обновляется"
    assert rec["study_url"] == "https://telegra.ph/study", "пустой URL не затирает архив"
    assert rec["title"] == "T2"


def test_composed_telegraph_titles_respect_256():
    pages = Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    assert '_full_title = f"{page_prefix}{_sep}{tg_title}"[:256]' in pages
    assert '[:256 - len(_sfx)] + _sfx' in pages
    tg = Path("services/telegraph.py").read_text(encoding="utf-8")
    assert "[:256 - len(_sfx)] + _sfx" in tg


def test_export_public_archive_keeps_both_quotes_and_study(tmp_path):
    from tools.export_public_archive import extract_records

    db = tmp_path / "bot_cache.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE video_cache (video_id TEXT PRIMARY KEY, created_at INTEGER, "
            "ai_data TEXT, quotes_tg_url TEXT, study_tg_url TEXT)"
        )
        conn.execute(
            "INSERT INTO video_cache VALUES (?, ?, ?, ?, ?)",
            ("v1", 1, json.dumps({"real_title": "T", "real_author": "A"}),
             "https://telegra.ph/quotes", "https://telegra.ph/study"),
        )
    records = extract_records(db)
    assert records
    urls = set(records[0]["links"].values())
    assert "https://telegra.ph/quotes" in urls
    assert "https://telegra.ph/study" in urls, "вторая страница «Разбор» выпадала из экспорта"
