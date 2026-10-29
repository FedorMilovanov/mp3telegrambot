from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from handlers import callbacks
from services import eng_subtitles, render_clips_montage, shorts_video


@pytest.mark.asyncio
async def test_reset_cache_callback_executes_delete_in_worker(monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch: pytest.MonkeyPatch,
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
async def test_extras_empty_gemini_response_uses_typed_callback_route(monkeypatch: pytest.MonkeyPatch) -> None:
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
    await_args = generate.await_args
    assert await_args is not None
    assert await_args.args[0] == [client]
    assert callable(await_args.args[1])


@pytest.mark.asyncio
async def test_shorts_download_wrapper_returns_owned_legacy_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    expected = tmp_path / "source.mp4"
    legacy_download = AsyncMock(return_value=expected)
    monkeypatch.setattr(shorts_video, "_LEGACY_DOWNLOAD_VIDEO", legacy_download)

    result = await shorts_video.download_video_for_shorts(
        "https://example.test/video",
        "media-1",
        workdir=tmp_path,
    )

    assert result is expected
    legacy_download.assert_called_once_with(
        "https://example.test/video",
        "media-1",
        workdir=tmp_path,
    )


def test_whisper_word_merging_and_gap_filling_execute_typed_results() -> None:
    from services import shorts_video_impl

    words = [
        {"word": "что", "start": 0.0, "end": 0.2},
        {"word": "-то", "start": 0.8, "end": 1.0},
    ]

    merged = shorts_video_impl._merge_hyphenated_particles(words)
    filled = shorts_video_impl._fill_timing_gaps(words, max_gap=0.3)

    assert len(merged) == 1
    assert merged[0]["word"] == "что-то"
    assert merged[0]["end"] == 1.0
    assert filled[0]["end"] == 0.8
