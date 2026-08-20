#!/usr/bin/env python3
"""Cold-start warmup for the pinned bgutil script provider.

Upstream bgutil 1.3.1 checks ``generate_once.js --version`` with a hard
15-second timeout during real yt-dlp provider discovery. On a cold Windows
filesystem/Node module graph that check can exceed 15 seconds, causing the
provider to be marked unavailable and the subsequent GVS download to fail 403.

MP3Bot intentionally performs the *same* production script-version command once
before accepting work, but under repo-owned process-tree ownership and a wider
bounded startup timeout. This warms the exact module graph that yt-dlp will
check later and also turns a misleading runtime 403 into an explicit startup
failure if the provider cannot execute at all.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from services.async_process import run_cancellable_process
from services.youtube_po_token_runtime import (
    BGUTIL_EXPECTED_VERSION,
    YouTubePoTokenRuntime,
)

_WARMUP_TIMEOUT_SEC = 60.0


class YouTubePoTokenWarmupError(RuntimeError):
    """Raised when the exact production bgutil script cannot be warmed safely."""


@dataclass(frozen=True)
class YouTubePoTokenWarmup:
    provider_version: str
    elapsed_seconds: float

    def status_text(self) -> str:
        return f"warmup={self.elapsed_seconds:.1f}s"


def _stdout_version(process: subprocess.CompletedProcess[str]) -> str:
    lines = [
        line.strip()
        for line in str(process.stdout or "").splitlines()
        if line.strip()
    ]
    return lines[-1] if lines else ""


async def _warm_provider_async(
    runtime: YouTubePoTokenRuntime,
    *,
    timeout_seconds: float = _WARMUP_TIMEOUT_SEC,
) -> YouTubePoTokenWarmup:
    node = shutil.which("node")
    if not node:
        raise YouTubePoTokenWarmupError("Node.js не найден во время PO-token warmup")

    provider_home = Path(runtime.provider_home).resolve()
    script = provider_home / "build" / "generate_once.js"
    if not script.is_file():
        raise YouTubePoTokenWarmupError(
            f"production bgutil script отсутствует: {script}"
        )

    started = time.perf_counter()
    try:
        process = await run_cancellable_process(
            [node, str(script), "--version"],
            cwd=provider_home,
            timeout=float(timeout_seconds),
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise YouTubePoTokenWarmupError(
            "bgutil cold-start warmup не завершился за "
            f"{float(timeout_seconds):g}s; production provider не готов"
        ) from exc
    except (OSError, RuntimeError) as exc:
        raise YouTubePoTokenWarmupError(
            f"bgutil cold-start warmup не удалось запустить: {exc}"
        ) from exc

    elapsed = max(0.0, time.perf_counter() - started)
    version = _stdout_version(process)
    if process.returncode != 0:
        detail = str(process.stderr or process.stdout or "").strip()[-900:]
        suffix = f" Детали: {detail}" if detail else ""
        raise YouTubePoTokenWarmupError(
            f"bgutil cold-start warmup завершился rc={process.returncode}.{suffix}"
        )
    if version != BGUTIL_EXPECTED_VERSION:
        raise YouTubePoTokenWarmupError(
            "bgutil cold-start warmup вернул неожиданную версию: "
            f"expected={BGUTIL_EXPECTED_VERSION} actual={version or 'empty'}"
        )

    return YouTubePoTokenWarmup(
        provider_version=version,
        elapsed_seconds=elapsed,
    )


def warm_youtube_po_token_provider(
    runtime: YouTubePoTokenRuntime,
    *,
    timeout_seconds: float = _WARMUP_TIMEOUT_SEC,
) -> YouTubePoTokenWarmup:
    """Warm the exact bgutil script before the bot creates its main event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise YouTubePoTokenWarmupError(
            "PO-token warmup must run before the production event loop starts"
        )
    return asyncio.run(
        _warm_provider_async(runtime, timeout_seconds=timeout_seconds)
    )


__all__ = [
    "YouTubePoTokenWarmup",
    "YouTubePoTokenWarmupError",
    "warm_youtube_po_token_provider",
]
