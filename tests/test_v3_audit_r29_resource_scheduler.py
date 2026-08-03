#!/usr/bin/env python3
"""AUDIT R29: три видео обрабатывались параллельно и дрались за одну
видеокарту (h264_nvenc упирался в 15-мин таймаут) и за CPU-Whisper.
Фикс: глобальные семафоры — 1 GPU-рендер и 1 Whisper одновременно по
умолчанию, тяжёлые шаги идут по очереди, а не в гонке.
"""
import asyncio
import os
from pathlib import Path

import pytest

from core.resource_scheduler import _limit, scheduler


def test_default_limits_are_one():
    assert _limit("GPU_RENDER_CONCURRENCY", 1) == 1
    assert _limit("WHISPER_CONCURRENCY", 1) == 1


def test_limit_reads_env(monkeypatch):
    monkeypatch.setenv("GPU_RENDER_CONCURRENCY", "3")
    assert _limit("GPU_RENDER_CONCURRENCY", 1) == 3
    monkeypatch.setenv("GPU_RENDER_CONCURRENCY", "bad")
    assert _limit("GPU_RENDER_CONCURRENCY", 1) == 1  # мусор -> дефолт
    monkeypatch.setenv("GPU_RENDER_CONCURRENCY", "0")
    assert _limit("GPU_RENDER_CONCURRENCY", 1) == 1  # минимум 1


@pytest.mark.asyncio
async def test_gpu_semaphore_serializes():
    """Второй захват gpu_render должен ждать освобождения первого (лимит 1)."""
    sem = scheduler.gpu_render
    # осушим до 0, если дефолтный лимит 1
    assert sem._value in (0, 1)
    order = []

    async def worker(tag):
        async with scheduler.gpu_render:
            order.append(f"start:{tag}")
            await asyncio.sleep(0.02)
            order.append(f"end:{tag}")

    await asyncio.gather(worker("a"), worker("b"))
    # при лимите 1 они не перекрываются: end:a до start:b (или наоборот)
    assert order in (
        ["start:a", "end:a", "start:b", "end:b"],
        ["start:b", "end:b", "start:a", "end:a"],
    )


def test_gpu_render_wrapped_in_heavy_renderers():
    for path in ("services/shorts_video_impl.py", "services/render_clips_montage.py"):
        src = Path(path).read_text(encoding="utf-8")
        assert "from core.resource_scheduler import scheduler" in src, f"{path} не импортит scheduler"
        assert ".gpu_render" in src, f"{path} не сериализует GPU-рендер"


def test_whisper_wrapped():
    src = Path("services/shorts_video_impl.py").read_text(encoding="utf-8")
    assert ".whisper" in src
