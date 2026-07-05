"""AUDIT R4 (2026-07-05): pipelines-layer regressions.

Covers: dead ID3 chapters (timestamps str vs list), borrowed LiveDub video
deleted by montage/shorts, orphaned livedub subtasks, lost ENG delivery on
too-big early return, playlist unguarded edit_text, cache-hit re-download,
progress dedup cross-chat bleed, ENG Quick fake success.
"""
import asyncio
from pathlib import Path


def test_timestamps_to_chapter_list_parses_pipeline_string():
    """ai_data['timestamps'] — строка "M:SS тема\\n…"; старый isinstance-гейт
    делал вшивание ID3-глав мёртвым для всех mp3."""
    from services.mp3_chapters import timestamps_to_chapter_list

    s = "0:00 Вступление\n12:45 Основная часть\n1:02:10 Финал и молитва"
    out = timestamps_to_chapter_list(s)
    assert out == [
        {"time": "0:00", "topic": "Вступление"},
        {"time": "12:45", "topic": "Основная часть"},
        {"time": "1:02:10", "topic": "Финал и молитва"},
    ]
    # список dict'ов проходит как есть, мусор отфильтровывается
    lst = [{"time": "0:00", "topic": "А"}, {"bad": 1}, "x", {"time": "", "topic": "Б"}]
    assert timestamps_to_chapter_list(lst) == [{"time": "0:00", "topic": "А"}]
    assert timestamps_to_chapter_list(None) == []
    assert timestamps_to_chapter_list("мусор без таймкода") == []


def test_pipeline_embeds_chapters_from_string_timestamps():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert src.count("timestamps_to_chapter_list(") >= 2, "оба пути (кэш и свежий)"
    assert "isinstance(_ts_for_chap, list)" not in src
    assert "isinstance(_ts_chap_c, list)" not in src


def test_montage_does_not_own_borrowed_livedub_video():
    """owned_video=True на LiveDub-видео удалял переведённое видео, и
    highlights молча рендерился из английского оригинала."""
    src = Path("pipelines/montage.py").read_text(encoding="utf-8")
    for chunk in src.split("async def ")[1:]:
        if "livedub_video_path and _P(livedub_video_path).exists()" not in chunk:
            continue
        livedub_branch = chunk.split("livedub_video_path).exists()", 1)[1]
        before_else = livedub_branch.split("else:", 1)[0]
        assert "owned_video = True" not in before_else, \
            "owned_video нельзя ставить в LiveDub-ветке"


def test_shorts_keeps_video_for_clips_and_never_deletes_borrowed():
    src = Path("pipelines/shorts.py").read_text(encoding="utf-8")
    assert 'asettings_get("clips")' in src
    assert "_borrowed" in src
    assert "video_path == livedub_video_path" in src


def test_livedub_bg_cancels_inner_tasks_on_teardown():
    """cancel() родителя оставлял vot-cli/ffmpeg/Whisper качать сотни МБ в
    уже удалённый rmtree каталог."""
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    bg = src.split("async def _run_livedub_bg", 1)[1].split("live_dub_task = asyncio.create_task", 1)[0]
    assert "finally:" in bg
    assert "asyncio.gather(*_inner, return_exceptions=True)" in bg


def test_too_big_early_return_still_sends_livedub():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    for marker in ("даже после сжатия.", "даже после сжатия до 64 kbps"):
        idx = src.find(marker)
        assert idx != -1
        window = src[idx:idx + 700]
        assert "_send_livedub_result()" in window, f"нет отправки LiveDub после '{marker}'"


def test_playlist_status_edits_are_guarded():
    src = Path("pipelines/playlist.py").read_text(encoding="utf-8")
    # оставшиеся raw edit_text обязаны быть внутри try с reply_text-fallback
    for i, line in enumerate(src.splitlines(), 1):
        if "await status_msg.edit_text(" in line:
            context = "\n".join(src.splitlines()[max(0, i - 4):i + 4])
            assert "except" in context, f"строка {i}: незащищённый edit_text"


def test_progress_dedup_key_includes_chat_id():
    from core.progress import _last_text_cache, set_progress

    _last_text_cache.clear()

    class _FakeMsg:
        def __init__(self, chat_id, message_id):
            self.chat_id = chat_id
            self.message_id = message_id
            self.edits = []

        async def edit_text(self, text):
            self.edits.append(text)

    async def _run():
        m1 = _FakeMsg(chat_id=111, message_id=42)
        m2 = _FakeMsg(chat_id=222, message_id=42)  # тот же message_id, другой чат
        await set_progress(m1, 1)
        await set_progress(m2, 1)
        return m1, m2

    m1, m2 = asyncio.run(_run())
    assert len(m1.edits) == 1
    assert len(m2.edits) == 1, "второй чат не должен «наследовать» кэш первого"


def test_eng_quick_returns_delivery_status_and_respects_silent_errors():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    block = src.split("ENG Quick done:", 1)[0]
    tail = block[-3500:]
    assert "return _delivered" in src.split("ENG Quick done:", 1)[1][:300]
    assert "if silent_errors:" in tail


def test_cache_hit_reuses_existing_compressed_mp3():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    idx = src.find("Кэш аудио: реюз существующего")
    assert idx != -1
    window = src[idx - 900:idx]
    assert 'glob(f"{media_id}*.mp3")' in window
