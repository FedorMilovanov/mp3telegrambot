from __future__ import annotations

import ast
import asyncio
import subprocess
import threading
import time
from pathlib import Path

import pytest

from services import render_clips_montage


TARGETS = {
    Path("services/shorts_video.py"): {
        "_unowned_render_short_clip",
        "_unowned_short_transform",
        "_unowned_burn_subtitles_into_short",
    },
    Path("services/render_clips_montage.py"): {
        "render_clip",
        "render_montage_short",
    },
    Path("services/eng_subtitles.py"): {"_burn_subtitles"},
    Path("services/shorts_subtitle_burn.py"): {"burn_subtitles_into_short"},
}


def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.AsyncFunctionDef) and item.name == name
    )
    return ast.get_source_segment(source, node) or ""


def test_all_async_encoder_call_sites_use_owned_threads() -> None:
    total = 0
    for path, names in TARGETS.items():
        source = path.read_text(encoding="utf-8")
        assert "from services.async_worker import await_owned_coroutine" in source
        selected = "\n".join(_function_source(path, name) for name in names)
        assert selected.count("await await_owned_coroutine(") == len(names)
        assert selected.count("asyncio.to_thread(_get_video_encoder)") == len(names)
        assert "= _get_video_encoder()" not in selected
        total += len(names)

    assert total == 7


def test_whisper_owned_worker_format_is_readable() -> None:
    source = Path("services/shorts_video.py").read_text(encoding="utf-8")
    assert (
        "            segments, audio_duration, detected_lang, lang_prob = await await_owned_coroutine(\n"
        "                asyncio.to_thread(_run_whisper)\n"
        "            )"
    ) in source


@pytest.mark.asyncio
async def test_slow_encoder_probe_does_not_block_event_loop(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.mp4"
    output_path = tmp_path / "clip.mp4"
    source_path.write_bytes(b"source")

    started = threading.Event()
    release = threading.Event()

    def slow_encoder():
        started.set()
        release.wait(timeout=1.0)
        return "libx264", ["-crf", "23"], ["-preset", "veryfast"]

    async def no_snap(path, target, search_window=5.0):
        return target

    async def fake_owner(command, **kwargs):
        Path(command[-1]).write_bytes(b"rendered")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(render_clips_montage.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(render_clips_montage, "_find_silence_end", no_snap)
    monkeypatch.setattr(render_clips_montage, "_get_video_encoder", slow_encoder)
    monkeypatch.setattr(
        render_clips_montage,
        "run_cancellable_process",
        fake_owner,
    )

    render_task = asyncio.create_task(
        render_clips_montage.render_clip(
            source_path,
            output_path,
            10,
            40,
        )
    )

    deadline = time.monotonic() + 0.5
    while not started.is_set() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert started.is_set(), "encoder probe did not start"

    heartbeat_start = time.monotonic()
    await asyncio.sleep(0.05)
    heartbeat_elapsed = time.monotonic() - heartbeat_start
    assert heartbeat_elapsed < 0.6
    assert render_task.done() is False

    release.set()
    assert await asyncio.wait_for(render_task, timeout=2.0) is True
    assert output_path.read_bytes() == b"rendered"
