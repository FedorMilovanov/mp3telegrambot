#!/usr/bin/env python3
"""Стадия «получить mp3»: скачивание, нормализация, сжатие под лимит отправки.

Зачем отдельный модуль: этот код существовал **двумя почти одинаковыми копиями**
в `pipelines/main_pipeline.py` — в обычном пути и в ветке повторной отправки из
кэша. Копии успели разойтись (разные таймауты, разные флаги yt-dlp, разные
тексты ошибок), а проверялись только живым ботом.

Правила, которые здесь зафиксированы:

* существующий файл переиспользуется (повторный запрос не качает заново);
* после скачивания mp3 нормализуется без потерь (mp3gain, если есть);
* файл больше лимита отправки пережимается в 64 kbps **атомарно**; если и после
  этого не влезает — это отдельная ошибка `AudioTooLarge`, а не «что-то упало»:
  вызывающий код обязан сказать пользователю про размер и не терять уже
  готовый дубляж.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Sequence

from services.async_process import run_cancellable_process
from services.ffmpeg import YTDLP_BASE_ARGS, normalize_mp3_lossless
from services.mp3_conversion import reencode_mp3_64k_atomic
from services.async_worker import await_owned_coroutine

logger = logging.getLogger(__name__)

#: Длительность, после которой сразу качаем 64 kbps: 128 на трёхчасовой
#: проповеди даёт файл, который Telegram не примет.
LONG_AUDIO_SEC = 3600

#: Сообщение для пользователя, когда заявленный размер не влезает даже в 64 kbps.
TOO_LARGE_MESSAGE = (
    "⚠️ Файл слишком большой ({size:.1f} МБ) даже после сжатия до 64 kbps.\n"
    "Текущий лимит отправки — {limit} МБ. Попробуйте более короткое видео."
)

SqueezeCallback = Callable[[float], Awaitable[None]]


class AudioError(RuntimeError):
    """Аудио получить не удалось (yt-dlp упал, файл не появился)."""


class AudioTooLarge(AudioError):
    """Файл больше лимита отправки даже после пережатия в 64 kbps.

    Отдельный тип нужен, чтобы вызывающий код мог отправить обещанный дубляж и
    дать понятное сообщение про размер, а не общее «❌ Ошибка».
    """

    def __init__(self, size_mb: float, limit_mb: int) -> None:
        self.size_mb = size_mb
        self.limit_mb = limit_mb
        super().__init__(TOO_LARGE_MESSAGE.format(size=size_mb, limit=limit_mb))


@dataclass(frozen=True)
class AudioResult:
    path: Path
    size_mb: float
    bitrate: str
    reused: bool


def audio_quality(duration: int) -> str:
    """128 kbps для коротких, 64 kbps для длинных (>1 часа)."""
    return "64K" if int(duration or 0) > LONG_AUDIO_SEC else "128K"


def bitrate_label(duration: int, path_name: str) -> str:
    """Каким битрейтом реально отдаётся файл — для подписи «🎵 128 kbps»."""
    return "64" if ("_64" in path_name or int(duration or 0) > LONG_AUDIO_SEC) else "128"


def find_existing(download_dir: Path, media_id: str) -> Path | None:
    """Уже скачанный файл для этого видео, если он есть.

    Ищем и `{id}.mp3`, и производные: прошлый прогон длинного видео мог
    оставить только пережатый `{id}_64.mp3` (оригинал удалён), и без этого
    повторный запрос качал бы всё заново. Сортировка — чтобы выбор между
    несколькими файлами был детерминированным.
    """
    exact = download_dir / f"{media_id}.mp3"
    if exact.exists():
        return exact
    candidates = sorted(download_dir.glob(f"{media_id}*.mp3"))
    return candidates[0] if candidates else None


def download_args(
    *,
    media_id: str,
    url: str,
    duration: int,
    download_dir: Path,
    sponsorblock_args: Sequence[str] = (),
    extra_args: Sequence[str] = (),
) -> list[str]:
    """Команда yt-dlp для mp3.

    ``extra_args`` существует потому, что два прежних места вызывали разные
    наборы флагов; расхождение сохранено явно, а не спрятано в двух копиях.
    """
    return (
        list(YTDLP_BASE_ARGS)
        + list(sponsorblock_args)
        + list(extra_args)
        + [
            "--extract-audio",
            "--audio-format",
            "mp3",
            "--audio-quality",
            audio_quality(duration),
            "--embed-metadata",
            "--embed-thumbnail",
            "--no-playlist",
            "--output",
            str(download_dir / f"{media_id}.%(ext)s"),
            url,
        ]
    )


async def prepare_audio(
    *,
    media_id: str,
    url: str,
    duration: int,
    download_dir: Path,
    max_size_mb: int,
    timeout: int = 600,
    sponsorblock_args: Sequence[str] = (),
    extra_args: Sequence[str] = (),
    log_prefix: str = "MP3",
    missing_message: str = "MP3 файл не найден",
    on_squeeze: SqueezeCallback | None = None,
) -> AudioResult:
    """Вернуть готовый к отправке mp3 или бросить `AudioError`/`AudioTooLarge`."""
    download_dir = Path(download_dir)

    existing = find_existing(download_dir, media_id)
    reused = existing is not None
    if existing is not None:
        logger.info("%s: реюз существующего %s", log_prefix, existing.name)
        path = existing
    else:
        path = download_dir / f"{media_id}.mp3"
        cmd = download_args(
            media_id=media_id,
            url=url,
            duration=duration,
            download_dir=download_dir,
            sponsorblock_args=sponsorblock_args,
            extra_args=extra_args,
        )
        proc = await run_cancellable_process(cmd, timeout=timeout, text=True)
        if proc.returncode != 0:
            raise AudioError((proc.stderr or "")[-500:] or f"yt-dlp rc={proc.returncode}")
        path = find_existing(download_dir, media_id) or path
        if path.exists():
            # mp3gain правит только заголовки: без перекодирования, обратимо.
            await await_owned_coroutine(asyncio.to_thread(normalize_mp3_lossless, path))
        else:
            raise AudioError(missing_message)

    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb <= max_size_mb:
        return AudioResult(
            path=path,
            size_mb=size_mb,
            bitrate=bitrate_label(duration, path.name),
            reused=reused,
        )

    if on_squeeze is not None:
        await on_squeeze(size_mb)
    compressed = await _squeeze(path, download_dir, media_id)
    final = compressed or path
    final_mb = final.stat().st_size / (1024 * 1024)
    if final_mb > max_size_mb:
        # Размер берётся у файла, который реально остался: сообщить исходные
        # 3 ГБ, когда после сжатия стало 2.5, — значит соврать пользователю.
        raise AudioTooLarge(final_mb, max_size_mb)
    return AudioResult(
        path=final,
        size_mb=final_mb,
        bitrate=bitrate_label(duration, final.name),
        reused=reused,
    )


async def _squeeze(path: Path, download_dir: Path, media_id: str) -> Path | None:
    """Пережать в 64 kbps атомарно.

    Возвращает файл, который остался на диске, или ``None``, если пережатия не
    было/оно не удалось (тогда остаётся исходник). Решение «влезло ли в лимит»
    принимает вызывающий: только он видит итоговый размер.

    Исходник удаляется лишь после успешного пережатия
    (`reencode_mp3_64k_atomic` пишет во временный файл и переименовывает), иначе
    при сбое ffmpeg пользователь остался бы без файла вообще.
    """
    target = download_dir / f"{media_id}_64.mp3"
    if path.name == target.name:
        # Уже 64 kbps — пережимать «в себя» нельзя: ffmpeg читал бы файл,
        # который сам же и пишет.
        return path
    converted = await reencode_mp3_64k_atomic(path, target)
    if not converted:
        logger.warning("Проверенное atomic-пережатие MP3 64k не удалось: %s", path.name)
        return None
    path.unlink(missing_ok=True)
    return target


__all__ = [
    "AudioError",
    "AudioResult",
    "AudioTooLarge",
    "LONG_AUDIO_SEC",
    "audio_quality",
    "bitrate_label",
    "download_args",
    "find_existing",
    "prepare_audio",
]
