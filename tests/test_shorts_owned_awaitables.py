from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from services import shorts_video


OWNED_PUBLIC_FUNCTIONS = (
    "download_video_for_shorts",
    "render_short_clip",
    "postprocess_short",
    "transcribe_short_clip",
    "create_short_title_poster",
    "create_short_snapshot",
)


def test_active_shorts_functions_have_one_public_wrapper() -> None:
    source = Path(shorts_video.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    for name in OWNED_PUBLIC_FUNCTIONS:
        assert names.count(name) == 1
        assert names.count(f"_unowned_{name}") == 1


def test_public_wrappers_use_owned_coroutine_contract() -> None:
    source = Path(shorts_video.__file__).read_text(encoding="utf-8")
    assert "from services.async_worker import await_owned_coroutine" in source

    for name in OWNED_PUBLIC_FUNCTIONS:
        public_source = ast.get_source_segment(
            source,
            next(
                node
                for node in ast.parse(source).body
                if isinstance(node, ast.AsyncFunctionDef) and node.name == name
            ),
        )
        assert public_source is not None
        assert "await await_owned_coroutine(" in public_source
        assert f"_unowned_{name}(" in public_source


def test_legacy_burn_delegates_to_transactional_owner() -> None:
    source = Path(shorts_video.__file__).read_text(encoding="utf-8")
    public_source = ast.get_source_segment(
        source,
        next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "burn_subtitles_into_short"
        ),
    )
    assert public_source is not None
    assert "services.shorts_subtitle_burn" in public_source
    assert "_unowned_burn_subtitles_into_short" not in public_source


@pytest.mark.asyncio
async def test_transcribe_wrapper_forwards_arguments(monkeypatch, tmp_path: Path) -> None:
    observed = {}

    async def fake(video_path: Path, ai_data=None):
        observed["video_path"] = video_path
        observed["ai_data"] = ai_data
        return [{"text": "готово"}]

    monkeypatch.setattr(shorts_video, "_unowned_transcribe_short_clip", fake)
    video = tmp_path / "clip.mp4"
    result = await shorts_video.transcribe_short_clip(video, ai_data={"x": 1})

    assert result == [{"text": "готово"}]
    assert observed == {"video_path": video, "ai_data": {"x": 1}}


@pytest.mark.asyncio
async def test_render_wrapper_does_not_cancel_inner_work(monkeypatch, tmp_path: Path) -> None:
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

    monkeypatch.setattr(shorts_video, "_unowned_render_short_clip", fake)
    task = asyncio.create_task(
        shorts_video.render_short_clip(
            tmp_path / "source.mp4",
            tmp_path / "output.mp4",
            0,
            20,
        )
    )
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)

    assert not task.done()
    assert inner_cancelled is False
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert inner_cancelled is False
