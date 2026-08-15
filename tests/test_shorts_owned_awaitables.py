from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from services import shorts_video


PUBLIC_OWNED = (
    "download_video_for_shorts",
    "render_short_clip",
    "postprocess_short",
    "transcribe_short_clip",
    "create_short_title_poster",
    "create_short_snapshot",
    "burn_subtitles_into_short",
)


def _public_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.AsyncFunctionDef) and item.name == name
    )
    value = ast.get_source_segment(source, node)
    assert value is not None
    return value


def test_active_shorts_functions_have_one_public_owner() -> None:
    source = Path(shorts_video.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    for public_name in PUBLIC_OWNED:
        assert names.count(public_name) == 1

    assert names.count("_owned_render_short_clip") == 1
    assert names.count("_owned_short_transform") == 1
    assert names.count("_owned_optional_output") == 1
    assert "_unowned_render_short_clip" not in names
    assert "_unowned_transcribe_short_clip" not in names


def test_public_long_running_wrappers_use_owned_coroutine_contract() -> None:
    source = Path(shorts_video.__file__).read_text(encoding="utf-8")

    expected_targets = {
        "download_video_for_shorts": "_LEGACY_DOWNLOAD_VIDEO(",
        "transcribe_short_clip": "_LEGACY_TRANSCRIBE_SHORT_CLIP(",
        "render_short_clip": "_owned_render_short_clip(",
        "postprocess_short": "_owned_short_transform(",
        "create_short_title_poster": "_owned_optional_output(",
        "create_short_snapshot": "_owned_optional_output(",
    }
    for public_name, target in expected_targets.items():
        public_source = _public_source(source, public_name)
        assert "_impl.await_owned_coroutine(" in public_source
        assert target in public_source


def test_legacy_burn_delegates_to_transactional_owner() -> None:
    source = Path(shorts_video.__file__).read_text(encoding="utf-8")
    public_source = _public_source(source, "burn_subtitles_into_short")
    assert "services.shorts_subtitle_burn" in public_source
    assert "owned_burn(" in public_source
    assert "_unowned_burn_subtitles_into_short" not in public_source


@pytest.mark.asyncio
async def test_transcribe_wrapper_forwards_arguments(monkeypatch, tmp_path: Path) -> None:
    observed = {}

    async def fake(video_path: Path, ai_data=None):
        observed["video_path"] = video_path
        observed["ai_data"] = ai_data
        return [{"text": "готово"}]

    monkeypatch.setitem(
        shorts_video.transcribe_short_clip.__globals__,
        "_LEGACY_TRANSCRIBE_SHORT_CLIP",
        fake,
    )
    video = tmp_path / "clip.mp4"
    result = await shorts_video.transcribe_short_clip(video, ai_data={"x": 1})

    assert result == [{"text": "готово"}]
    assert observed == {"video_path": video, "ai_data": {"x": 1}}


@pytest.mark.asyncio
async def test_render_wrapper_survives_repeated_outer_cancellation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    inner_cancelled = False

    async def fake(*args, **kwargs):
        nonlocal inner_cancelled
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            inner_cancelled = True
            raise
        return True

    monkeypatch.setitem(
        shorts_video.render_short_clip.__globals__,
        "_owned_render_short_clip",
        fake,
    )
    task = asyncio.create_task(
        shorts_video.render_short_clip(
            tmp_path / "source.mp4",
            tmp_path / "output.mp4",
            0,
            20,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)

    assert not task.done()
    assert inner_cancelled is False
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1.0)
    assert inner_cancelled is False
