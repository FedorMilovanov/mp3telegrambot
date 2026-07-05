"""AUDIT R4 (2026-07-05): handlers-layer regressions.

Covers: /pdf stub, dead strim retrim (source video path), caption prefix
overflow, quiz correct-index shift after dedupe, /mode blocking SQLite and
double-tap edit, rate-limit refund on video-lock timeout.
"""
import json
import sqlite3
from pathlib import Path


def test_pdf_command_is_implemented():
    """Тело /pdf было заглушкой `...` — команда молча ничего не делала."""
    src = Path("handlers/commands.py").read_text(encoding="utf-8")
    body = src.split("async def pdf_command", 1)[1].split("async def disk_command", 1)[0]
    assert "(Existing implementation)" not in body
    assert "generate_sermon_pdf_async" in body
    assert "_resolve_segment_source" in body
    assert "reply_document" in body


def test_strim_record_stores_source_video_not_rendered_clip():
    """trim-кнопки хранили отрендеренный клип (который тут же удалялся) и
    абсолютные таймкоды исходника — каждый ретрим падал."""
    shorts = Path("pipelines/shorts.py").read_text(encoding="utf-8")
    save_call = shorts.split("short_trim_save(", 1)[1]
    assert "video_path=str(video_path)" in save_call.split(")", 20)[0] or \
        "video_path=str(video_path)" in save_call[:600]
    callbacks = Path("handlers/callbacks.py").read_text(encoding="utf-8")
    retrim = callbacks.split("async def _handle_strim_retrim", 1)[1]
    assert "video_path=str(video_path)" in retrim
    assert "video_path=str(out_path)" not in retrim
    # исходник перекачивается по требованию, а не мгновенный отказ
    assert "download_video_for_shorts" in callbacks


def test_strim_captions_trimmed_after_prefix():
    """Префикс добавлялся ПОСЛЕ обрезки до 1024 — длинные caption падали
    с MEDIA_CAPTION_TOO_LONG (AGENTS.md: respect Telegram limits)."""
    callbacks = Path("handlers/callbacks.py").read_text(encoding="utf-8")
    nosub = callbacks.split("async def _handle_strim_nosub", 1)[1].split("async def ", 1)[0]
    prefix_pos = nosub.find('caption = f"🚫 Без субтитров')
    trim_pos = nosub.find("safe_trim_caption", prefix_pos)
    assert prefix_pos != -1 and trim_pos > prefix_pos
    retrim = callbacks.split("async def _handle_strim_retrim", 1)[1]
    prefix_pos2 = retrim.find('caption = f"✂️ {label}')
    trim_pos2 = retrim.find("safe_trim_caption", prefix_pos2)
    assert prefix_pos2 != -1 and trim_pos2 > prefix_pos2


def test_quiz_correct_index_survives_dedupe():
    """Индекс correct указывает в исходный список ДО dedupe: выпавший дубль
    смещал правильный ответ на соседний вариант."""
    from services.quiz_generator import _parse_quiz_json

    raw = json.dumps([{
        "question": "Что утверждает Павел об оправдании согласно материалу проповеди?",
        "options": [
            "Оправдание достигается делами закона Моисея",
            "Оправдание достигается делами закона Моисея",
            "Оправдание принимается только верой во Христа",
            "Оправдание передаётся через церковные таинства",
            "Оправдание наследуется от праведных предков",
        ],
        "correct": 2,
        "explanation": "Павел утверждает оправдание верой (Рим 3:28).",
    }], ensure_ascii=False)
    result = _parse_quiz_json(raw)
    assert result, "вопрос с дублем варианта должен пройти после ремапа"
    item = result[0]
    assert item["options"][item["correct"]] == "Оправдание принимается только верой во Христа"


def test_mode_handlers_use_executor_and_ignore_not_modified():
    src = Path("handlers/mode_command.py").read_text(encoding="utf-8")
    assert src.count("run_in_executor") >= 2
    assert "is not modified" in src


def test_refund_rate_limit_decrements_today_only(tmp_path, monkeypatch):
    import core.utils as cu

    db = tmp_path / "rl.db"

    def _conn():
        return sqlite3.connect(db)

    with _conn() as c:
        c.execute(
            "CREATE TABLE rate_limit (user_id INTEGER PRIMARY KEY, "
            "last_request REAL, daily_count INTEGER, daily_date TEXT)"
        )
    monkeypatch.setattr(cu, "_db_conn", _conn)
    monkeypatch.setattr(cu, "WHITELIST_IDS", set())
    today = cu._today_str()
    with _conn() as c:
        c.execute("INSERT INTO rate_limit VALUES (1, 0, 3, ?)", (today,))
        c.execute("INSERT INTO rate_limit VALUES (2, 0, 5, '2000-01-01')")

    cu.refund_rate_limit(1)
    with _conn() as c:
        assert c.execute("SELECT daily_count FROM rate_limit WHERE user_id=1").fetchone()[0] == 2
    # не уходит в минус
    cu.refund_rate_limit(1)
    cu.refund_rate_limit(1)
    cu.refund_rate_limit(1)
    with _conn() as c:
        assert c.execute("SELECT daily_count FROM rate_limit WHERE user_id=1").fetchone()[0] == 0
    # чужой день не трогаем
    cu.refund_rate_limit(2)
    with _conn() as c:
        assert c.execute("SELECT daily_count FROM rate_limit WHERE user_id=2").fetchone()[0] == 5


def test_lock_timeout_refunds_reserved_slot():
    src = Path("handlers/commands.py").read_text(encoding="utf-8")
    timeout_block = src.split("except asyncio.TimeoutError:", 1)[1][:900]
    assert "arefund_rate_limit" in timeout_block
