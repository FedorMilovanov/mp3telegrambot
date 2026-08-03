from __future__ import annotations

import inspect

from services import highlights_quality


def test_audio_probe_uses_owned_async_processes() -> None:
    source = inspect.getsource(highlights_quality._build_audio_probe)
    assert "run_cancellable_process" in source
    assert "run_in_executor" not in source
    assert "subprocess.run" not in source


def test_verified_render_keeps_process_owned_through_cancellation() -> None:
    source = inspect.getsource(highlights_quality.render_verified_highlights)
    assert "run_cancellable_process" in source
    assert "run_in_executor" not in source
    assert "except asyncio.CancelledError" in source
    assert "output_path.unlink(missing_ok=True)" in source
    assert "async with resource_scheduler.gpu_render" in source
