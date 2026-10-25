#!/usr/bin/env python3
"""Обложка необязательна: файловые сценарии fresh/cache без сети и Pillow."""
from __future__ import annotations

import asyncio
import logging
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from pipelines.thumbnail_stage import (
    THUMBNAIL_TIMEOUT_SEC,
    ThumbnailDeps,
    load_thumbnail,
    thumbnail_command,
)

URL = "https://youtu.be/video-id"


def _prepare(path: Path) -> BytesIO:
    return BytesIO(path.read_bytes())


def _deps(run: Any, prepare: Any = _prepare) -> ThumbnailDeps:
    return ThumbnailDeps(
        run=run,
        prepare=prepare,
        base_args=("yt-dlp", "--no-warnings"),
    )


def _load(
    tmp_path: Path,
    deps: ThumbnailDeps,
    *,
    cache_hit: bool = False,
) -> BytesIO | None:
    return asyncio.run(
        load_thumbnail(
            media_id="video-id",
            url=URL,
            thumbs_dir=tmp_path,
            cache_hit=cache_hit,
            deps=deps,
        )
    )


def test_thumbnail_command_preserves_flags_output_and_url(tmp_path: Path) -> None:
    command = thumbnail_command(
        media_id="video-id",
        url=URL,
        thumbs_dir=tmp_path,
        base_args=("yt-dlp", "--proxy", "http://proxy"),
    )

    assert command == [
        "yt-dlp",
        "--proxy",
        "http://proxy",
        "--skip-download",
        "--write-thumbnail",
        "--no-playlist",
        "--convert-thumbnails",
        "jpg",
        "--output",
        str(tmp_path / "video-id_thumb.%(ext)s"),
        URL,
    ]


