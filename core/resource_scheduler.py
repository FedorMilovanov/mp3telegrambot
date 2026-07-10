"""AUDIT R29 — process-wide resource scheduler.

Живой лог: три видео обрабатывались параллельно и одновременно дрались за
одну видеокарту (h264_nvenc) и за CPU-Whisper large-v3. Результат: один
ffmpeg-рендер упёрся в 15-минутный таймаут, Whisper тянулся по 3-8 минут
вместо одной, а лавина одновременных процессов вносила вклад в
исчерпание ресурсов сессии Windows.

Лечение: глобальные семафоры. Тяжёлые шаги всех параллельных видео идут
ПО ОЧЕРЕДИ (по умолчанию 1 GPU-рендер и 1 Whisper одновременно), а не
дерутся. Все видео всё равно обрабатываются — просто без гонки за железо.

Лимиты настраиваются через env:
  GPU_RENDER_CONCURRENCY   (по умолчанию 1)
  WHISPER_CONCURRENCY      (по умолчанию 1)
"""

from __future__ import annotations

import asyncio
import os


def _limit(env_name: str, default: int = 1) -> int:
    try:
        val = int(os.getenv(env_name, str(default)).strip() or default)
    except ValueError:
        val = default
    return max(1, val)


class _Scheduler:
    """Ленивая инициализация семафоров: создаются при первом обращении уже
    внутри работающего event loop, чтобы не привязаться к чужому циклу."""

    def __init__(self) -> None:
        self._gpu: asyncio.Semaphore | None = None
        self._whisper: asyncio.Semaphore | None = None

    @property
    def gpu_render(self) -> asyncio.Semaphore:
        if self._gpu is None:
            self._gpu = asyncio.Semaphore(_limit("GPU_RENDER_CONCURRENCY", 1))
        return self._gpu

    @property
    def whisper(self) -> asyncio.Semaphore:
        if self._whisper is None:
            self._whisper = asyncio.Semaphore(_limit("WHISPER_CONCURRENCY", 1))
        return self._whisper


scheduler = _Scheduler()
