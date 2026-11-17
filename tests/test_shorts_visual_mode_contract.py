"""Generic renderer entrypoints reject unspecified blur before touching files."""

from __future__ import annotations
from pathlib import Path
import pytest
from services import highlights_quality, render_clips_montage, shorts_video, shorts_video_impl
from services.shorts_visual_mode_contract import has_explicit_visual_mode


@pytest.mark.parametrize("mode", [None, "auto", "", "blur", "crop", "FULL_FRAME_VERTICAL"])
def test_unknown_visual_mode_never_becomes_blur(mode: str | None) -> None:
    assert has_explicit_visual_mode(mode) is False


@pytest.mark.parametrize("mode", ["crop_zoom", "full_frame_vertical"])
def test_valid_modes_require_explicit_selection(mode: str) -> None:
    assert has_explicit_visual_mode(mode) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "auto", "blur"])
async def test_owned_renderer_rejects_implicit_mode_without_deleting_old_output(
    tmp_path: Path, mode: str | None
) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "existing.mp4"
    source.write_bytes(b"source")
    output.write_bytes(b"important old output")
    assert await shorts_video.render_short_clip(source, output, 0, 20, visual_mode=mode) is False
    assert output.read_bytes() == b"important old output"
    assert source.read_bytes() == b"source"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "auto", "blur"])
async def test_legacy_renderer_rejects_implicit_mode_without_deleting_old_output(
    tmp_path: Path, mode: str | None
) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "existing.mp4"
    source.write_bytes(b"source")
    output.write_bytes(b"important old output")
    assert await shorts_video_impl.render_short_clip(source, output, 0, 20, visual_mode=mode) is False
    assert output.read_bytes() == b"important old output"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "auto", "blur"])
async def test_montage_rejects_implicit_mode_without_deleting_old_output(tmp_path: Path, mode: str | None) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "existing.mp4"
    source.write_bytes(b"source")
    output.write_bytes(b"important old output")
    fragments = [{"start_seconds": 0, "end_seconds": 20}]
    assert await render_clips_montage.render_montage_short(source, output, fragments, visual_mode=mode) is False
    assert output.read_bytes() == b"important old output"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "auto", "blur"])
async def test_verified_highlights_rejects_implicit_mode_without_deleting_old_output(
    tmp_path: Path, mode: str | None
) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "existing.mp4"
    source.write_bytes(b"source")
    output.write_bytes(b"important old output")
    fragments = [{"start_seconds": 0, "end_seconds": 20}]
    assert await highlights_quality.render_verified_highlights(
        source, output, fragments, visual_mode=mode
    ) is False
    assert output.read_bytes() == b"important old output"
