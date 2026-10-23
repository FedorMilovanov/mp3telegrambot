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

from core import runtime_status


def _limit(env_name: str, default: int = 1) -> int:
    try:
        val = int(os.getenv(env_name, str(default)).strip() or default)
    except ValueError:
        val = default
    return max(1, val)


class _TrackedSemaphore(asyncio.Semaphore):
    """Семафор, который сообщает о занятости в ``runtime_status``.

    Счётчики для ``/healthz`` и ``/metrics`` ведутся здесь, а не в двадцати
    местах вызова: все тяжёлые шаги берут слот через ``async with``, значит
    ``acquire``/``release`` — единственная точка, которую нельзя обойти, не
    заметив этого. Наследование (а не обёртка) сохраняет поведение: очередь,
    отмена и ``wait_for`` работают ровно так же, как у обычного семафора.
    """

    async def acquire(self) -> bool:
        acquired = await super().acquire()
        if acquired:
            runtime_status.record_job_started()
        return acquired

    def release(self) -> None:
        super().release()
        runtime_status.record_job_finished()


class _Scheduler:
    """Ленивая инициализация семафоров: создаются при первом обращении уже
    внутри работающего event loop, чтобы не привязаться к чужому циклу."""

    def __init__(self) -> None:
        self._gpu: asyncio.Semaphore | None = None
        self._whisper: asyncio.Semaphore | None = None

    @property
    def gpu_render(self) -> asyncio.Semaphore:
        if self._gpu is None:
            self._gpu = _TrackedSemaphore(_limit("GPU_RENDER_CONCURRENCY", 1))
        return self._gpu

    @property
    def whisper(self) -> asyncio.Semaphore:
        if self._whisper is None:
            self._whisper = _TrackedSemaphore(_limit("WHISPER_CONCURRENCY", 1))
        return self._whisper

    def utilization(self) -> dict[str, int]:
        """Сколько слотов занято прямо сейчас — для ``/healthz`` и ``/metrics``.

        ``asyncio.Semaphore._value`` — приватное поле, но единственный способ
        прочитать занятость без переписывания всех мест, где семафор берётся
        (`async with scheduler.gpu_render`). Чтение согласовано: в event loop
        значение меняется только между await'ами, а вызов идёт из того же
        процесса. Если поле исчезнет в новой версии Python — вернётся 0, и
        метрика simply станет нулевой, а не упадёт.
        """
        result: dict[str, int] = {}
        for name, semaphore, limit_env in (
            ("gpu", self._gpu, "GPU_RENDER_CONCURRENCY"),
            ("whisper", self._whisper, "WHISPER_CONCURRENCY"),
        ):
            limit = _limit(limit_env, 1)
            value = getattr(semaphore, "_value", None) if semaphore is not None else None
            in_use = 0 if value is None else max(0, limit - int(value))
            result[f"{name}_slots_in_use"] = in_use
            result[f"{name}_slots_limit"] = limit
        return result


scheduler = _Scheduler()
