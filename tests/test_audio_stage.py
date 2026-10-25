#!/usr/bin/env python3
"""Стадия «получить mp3»: реюз, скачивание, сжатие под лимит отправки.

Раньше этот код существовал двумя копиями внутри `process_single_video` и
проверялся только живым ботом: упавший yt-dlp, отсутствующий файл, файл больше
лимита Telegram — всё это выяснялось на пользователе. Здесь каждая из этих
веток воспроизводится без сети и без ffmpeg.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pipelines import audio_stage


class _Proc:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr


@pytest.fixture(autouse=True)
def _no_real_io(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Никакой реальной нормализации и пережатия: только фиксация вызовов."""
    normalized: list[Path] = []

    async def owned(coro):
        return await coro

    monkeypatch.setattr(audio_stage, "await_owned_coroutine", owned)
    monkeypatch.setattr(audio_stage, "normalize_mp3_lossless", lambda path: normalized.append(path) or True)
    return normalized


def _make_mp3(path: Path, size_mb: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * int(size_mb * 1024 * 1024))
    return path


def _prepare(tmp_path: Path, **overrides):
    kwargs = {
        "media_id": "abc123",
        "url": "https://youtu.be/abc123",
        "duration": 600,
        "download_dir": tmp_path,
        "max_size_mb": 50,
    }
    kwargs.update(overrides)
    return asyncio.run(audio_stage.prepare_audio(**kwargs))


# ── переиспользование и скачивание ──────────────────────────────────────────


def test_existing_file_is_reused_without_download(tmp_path, monkeypatch) -> None:
    _make_mp3(tmp_path / "abc123.mp3", 1.0)
    calls: list[list[str]] = []

    async def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return _Proc()

    monkeypatch.setattr(audio_stage, "run_cancellable_process", fake_run)
    result = _prepare(tmp_path)

    assert calls == []
    assert result.reused is True
    assert result.path.name == "abc123.mp3"
    assert result.size_mb == pytest.approx(1.0, abs=0.01)


def test_compressed_variant_is_reused(tmp_path, monkeypatch) -> None:
    """Прошлый прогон мог оставить только `_64.mp3` — качать заново не нужно."""
    _make_mp3(tmp_path / "abc123_64.mp3", 0.5)

    async def boom(_cmd, **_kwargs):
        raise AssertionError("скачивания быть не должно")

    monkeypatch.setattr(audio_stage, "run_cancellable_process", boom)
    result = _prepare(tmp_path)

    assert result.reused is True
    assert result.path.name == "abc123_64.mp3"
    assert result.bitrate == "64"


def test_download_then_normalize(tmp_path, monkeypatch, _no_real_io) -> None:
    """Скачанный файл нормализуется без потерь (mp3gain), а не loudnorm'ом."""
    commands: list[list[str]] = []

    async def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        _make_mp3(tmp_path / "abc123.mp3", 2.0)
        return _Proc()

    monkeypatch.setattr(audio_stage, "run_cancellable_process", fake_run)
    result = _prepare(tmp_path)

    assert result.reused is False
    assert _no_real_io == [tmp_path / "abc123.mp3"]
    assert result.size_mb == pytest.approx(2.0, abs=0.01)


def test_download_passes_sponsorblock_and_extra_args(tmp_path, monkeypatch) -> None:
    seen: list[list[str]] = []

    async def fake_run(cmd, **_kwargs):
        seen.append(cmd)
        _make_mp3(tmp_path / "abc123.mp3", 1.0)
        return _Proc()

    monkeypatch.setattr(audio_stage, "run_cancellable_process", fake_run)
    _prepare(
        tmp_path,
        sponsorblock_args=("--sponsorblock-remove", "sponsor"),
        extra_args=("--format", "bestaudio/best"),
    )

    cmd = seen[0]
    assert "--sponsorblock-remove" in cmd
    assert cmd[cmd.index("--format") + 1] == "bestaudio/best"
    assert cmd[-1] == "https://youtu.be/abc123"
    assert "--embed-metadata" in cmd and "--embed-thumbnail" in cmd


def test_download_failure_reports_stderr(tmp_path, monkeypatch) -> None:
    async def fake_run(_cmd, **_kwargs):
        return _Proc(returncode=1, stderr="ERROR: Video unavailable")

    monkeypatch.setattr(audio_stage, "run_cancellable_process", fake_run)
    with pytest.raises(audio_stage.AudioError, match="Video unavailable"):
        _prepare(tmp_path)


def test_download_without_result_fails(tmp_path, monkeypatch) -> None:
    """rc=0, но файла нет — это тоже ошибка, а не «продолжаем без mp3»."""

    async def fake_run(_cmd, **_kwargs):
        return _Proc(returncode=0)

    monkeypatch.setattr(audio_stage, "run_cancellable_process", fake_run)
    with pytest.raises(audio_stage.AudioError, match="MP3 файл не найден"):
        _prepare(tmp_path)


def test_missing_message_is_configurable(tmp_path, monkeypatch) -> None:
    """Ветка кэша говорит «Не удалось скачать аудио» — текст не унифицируем молча."""

    async def fake_run(_cmd, **_kwargs):
        return _Proc()

    monkeypatch.setattr(audio_stage, "run_cancellable_process", fake_run)
    with pytest.raises(audio_stage.AudioError, match="Не удалось скачать аудио"):
        _prepare(tmp_path, missing_message="Не удалось скачать аудио")