@pytest.mark.parametrize(
    ("cache_hit", "expected_log"),
    [
        (False, "Обложка кэш: используем video-id_thumb.webp"),
        (True, "Обложка кэш (hit): используем video-id_thumb.webp"),
    ],
)
def test_existing_file_is_reused_without_process(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    cache_hit: bool,
    expected_log: str,
) -> None:
    path = tmp_path / "video-id_thumb.webp"
    path.write_bytes(b"existing-image")
    prepared: list[Path] = []

    async def unexpected_run(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("yt-dlp не нужен при существующей обложке")

    def prepare(value: Path) -> BytesIO:
        prepared.append(value)
        return _prepare(value)

    with caplog.at_level(logging.INFO):
        result = _load(tmp_path, _deps(unexpected_run, prepare), cache_hit=cache_hit)

    assert result is not None
    assert result.getvalue() == b"existing-image"
    assert prepared == [path]
    assert expected_log in caplog.text


@pytest.mark.parametrize(
    ("cache_hit", "expected_log"),
    [
        (False, "Обложка скачана: video-id_thumb.jpg"),
        (True, "Обложка (кэш) скачана: video-id_thumb.jpg"),
    ],
)
def test_download_success_uses_timeout_and_prepares_new_file(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    cache_hit: bool,
    expected_log: str,
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    async def run(command: list[str], **kwargs: Any) -> Any:
        calls.append((command, kwargs))
        (tmp_path / "video-id_thumb.jpg").write_bytes(b"downloaded-image")
        return SimpleNamespace(returncode=0, stderr="")

    with caplog.at_level(logging.INFO):
        result = _load(tmp_path, _deps(run), cache_hit=cache_hit)

    assert result is not None
    assert result.getvalue() == b"downloaded-image"
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[:2] == ["yt-dlp", "--no-warnings"]
    assert command[-1] == URL
    assert kwargs == {"timeout": THUMBNAIL_TIMEOUT_SEC, "text": True}
    assert expected_log in caplog.text


def test_nonzero_rc_still_uses_file_created_by_ytdlp(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run(_command: list[str], **_kwargs: Any) -> Any:
        (tmp_path / "video-id_thumb.jpg").write_bytes(b"partial-but-usable")
        return SimpleNamespace(returncode=9, stderr="warning after download")

    with caplog.at_level(logging.INFO):
        result = _load(tmp_path, _deps(run))

    assert result is not None
    assert result.getvalue() == b"partial-but-usable"
    assert "Обложка yt-dlp returncode=9: warning after download" in caplog.text
    assert "Обложка скачана: video-id_thumb.jpg" in caplog.text


@pytest.mark.parametrize(
    ("cache_hit", "rc_label", "missing_label"),
    [
        (False, "returncode", "Обложка"),
        (True, "rc", "Обложка (кэш)"),
    ],
)
def test_missing_file_preserves_log_variant_and_stderr_tail(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    cache_hit: bool,
    rc_label: str,
    missing_label: str,
) -> None:
    stderr = "  " + ("x" * 350) + "TERMINAL-ERROR\n"

    async def run(_command: list[str], **_kwargs: Any) -> Any:
        return SimpleNamespace(returncode=7, stderr=stderr)

    with caplog.at_level(logging.INFO):
        result = _load(tmp_path, _deps(run), cache_hit=cache_hit)

    assert result is None
    assert f"{missing_label} yt-dlp {rc_label}=7:" in caplog.text
    assert "TERMINAL-ERROR" in caplog.text
    assert ("x" * 350) not in caplog.text
    assert f"{missing_label}: файл не найден после yt-dlp (rc=7)" in caplog.text


def test_missing_file_accepts_none_stderr(tmp_path: Path) -> None:
    async def run(_command: list[str], **_kwargs: Any) -> Any:
        return SimpleNamespace(returncode=1, stderr=None)

    assert _load(tmp_path, _deps(run)) is None


def test_zero_rc_without_file_has_only_missing_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run(_command: list[str], **_kwargs: Any) -> Any:
        return SimpleNamespace(returncode=0, stderr="")

    with caplog.at_level(logging.INFO):
        result = _load(tmp_path, _deps(run))

    assert result is None
    assert "Обложка: файл не найден после yt-dlp (rc=0)" in caplog.text
    assert "yt-dlp returncode" not in caplog.text


@pytest.mark.parametrize(
    ("cache_hit", "expected_log"),
    [
        (False, "Обложка не скачана: network down"),
        (True, "Обложка (кэш) не скачана: network down"),
    ],
)
def test_process_failure_is_nonfatal(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    cache_hit: bool,
    expected_log: str,
) -> None:
    async def run(_command: list[str], **_kwargs: Any) -> Any:
        raise RuntimeError("network down")

    with caplog.at_level(logging.INFO):
        result = _load(tmp_path, _deps(run), cache_hit=cache_hit)

    assert result is None
    assert expected_log in caplog.text


def test_prepare_failure_is_nonfatal_and_does_not_download(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    (tmp_path / "video-id_thumb.jpg").write_bytes(b"broken")

    async def unexpected_run(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("не должно быть второй попытки")

    def prepare(_path: Path) -> BytesIO:
        raise RuntimeError("broken image")

    with caplog.at_level(logging.INFO):
        result = _load(tmp_path, _deps(unexpected_run, prepare))

    assert result is None
    assert "Обложка не скачана: broken image" in caplog.text


def test_prepare_none_does_not_trigger_download(tmp_path: Path) -> None:
    (tmp_path / "video-id_thumb.jpg").write_bytes(b"not-jpeg")

    async def unexpected_run(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("prepare_thumbnail сам решил пропустить обложку")

    result = _load(tmp_path, _deps(unexpected_run, lambda _path: None))

    assert result is None


def test_glob_failure_is_nonfatal(caplog: pytest.LogCaptureFixture) -> None:
    broken_dir = Mock()
    broken_dir.glob.side_effect = OSError("NTFS unavailable")

    async def unexpected_run(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("процесс не должен запускаться")

    with caplog.at_level(logging.INFO):
        result = asyncio.run(
            load_thumbnail(
                media_id="video-id",
                url=URL,
                thumbs_dir=broken_dir,
                deps=_deps(unexpected_run),
            )
        )

    assert result is None
    assert "Обложка не скачана: NTFS unavailable" in caplog.text


def test_default_dependencies_prepare_existing_file_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import utils

    path = tmp_path / "video-id_thumb.jpg"
    path.write_bytes(b"default-image")

    def prepare(value: Path) -> BytesIO:
        assert value == path
        return BytesIO(b"default-prepared")

    monkeypatch.setattr(utils, "prepare_thumbnail", prepare)

    result = asyncio.run(
        load_thumbnail(
            media_id="video-id",
            url=URL,
            thumbs_dir=tmp_path,
        )
    )

    assert result is not None
    assert result.getvalue() == b"default-prepared"


def test_main_pipeline_wires_fresh_and_cache_hit_to_shared_stage() -> None:
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert src.count("await load_thumbnail(") == 2
    assert "cache_hit=True" in src
    assert "cache_hit=False" in src
    assert "prepare_thumbnail" not in src
