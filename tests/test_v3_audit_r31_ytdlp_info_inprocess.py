#!/usr/bin/env python3
"""AUDIT R31: in-process yt-dlp metadata first, owned subprocess fallback second.

The original live failure was ``python.exe Application Error 0xc0000142`` when
metadata was always fetched by spawning ``sys.executable -m yt_dlp``. The bot
therefore tries the yt-dlp Python API in-process first. If that completed with
an actual failure, the fallback remains available but must be owned by the
shared cancellation-safe subprocess lifecycle.
"""
from pathlib import Path

SRC = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")


def test_inprocess_info_helper_exists():
    assert "async def _ytdlp_info_inprocess(" in SRC
    assert "yt_dlp.YoutubeDL(opts)" in SRC
    assert "extract_info(url, download=False)" in SRC


def test_inprocess_tried_before_owned_subprocess_fallback():
    inproc = SRC.find("info_dict = await _ytdlp_info_inprocess(url, _info_timeout)")
    condition = SRC.find("if info_dict is None:", inproc)
    fallback = SRC.find("info_proc = await run_cancellable_process(", condition)
    fallback_command = SRC.find("info_cmd, timeout=_info_timeout, text=True", fallback)

    assert min(inproc, condition, fallback, fallback_command) != -1
    assert inproc < condition < fallback < fallback_command
    assert "subprocess.run(info_cmd" not in SRC


def test_inprocess_carries_cookies_and_proxy():
    """The in-process attempt must retain cookie/proxy/js-runtime parity."""
    seg = SRC.split("async def _ytdlp_info_inprocess(", 1)[1][:3000]
    assert "YTDLP_BASE_ARGS" in seg
    assert "parse_options" in seg
    assert 'opts["noplaylist"] = True' in seg
    assert "player_client" not in seg


def test_inprocess_returns_none_on_finished_failure_for_fallback():
    seg = SRC.split("async def _ytdlp_info_inprocess(", 1)[1][:3000]
    assert "return None" in seg
    assert "except asyncio.CancelledError:" in seg
    assert "raise" in seg