# ── лимит размера и пережатие ───────────────────────────────────────────────


def test_file_within_limit_is_returned_as_is(tmp_path, monkeypatch) -> None:
    _make_mp3(tmp_path / "abc123.mp3", 10.0)

    async def boom(*_a, **_k):
        raise AssertionError("пережатия быть не должно")

    monkeypatch.setattr(audio_stage, "reencode_mp3_64k_atomic", boom)
    assert _prepare(tmp_path, max_size_mb=50).size_mb == pytest.approx(10.0, abs=0.01)


def test_large_file_is_squeezed_to_64k(tmp_path, monkeypatch) -> None:
    _make_mp3(tmp_path / "abc123.mp3", 3.0)
    squeezed: list[tuple[Path, Path]] = []
    notices: list[float] = []

    async def fake_reencode(source, output):
        squeezed.append((source, output))
        _make_mp3(output, 1.0)
        return True

    async def notice(size_mb):
        notices.append(size_mb)

    monkeypatch.setattr(audio_stage, "reencode_mp3_64k_atomic", fake_reencode)
    result = _prepare(tmp_path, max_size_mb=2, on_squeeze=notice)

    assert squeezed == [(tmp_path / "abc123.mp3", tmp_path / "abc123_64.mp3")]
    assert result.path.name == "abc123_64.mp3"
    assert result.bitrate == "64"
    assert result.size_mb == pytest.approx(1.0, abs=0.01)
    assert notices == [pytest.approx(3.0, abs=0.01)]
    # Исходник удалён только после успешного пережатия.
    assert not (tmp_path / "abc123.mp3").exists()


def test_failed_reencode_keeps_original(tmp_path, monkeypatch) -> None:
    """Сбой ffmpeg не должен оставлять пользователя без файла вообще."""
    _make_mp3(tmp_path / "abc123.mp3", 3.0)

    async def fake_reencode(_source, _output):
        return False

    monkeypatch.setattr(audio_stage, "reencode_mp3_64k_atomic", fake_reencode)
    with pytest.raises(audio_stage.AudioTooLarge):
        _prepare(tmp_path, max_size_mb=2)
    assert (tmp_path / "abc123.mp3").exists()


def test_still_too_large_after_squeeze(tmp_path, monkeypatch) -> None:
    _make_mp3(tmp_path / "abc123.mp3", 3.0)

    async def fake_reencode(_source, output):
        _make_mp3(output, 2.5)
        return True

    monkeypatch.setattr(audio_stage, "reencode_mp3_64k_atomic", fake_reencode)
    with pytest.raises(audio_stage.AudioTooLarge) as exc_info:
        _prepare(tmp_path, max_size_mb=2)

    error = exc_info.value
    # Размер — у файла, который реально остался, а не у исходного: иначе
    # сообщение «3.0 МБ даже после сжатия» было бы ложью.
    assert error.size_mb == pytest.approx(2.5, abs=0.01)
    assert error.limit_mb == 2
    assert "даже после сжатия до 64 kbps" in str(error)
    assert "лимит отправки — 2 МБ" in str(error)


def test_already_64k_is_not_recompressed_into_itself(tmp_path, monkeypatch) -> None:
    """`_64.mp3` пережимать в себя нельзя: ffmpeg читал бы файл, который пишет."""
    _make_mp3(tmp_path / "abc123_64.mp3", 3.0)

    async def boom(*_a, **_k):
        raise AssertionError("самопережатия быть не должно")

    monkeypatch.setattr(audio_stage, "reencode_mp3_64k_atomic", boom)
    with pytest.raises(audio_stage.AudioTooLarge):
        _prepare(tmp_path, max_size_mb=2)


# ── чистые помощники ────────────────────────────────────────────────────────


@pytest.mark.parametrize(("duration", "expected"), [(0, "128K"), (3599, "128K"), (3601, "64K"), (10_800, "64K")])
def test_quality_depends_on_duration(duration, expected) -> None:
    assert audio_stage.audio_quality(duration) == expected


def test_bitrate_label_prefers_actual_file() -> None:
    assert audio_stage.bitrate_label(600, "abc.mp3") == "128"
    assert audio_stage.bitrate_label(600, "abc_64.mp3") == "64"
    assert audio_stage.bitrate_label(7200, "abc.mp3") == "64"


def test_find_existing_is_deterministic(tmp_path) -> None:
    _make_mp3(tmp_path / "abc_b.mp3", 0.1)
    _make_mp3(tmp_path / "abc_a.mp3", 0.1)
    assert audio_stage.find_existing(tmp_path, "abc").name == "abc_a.mp3"


def test_find_existing_prefers_exact_name(tmp_path) -> None:
    _make_mp3(tmp_path / "abc_a.mp3", 0.1)
    _make_mp3(tmp_path / "abc.mp3", 0.1)
    assert audio_stage.find_existing(tmp_path, "abc").name == "abc.mp3"


def test_find_existing_returns_none_for_empty_dir(tmp_path) -> None:
    assert audio_stage.find_existing(tmp_path, "abc") is None
