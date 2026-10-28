from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from handlers import callbacks
from services import eng_subtitles, render_clips_montage, shorts_video


@pytest.mark.asyncio
async def test_reset_cache_callback_executes_delete_in_worker(monkeypatch) -> None:
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.return_value = SimpleNamespace(rowcount=7)
    monkeypatch.setattr(callbacks, "_db_conn", lambda: connection)
    monkeypatch.setattr(callbacks, "ADMIN_IDS", {123})

    query = SimpleNamespace(
        data="resetcache:all",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=123),
    )

    await callbacks.handle_callback(update, None)

    connection.execute.assert_called_once_with("DELETE FROM video_cache")
    connection.commit.assert_called_once_with()
    query.edit_message_text.assert_awaited_once_with("🗑 Весь кэш удалён: 7 записей.")


@pytest.mark.asyncio
async def test_eng_subtitles_gemini_path_writes_translated_srt(
    monkeypatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "original_audio.m4a").write_bytes(b"audio")
    segment = SimpleNamespace(start=0.0, end=1.5, text="Hello\nworld")
    model = SimpleNamespace(transcribe=Mock(return_value=([segment], None)))
    monkeypatch.setattr(
        eng_subtitles,
        "run_cancellable_process",
        AsyncMock(return_value=SimpleNamespace(returncode=0, stderr="")),
    )
    monkeypatch.setattr(eng_subtitles, "_get_audio_duration", AsyncMock(return_value=1.5))
    monkeypatch.setattr(eng_subtitles, "GEMINI_CLIENTS", [object()])
    monkeypatch.setattr(shorts_video, "_get_whisper_model", lambda: model)
    translate = AsyncMock(return_value={"1": "Привет\nмир"})
    monkeypatch.setattr(eng_subtitles, "_translate_chunk_with_retry", translate)

    result = await eng_subtitles.create_gemini_subtitles(
        "https://example.test/video",
        tmp_path,
        known_duration=2,
        lang="en",
    )

    assert result == tmp_path / "gemini_subs.srt"
    assert result.read_text(encoding="utf-8") == "1\n00:00:00,000 --> 00:00:01,500\nПривет мир\n\n"
    translate.assert_awaited_once_with([(1, "Hello\nworld")], "")


@pytest.mark.asyncio
async def test_extras_empty_gemini_response_uses_typed_callback_route(monkeypatch) -> None:
    client = object()
    monkeypatch.setattr(render_clips_montage, "GEMINI_CLIENTS", [client])
    monkeypatch.setattr(render_clips_montage, "HAS_GEMINI", True)
    monkeypatch.setattr(render_clips_montage, "make_text_config_smart", lambda **kwargs: kwargs)
    monkeypatch.setattr(render_clips_montage, "alog_gemini_response", AsyncMock())
    generate = AsyncMock(return_value=SimpleNamespace(text=""))
    monkeypatch.setattr(render_clips_montage, "gemini_generate", generate)

    result = await render_clips_montage.create_extras_candidates(
        {"format": "other"},
        "Title",
        "Author",
        60,
    )

    assert result == {"montage_candidates": [], "highlights_candidates": []}
    assert generate.await_args.args[0] == [client]
    assert callable(generate.await_args.args[1])
