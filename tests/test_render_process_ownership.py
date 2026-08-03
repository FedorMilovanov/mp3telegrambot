from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from services import render_clips_montage


SHORTS_PATH = Path("services/shorts_video.py")
CLIPS_PATH = Path("services/render_clips_montage.py")
SHORTS_FUNCTIONS = (
    "_unowned_download_video_for_shorts",
    "_unowned_render_short_clip",
    "_unowned_short_transform",
    "_unowned_transcribe_short_clip",
    "_unowned_burn_subtitles_into_short",
    "_unowned_create_short_title_poster",
    "_unowned_create_short_snapshot",
)
CLIP_FUNCTIONS = (
    "render_clip",
    "create_clip_snapshot",
    "render_montage_short",
)


def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.AsyncFunctionDef) and item.name == name
    )
    return ast.get_source_segment(source, node) or ""


def test_shorts_render_surface_uses_owned_processes_and_threads() -> None:
    source = SHORTS_PATH.read_text(encoding="utf-8")
    selected = "\n".join(
        _function_source(SHORTS_PATH, name) for name in SHORTS_FUNCTIONS
    )

    assert "from services.async_process import run_cancellable_process" in source
    assert selected.count("await run_cancellable_process(") == 7
    assert selected.count("asyncio.to_thread(_run_whisper)") == 1
    assert selected.count("asyncio.to_thread(_draw_poster)") == 1
    assert selected.count("asyncio.to_thread(_get_video_encoder)") == 3
    assert selected.count("asyncio.to_thread(") == 5
    assert "await_owned_coroutine" in selected
    assert "run_in_executor" not in selected
    assert "subprocess.run(" not in selected


def test_clips_and_montage_use_owned_processes() -> None:
    source = CLIPS_PATH.read_text(encoding="utf-8")
    selected = "\n".join(
        _function_source(CLIPS_PATH, name) for name in CLIP_FUNCTIONS
    )

    assert "from services.async_process import run_cancellable_process" in source
    assert selected.count("await run_cancellable_process(") == 4
    assert "run_in_executor" not in selected
    assert "subprocess.run(" not in selected


def test_gpu_semaphores_still_wrap_long_render_owners() -> None:
    shorts_render = _function_source(SHORTS_PATH, "_unowned_render_short_clip")
    shorts_transform = _function_source(SHORTS_PATH, "_unowned_short_transform")
    shorts_burn = _function_source(SHORTS_PATH, "_unowned_burn_subtitles_into_short")
    clip_render = _function_source(CLIPS_PATH, "render_clip")
    montage = _function_source(CLIPS_PATH, "render_montage_short")

    for function in (
        shorts_render,
        shorts_transform,
        shorts_burn,
        clip_render,
        montage,
    ):
        assert "async with _sched.gpu_render:" in function
        assert function.index("async with _sched.gpu_render:") < function.index(
            "await run_cancellable_process("
        )


@pytest.mark.asyncio
async def test_render_clip_calls_tree_owner_with_existing_command_contract(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.mp4"
    output_path = tmp_path / "clip.mp4"
    source_path.write_bytes(b"source")
    calls: list[tuple[list[str], dict[str, object]]] = []

    async def no_snap(path, target, search_window=5.0):
        return target

    async def fake_owner(command, **kwargs):
        calls.append((list(command), dict(kwargs)))
        Path(command[-1]).write_bytes(b"rendered")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(render_clips_montage.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(render_clips_montage, "_find_silence_end", no_snap)
    monkeypatch.setattr(
        render_clips_montage,
        "_get_video_encoder",
        lambda: ("libx264", ["-crf", "23"], ["-preset", "veryfast"]),
    )
    monkeypatch.setattr(
        render_clips_montage,
        "run_cancellable_process",
        fake_owner,
    )

    result = await render_clips_montage.render_clip(
        source_path,
        output_path,
        10,
        40,
    )

    assert result is True
    command, kwargs = calls[0]
    assert command[0] == "ffmpeg"
    assert command[-1] == str(output_path)
    assert "-t" in command and command[command.index("-t") + 1] == "30"
    assert kwargs == {"timeout": 900, "text": True}
