"""ENG Full QA cache, input, autofix, and status lifecycle contracts."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipelines.livedub_full_qa_stage import run_livedub_full_qa

_DEFAULT = object()


class Status:
    def __init__(self, *, delete_error=None):
        self.delete_error = delete_error
        self.deleted = False

    async def delete(self):
        self.deleted = True
        if self.delete_error:
            raise self.delete_error


class Bot:
    def __init__(self, *, status_error=None, delete_error=None):
        self.status_error = status_error
        self.status = Status(delete_error=delete_error)
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)
        if self.status_error:
            raise self.status_error
        return self.status


def _invoke(
    tmp_path,
    monkeypatch,
    qa_result,
    *,
    autofix=False,
    cached=_DEFAULT,
    cache_result=(True, ""),
    cache_error=None,
    audio_part=None,
    client=None,
    max_size=50,
    bot=None,
    workdir=_DEFAULT,
    primary_mp3=True,
    secondary_mp3=False,
    find_tracks=None,
    fix=None,
    prepare=None,
):
    video = tmp_path / "dub.mp4"
    video.write_bytes(b"video")
    if workdir is _DEFAULT:
        workdir = tmp_path / "work"
        workdir.mkdir(exist_ok=True)
        (workdir / "translation.srt").write_text("translation", encoding="utf-8")
        (workdir / "gemini_subs.srt").write_text("excluded", encoding="utf-8")
    download = tmp_path / "downloads"
    download.mkdir(exist_ok=True)
    if primary_mp3:
        (download / "media1.mp3").write_bytes(b"primary")
    if secondary_mp3:
        (download / "media1_64.mp3").write_bytes(b"secondary")
    if cached is _DEFAULT:
        cached = {"ai_data": {"title": "cached analysis"}}
    calls = []

    async def run_qa(**kwargs):
        calls.append(("qa", kwargs))
        if isinstance(qa_result, BaseException):
            raise qa_result
        return qa_result

    async def get_cached(media_id):
        calls.append(("cache", media_id))
        if cache_error:
            raise cache_error
        return cached

    def validate(received):
        calls.append(("validate", received))
        return cache_result

    if find_tracks is None:
        clean = tmp_path / "clean.mp3"
        clean.write_bytes(b"clean")
        find_tracks = lambda received: (None, clean if received == workdir else None)

    async def default_fix(*_args):
        raise AssertionError("unexpected autofix")

    def default_prepare(path, *_args, **_kwargs):
        return path

    monkeypatch.setattr("services.livedub_qa.run_translation_qa", run_qa)
    monkeypatch.setattr("services.livedub_mix.find_pro_tracks", find_tracks)
    monkeypatch.setattr("services.livedub_mix.apply_qa_audio_fixes", fix or default_fix)
    monkeypatch.setattr("services.livedub_mix.prepare_livedub_send_path", prepare or default_prepare)
    bot = bot or Bot()
    result = asyncio.run(
        run_livedub_full_qa(
            video_path=video,
            workdir=workdir,
            download_dir=download,
            media_id="media1",
            title_line="Title - Author",
            duration=60,
            autofix=autofix,
            audio_part=audio_part,
            client=client,
            bot=bot,
            chat_id=123,
            reply_to=777,
            get_cached=get_cached,
            validate_cache=validate,
            max_file_size_mb=lambda: max_size,
        )
    )
    return result, bot, calls, video, workdir


def test_qa_asset_discovery_failure_is_nonfatal(tmp_path, monkeypatch):
    def fail_exists(_path):
        raise OSError("workdir unavailable")

    monkeypatch.setattr("pipelines.livedub_full_qa_stage._path_exists", fail_exists)
    qa = {"issues": []}

    result, _bot, calls, video, _workdir = _invoke(tmp_path, monkeypatch, qa)

    assert result == (video, qa, "")
    kwargs = next(call[1] for call in calls if call[0] == "qa")
    assert kwargs["dub_srt_path"] is None
    assert kwargs["dub_audio_path"] is None


def test_valid_cache_inputs_and_reused_part_reach_qa(tmp_path, monkeypatch):
    part = SimpleNamespace(name="audio-part")
    client = SimpleNamespace(name="client")
    qa = {"issues": []}

    result, bot, calls, video, workdir = _invoke(
        tmp_path,
        monkeypatch,
        qa,
        audio_part=part,
        client=client,
    )

    assert result == (video, qa, "")
    assert bot.status.deleted
    assert bot.messages[0]["reply_to_message_id"] == 777
    kwargs = next(call[1] for call in calls if call[0] == "qa")
    assert kwargs == {
        "dub_video_path": video,
        "original_audio_path": tmp_path / "downloads" / "media1.mp3",
        "ai_data": {"title": "cached analysis"},
        "duration": 60,
        "dub_srt_path": workdir / "translation.srt",
        "dub_audio_path": tmp_path / "clean.mp3",
        "existing_audio_part": part,
        "existing_client": client,
    }


def test_secondary_audio_and_part_without_name_use_fallbacks(tmp_path, monkeypatch):
    result, _bot, calls, _video, _workdir = _invoke(
        tmp_path,
        monkeypatch,
        {"issues": []},
        cached=None,
        cache_result=(False, "empty"),
        audio_part=object(),
        client="unused",
        primary_mp3=False,
        secondary_mp3=True,
    )

    kwargs = next(call[1] for call in calls if call[0] == "qa")
    assert kwargs["original_audio_path"] == tmp_path / "downloads" / "media1_64.mp3"
    assert kwargs["ai_data"] is None
    assert kwargs["existing_audio_part"] is None
    assert kwargs["existing_client"] is None
    assert result[2] == ""


def test_stale_cache_is_ignored(tmp_path, monkeypatch):
    _result, _bot, calls, _video, _workdir = _invoke(
        tmp_path,
        monkeypatch,
        {"issues": []},
        cache_result=(False, "stale"),
        primary_mp3=False,
    )

    kwargs = next(call[1] for call in calls if call[0] == "qa")
    assert kwargs["ai_data"] is None
    assert kwargs["original_audio_path"] is None


def test_cache_error_is_noncritical(tmp_path, monkeypatch):
    _result, _bot, calls, _video, _workdir = _invoke(
        tmp_path,
        monkeypatch,
        {"issues": []},
        cache_error=RuntimeError("database unavailable"),
    )

    assert next(call[1] for call in calls if call[0] == "qa")["ai_data"] is None


def test_discovery_and_clean_track_errors_fall_back_to_video_audio(tmp_path, monkeypatch):
    def no_tracks(_workdir):
        raise RuntimeError("tracks unavailable")

    _result, _bot, calls, _video, _workdir = _invoke(
        tmp_path,
        monkeypatch,
        {"issues": []},
        find_tracks=no_tracks,
    )

    kwargs = next(call[1] for call in calls if call[0] == "qa")
    assert kwargs["dub_srt_path"] is not None
    assert kwargs["dub_audio_path"] is None


def test_missing_workdir_skips_discovery(tmp_path, monkeypatch):
    _result, _bot, calls, _video, _workdir = _invoke(
        tmp_path,
        monkeypatch,
        {"issues": []},
        workdir=None,
    )

    kwargs = next(call[1] for call in calls if call[0] == "qa")
    assert kwargs["dub_srt_path"] is None
    assert kwargs["dub_audio_path"] is None


def test_discovery_error_and_empty_qa_result_are_noncritical(tmp_path, monkeypatch):
    real_glob = Path.glob

    def broken_glob(path, pattern):
        if path.name == "work":
            raise RuntimeError("directory listing failed")
        return real_glob(path, pattern)

    monkeypatch.setattr(Path, "glob", broken_glob)
    result, bot, calls, video, _workdir = _invoke(
        tmp_path,
        monkeypatch,
        None,
        autofix=True,
    )

    assert result == (video, None, "")
    assert bot.status.deleted
    kwargs = next(call[1] for call in calls if call[0] == "qa")
    assert kwargs["dub_srt_path"] is None
    assert kwargs["dub_audio_path"] is None


def test_enabled_autofix_does_not_run_without_major_issues(tmp_path, monkeypatch):
    qa = {"issues": [{"severity": "minor"}]}
    result, _bot, _calls, video, _workdir = _invoke(
        tmp_path,
        monkeypatch,
        qa,
        autofix=True,
    )

    assert result == (video, qa, "")


def test_major_autofix_success_uses_prepared_fixed_video(tmp_path, monkeypatch):
    fixed = tmp_path / "fixed.mp4"
    fixed.write_bytes(b"fixed")
    prepared = tmp_path / "prepared.mp4"
    prepared.write_bytes(b"prepared")
    issues = [{"severity": "major"}, {"severity": "minor"}]
    calls = []

    async def fix(workdir, received_issues):
        calls.append(("fix", workdir, received_issues))
        return fixed

    def prepare(path, title, *, fallback):
        calls.append(("prepare", path, title, fallback))
        return prepared

    result, _bot, qa_calls, _video, workdir = _invoke(
        tmp_path,
        monkeypatch,
        {"issues": issues},
        autofix=True,
        fix=fix,
        prepare=prepare,
    )

    assert result[0] == prepared
    assert "В 1 месте(ах)" in result[2]
    assert ("fix", workdir, issues) in calls
    assert ("prepare", fixed, "Title - Author", "media1_fixed") in calls
    assert next(call[1] for call in qa_calls if call[0] == "qa")


def test_oversized_fix_keeps_original_without_note(tmp_path, monkeypatch):
    fixed = tmp_path / "fixed.mp4"
    fixed.write_bytes(b"fixed")

    async def fix(*_args):
        return fixed

    result, _bot, _calls, video, _workdir = _invoke(
        tmp_path,
        monkeypatch,
        {"issues": [{"severity": "major"}]},
        autofix=True,
        fix=fix,
        max_size=0,
    )

    assert result == (video, {"issues": [{"severity": "major"}]}, "")


def test_missing_fix_has_honest_note(tmp_path, monkeypatch):
    async def fix(*_args):
        return None

    result, _bot, _calls, video, _workdir = _invoke(
        tmp_path,
        monkeypatch,
        {"issues": [{"severity": "major"}]},
        autofix=True,
        fix=fix,
    )

    assert result[0] == video
    assert "авто-приглушение не удалось" in result[2]


def test_autofix_error_keeps_original_and_qa_result(tmp_path, monkeypatch):
    async def fix(*_args):
        raise RuntimeError("fix failed")

    qa = {"issues": [{"severity": "major"}]}
    result, _bot, _calls, video, _workdir = _invoke(
        tmp_path,
        monkeypatch,
        qa,
        autofix=True,
        fix=fix,
    )

    assert result == (video, qa, "")


def test_qa_error_still_deletes_status(tmp_path, monkeypatch):
    result, bot, _calls, video, _workdir = _invoke(
        tmp_path,
        monkeypatch,
        RuntimeError("QA failed"),
    )

    assert result == (video, None, "")
    assert bot.status.deleted


def test_status_send_and_delete_errors_are_best_effort(tmp_path, monkeypatch):
    bot = Bot(status_error=RuntimeError("status unavailable"))
    result, bot, calls, video, _workdir = _invoke(
        tmp_path,
        monkeypatch,
        {"issues": []},
        bot=bot,
    )
    assert result[0] == video
    assert not bot.status.deleted
    assert any(call[0] == "qa" for call in calls)

    bot = Bot(delete_error=RuntimeError("delete unavailable"))
    _result, bot, _calls, _video, _workdir = _invoke(
        tmp_path,
        monkeypatch,
        {"issues": []},
        bot=bot,
    )
    assert bot.status.deleted


def test_qa_cancellation_propagates_but_deletes_status(tmp_path, monkeypatch):
    with pytest.raises(asyncio.CancelledError):
        _invoke(tmp_path, monkeypatch, asyncio.CancelledError())
