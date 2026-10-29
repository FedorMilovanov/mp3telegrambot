#!/usr/bin/env python3
"""AUDIT R31: in-process yt-dlp metadata first, owned subprocess fallback second.

The original live failure was ``python.exe Application Error 0xc0000142`` when
metadata was always fetched by spawning ``sys.executable -m yt_dlp``. The bot
therefore tries the yt-dlp Python API in-process first. If that completed with
an actual failure, the fallback remains available but must be owned by the
shared cancellation-safe subprocess lifecycle.
"""

import asyncio
from pathlib import Path
import sys
from collections.abc import Awaitable
from types import SimpleNamespace, TracebackType
from typing import Any, Literal

import pytest

from pipelines import main_pipeline
from services import ffmpeg

SRC = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")


def test_inprocess_info_helper_exists() -> None:
    assert "async def _ytdlp_info_inprocess(" in SRC
    assert "yt_dlp.YoutubeDL(opts)" in SRC
    assert "extract_info(url, download=False)" in SRC


def test_inprocess_tried_before_owned_subprocess_fallback() -> None:
    inproc = SRC.find("info_dict = await _ytdlp_info_inprocess(url, _info_timeout)")
    condition = SRC.find("if info_dict is None:", inproc)
    fallback = SRC.find("info_proc = await run_cancellable_process(", condition)
    fallback_command = SRC.find("info_cmd, timeout=_info_timeout, text=True", fallback)

    assert min(inproc, condition, fallback, fallback_command) != -1
    assert inproc < condition < fallback < fallback_command
    assert "subprocess.run(info_cmd" not in SRC


def test_inprocess_carries_cookies_and_proxy() -> None:
    """The in-process attempt must retain cookie/proxy/js-runtime parity."""
    seg = SRC.split("async def _ytdlp_info_inprocess(", 1)[1][:3000]
    assert "YTDLP_BASE_ARGS" in seg
    assert "parse_options" in seg
    assert 'opts["noplaylist"] = True' in seg
    assert "player_client" not in seg


def test_inprocess_returns_none_on_finished_failure_for_fallback() -> None:
    seg = SRC.split("async def _ytdlp_info_inprocess(", 1)[1][:3000]
    assert "return None" in seg
    assert "except asyncio.CancelledError:" in seg
    assert "raise" in seg


def _run_info(url: str = "https://youtu.be/video", timeout: int = 7) -> dict[str, Any] | None:
    return asyncio.run(main_pipeline._ytdlp_info_inprocess(url, timeout))


def _install_ydl(monkeypatch: pytest.MonkeyPatch, info: dict[str, Any], captured: dict[str, Any]) -> None:
    import yt_dlp

    class _YoutubeDL:
        def __init__(self, opts: dict[str, Any]) -> None:
            captured["opts"] = opts

        def __enter__(self) -> "_YoutubeDL":
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> Literal[False]:
            return False

        def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
            captured["url"] = url
            captured["download"] = download
            return info

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _YoutubeDL)


def test_inprocess_import_failure_allows_subprocess_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "yt_dlp", None)
    assert _run_info() is None


def test_inprocess_parse_failure_allows_subprocess_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_dlp

    monkeypatch.setattr(ffmpeg, "YTDLP_BASE_ARGS", ["yt-dlp", "--quiet"])

    def fail_parse(_flags: list[str]) -> None:
        raise RuntimeError("bad options")

    monkeypatch.setattr(yt_dlp, "parse_options", fail_parse)
    assert _run_info() is None


def test_inprocess_rejects_missing_or_non_dict_options(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_dlp

    # No yt-dlp token: the argument-normalization loop runs to exhaustion.
    monkeypatch.setattr(ffmpeg, "YTDLP_BASE_ARGS", ["--quiet"])
    monkeypatch.setattr(
        yt_dlp,
        "parse_options",
        lambda _flags: SimpleNamespace(ydl_opts=None),
    )
    assert _run_info() is None


def test_inprocess_success_preserves_options_and_reduces_playlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yt_dlp

    captured: dict[str, Any] = {}
    monkeypatch.setattr(ffmpeg, "YTDLP_BASE_ARGS", ["yt-dlp", "--quiet"])
    monkeypatch.setattr(
        yt_dlp,
        "parse_options",
        lambda _flags: SimpleNamespace(ydl_opts={}),
    )
    _install_ydl(monkeypatch, {"entries": [{}, {"id": "first"}]}, captured)

    async def owned(awaitable: Awaitable[Any], timeout: float) -> tuple[Any, bool]:
        captured["timeout"] = timeout
        return await awaitable, False

    monkeypatch.setattr(main_pipeline, "await_owned_with_soft_timeout", owned)
    assert _run_info() == {"id": "first"}
    assert captured["opts"]["socket_timeout"] == 30
    assert captured["opts"]["noplaylist"] is True
    assert captured["url"] == "https://youtu.be/video"
    assert captured["download"] is False
    assert captured["timeout"] == 7


def test_inprocess_tuple_options_and_late_result_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yt_dlp

    captured: dict[str, Any] = {}
    monkeypatch.setattr(ffmpeg, "YTDLP_BASE_ARGS", ["yt-dlp"])
    monkeypatch.setattr(
        yt_dlp,
        "parse_options",
        lambda _flags: (None, {"socket_timeout": 12}),
    )
    _install_ydl(monkeypatch, {"id": "late"}, captured)

    async def owned(awaitable: Awaitable[Any], timeout: float) -> tuple[Any, bool]:
        return await awaitable, True

    monkeypatch.setattr(main_pipeline, "await_owned_with_soft_timeout", owned)
    assert _run_info() == {"id": "late"}
    assert captured["opts"]["socket_timeout"] == 12


def test_inprocess_finished_failure_returns_none_after_owned_await(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_dlp

    captured: dict[str, Any] = {}
    monkeypatch.setattr(ffmpeg, "YTDLP_BASE_ARGS", ["yt-dlp"])
    monkeypatch.setattr(
        yt_dlp,
        "parse_options",
        lambda _flags: SimpleNamespace(ydl_opts={}),
    )
    _install_ydl(monkeypatch, {"id": "unused"}, captured)

    async def owned(awaitable: Awaitable[Any], timeout: float) -> tuple[Any, bool]:
        await awaitable
        raise RuntimeError("extract failed")

    monkeypatch.setattr(main_pipeline, "await_owned_with_soft_timeout", owned)
    assert _run_info() is None


def test_inprocess_cancellation_is_not_converted_to_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import yt_dlp

    captured: dict[str, Any] = {}
    monkeypatch.setattr(ffmpeg, "YTDLP_BASE_ARGS", ["yt-dlp"])
    monkeypatch.setattr(
        yt_dlp,
        "parse_options",
        lambda _flags: SimpleNamespace(ydl_opts={}),
    )
    _install_ydl(monkeypatch, {"id": "unused"}, captured)

    async def owned(awaitable: Awaitable[Any], timeout: float) -> tuple[Any, bool]:
        await awaitable
        raise asyncio.CancelledError

    monkeypatch.setattr(main_pipeline, "await_owned_with_soft_timeout", owned)
    with pytest.raises(asyncio.CancelledError):
        _run_info()
