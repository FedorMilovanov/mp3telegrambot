"""Best-effort resource teardown for ``process_single_video``.

This is deliberately limited to the historical ``finally`` block.  LiveDub
result delivery and its error recovery remain in the orchestrator.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


async def cleanup_pipeline_resources(
    *,
    live_dub_task: asyncio.Task[Any] | None,
    workdir: Path | None,
    used_audio_part: Any,
    used_client: Any,
    thumbnail: Any,
) -> None:
    """Release resources in the original order and with the original failures.

    Task cancellation and Gemini delete errors are contained.  As before,
    unexpected failures from ``cancel()``, workdir inspection/removal, or
    thumbnail closing propagate and therefore stop the remaining teardown.
    """
    if live_dub_task is not None and not live_dub_task.done():
        live_dub_task.cancel()
        try:
            await live_dub_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("LiveDub task failed during final cancellation cleanup", exc_info=True)

    if workdir is not None:

        def _remove_workdir() -> None:
            if workdir.exists():
                shutil.rmtree(workdir, ignore_errors=True)

        await asyncio.to_thread(_remove_workdir)

    # The Gemini audio part must outlive synopsis/Shorts/Clips and is deleted
    # only here, after all downstream users have finished.
    if used_audio_part and hasattr(used_audio_part, "name") and used_client:
        try:
            await used_client.aio.files.delete(name=used_audio_part.name)
            logger.info("Gemini Files: audio_part удалён")
        except Exception as delete_error:
            logger.warning("Gemini Files delete error: %s", delete_error)

    if thumbnail:
        thumbnail.close()
