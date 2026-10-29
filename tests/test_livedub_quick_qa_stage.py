"""ENG Quick QA selection, reporting, and autofix contracts."""

import pathlib

import asyncio
import os
from typing import Any

import pytest

from pipelines.livedub_quick_qa_stage import QuickQaResult, run_livedub_quick_qa


class Bot:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> None:
        self.messages.append(kwargs)


def _video(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "dub.mp4"
    path.write_bytes(b"video")
    return path


def _workdir(tmp_path: pathlib.Path, *, original: bool = True) -> pathlib.Path:
    workdir = tmp_path / "work"
    workdir.mkdir()
    if original:
        (workdir / "original_video.mp3").write_bytes(b"original")
    return workdir


def _patch_qa(monkeypatch: pytest.MonkeyPatch, result: Any, calls: list[Any]) -> None:
    async def run_qa(**kwargs: Any) -> Any:
        calls.append(("qa", kwargs))
        if isinstance(result, BaseException):
            raise result
        return result

    def format_report(qa: Any, *, video_url: str) -> str:
        calls.append(("format", qa, video_url))
        return "QA report"

    monkeypatch.setattr("services.livedub_qa.run_translation_qa", run_qa)
    monkeypatch.setattr("services.livedub_qa.format_qa_report", format_report)
    monkeypatch.setattr("services.livedub_info.get_light_model", lambda: "light-model")
    monkeypatch.setattr("services.livedub_mix.find_pro_tracks", lambda _workdir: (None, None))


def _run(
    video: pathlib.Path, workdir: pathlib.Path | None, *, duration: int = 60, bot: Bot | None = None
) -> QuickQaResult:
    return asyncio.run(
        run_livedub_quick_qa(
            video_path=video,
            workdir=workdir,
            duration=duration,
            video_url="https://example.test/video",
            bot=bot or Bot(),
            chat_id=123,
            reply_to=777,
        )
    )


def test_duration_limit_skips_before_loading_qa_services(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LIVEDUB_QUICK_QA_MAX_DURATION", "10")
    video = _video(tmp_path)

    assert _run(video, None, duration=11) == (
        video,
        "",
        "Проверка перевода пропущена: ролик длиннее 10 сек",
    )


def test_invalid_duration_limit_uses_historical_default(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LIVEDUB_QUICK_QA_MAX_DURATION", "invalid")
    video = _video(tmp_path)

    result = _run(video, None, duration=121)

    assert result[2] == "Проверка перевода пропущена: ролик длиннее 120 сек"


def test_missing_original_has_explicit_skip_note(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    _patch_qa(monkeypatch, {"issues": []}, calls)
    video = _video(tmp_path)
    workdir = _workdir(tmp_path, original=False)

    result = _run(video, workdir)

    assert result == (
        video,
        "",
        "Проверка перевода пропущена: оригинальная дорожка не сохранилась",
    )
    assert calls == []


def test_missing_workdir_has_the_same_explicit_skip_note(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []
    _patch_qa(monkeypatch, {"issues": []}, calls)
    video = _video(tmp_path)

    result = _run(video, None)

    assert "оригинальная дорожка не сохранилась" in result[2]
    assert calls == []


def test_nonexistent_workdir_skips_clean_track_discovery(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []
    _patch_qa(monkeypatch, {"issues": []}, calls)
    monkeypatch.setattr(
        "services.livedub_mix.find_pro_tracks",
        lambda _workdir: pytest.fail("missing workdir must not be scanned"),
    )
    video = _video(tmp_path)

    result = _run(video, tmp_path / "missing")

    assert "оригинальная дорожка не сохранилась" in result[2]
    assert calls == []


def test_latest_inputs_clean_ru_and_env_reach_qa(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = _video(tmp_path)
    workdir = _workdir(tmp_path, original=False)
    old_original = workdir / "original_video.old.mp3"
    new_original = workdir / "original_video.new.mp3"
    old_original.write_bytes(b"old")
    new_original.write_bytes(b"new")
    os.utime(old_original, (1, 1))
    os.utime(new_original, (2, 2))
    (workdir / "gemini_subs.srt").write_text("excluded", encoding="utf-8")
    old_srt = workdir / "old.srt"
    new_srt = workdir / "new.srt"
    old_srt.write_text("old", encoding="utf-8")
    new_srt.write_text("new", encoding="utf-8")
    os.utime(old_srt, (1, 1))
    os.utime(new_srt, (3, 3))
    clean_ru = workdir / "clean-ru.mp3"
    clean_ru.write_bytes(b"clean")
    calls: list[Any] = []
    qa = {"issues": [{"severity": "minor", "problem": "small"}]}
    _patch_qa(monkeypatch, qa, calls)
    monkeypatch.setattr(
        "services.livedub_mix.find_pro_tracks",
        lambda received: (None, clean_ru) if received == workdir else None,
    )
    monkeypatch.setenv("LIVEDUB_QUICK_QA_MODEL", "configured-model")
    monkeypatch.setenv("LIVEDUB_QUICK_QA_THINKING", "")

    result = _run(video, workdir)

    assert result == (video, "QA report", "")
    assert calls[0] == (
        "qa",
        {
            "dub_video_path": video,
            "original_audio_path": new_original,
            "ai_data": None,
            "duration": 60,
            "model_name": "configured-model",
            "dub_srt_path": new_srt,
            "dub_audio_path": clean_ru,
            "thinking_level": "minimal",
        },
    )
    assert calls[1] == ("format", qa, "https://example.test/video")


def test_empty_qa_response_is_not_a_report(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    _patch_qa(monkeypatch, None, calls)
    video = _video(tmp_path)

    result = _run(video, _workdir(tmp_path))

    assert result == (video, "", "Проверка перевода не удалась (Gemini не дал ответ)")
    assert [call[0] for call in calls] == ["qa"]


def test_major_issues_apply_fix_and_notify_before_report(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = _video(tmp_path)
    workdir = _workdir(tmp_path)
    fixed = workdir / "fixed.mp4"
    fixed.write_bytes(b"fixed")
    issues = [{"severity": "major", "problem": "bad"}, {"severity": "minor"}]
    calls: list[Any] = []
    _patch_qa(monkeypatch, {"issues": issues}, calls)

    async def fix(received_workdir: pathlib.Path, received_issues: list[dict[str, Any]]) -> pathlib.Path:
        calls.append(("fix", received_workdir, received_issues))
        return fixed

    monkeypatch.setattr("services.livedub_mix.apply_qa_audio_fixes", fix)
    bot = Bot()

    result = _run(video, workdir, bot=bot)

    assert result == (fixed, "QA report", "")
    assert ("fix", workdir, issues) in calls
    assert len(bot.messages) == 1
    assert bot.messages[0]["chat_id"] == 123
    assert bot.messages[0]["reply_to_message_id"] == 777
    assert "серьёзное искажение" in bot.messages[0]["text"]


def test_missing_fixed_file_keeps_original_and_sends_no_notice(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = _video(tmp_path)
    workdir = _workdir(tmp_path)
    calls: list[Any] = []
    _patch_qa(monkeypatch, {"issues": [{"severity": "major"}]}, calls)

    async def fix(*_args: Any) -> pathlib.Path:
        return workdir / "missing.mp4"

    monkeypatch.setattr("services.livedub_mix.apply_qa_audio_fixes", fix)
    bot = Bot()

    result = _run(video, workdir, bot=bot)

    assert result == (video, "QA report", "")
    assert bot.messages == []


def test_autofix_error_is_noncritical(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = _video(tmp_path)
    calls: list[Any] = []
    _patch_qa(monkeypatch, {"issues": [{"severity": "major"}]}, calls)

    async def fix(*_args: Any) -> None:
        raise RuntimeError("fix failed")

    monkeypatch.setattr("services.livedub_mix.apply_qa_audio_fixes", fix)

    result = _run(video, _workdir(tmp_path))

    assert result == (video, "QA report", "")


def test_clean_track_discovery_error_falls_back_to_video_audio(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []
    _patch_qa(monkeypatch, {"issues": []}, calls)

    def no_tracks(_workdir: pathlib.Path) -> tuple[Any, Any]:
        raise RuntimeError("no tracks")

    monkeypatch.setattr("services.livedub_mix.find_pro_tracks", no_tracks)
    video = _video(tmp_path)

    result = _run(video, _workdir(tmp_path))

    assert result == (video, "QA report", "")
    assert calls[0][1]["dub_audio_path"] is None


def test_qa_error_returns_internal_skip_note(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    _patch_qa(monkeypatch, RuntimeError("QA unavailable"), calls)
    video = _video(tmp_path)

    result = _run(video, _workdir(tmp_path))

    assert result == (video, "", "Проверка перевода не удалась (внутренняя ошибка)")


def test_qa_cancellation_is_not_swallowed(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    _patch_qa(monkeypatch, asyncio.CancelledError(), calls)
    video = _video(tmp_path)

    with pytest.raises(asyncio.CancelledError):
        _run(video, _workdir(tmp_path))
