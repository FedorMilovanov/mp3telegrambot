#!/usr/bin/env python3
"""
FFmpeg & yt-dlp helpers — базовые аргументы, энкодер, silence/black detection.
Извлечено из bot.py строки 751–780, 9235–9404.
"""
import asyncio
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path


# FIX #3: определяем здесь — ffmpeg.py не импортирует utils.py (избегаем кругового импорта)
PYTHON_EXEC  = sys.executable
COOKIES_FILE = Path("cookies.txt")

# FIX #4: инициализируем кэш энкодера на уровне модуля
_VIDEO_ENCODER: str | None = None

logger = logging.getLogger(__name__)

def _build_ytdlp_base_args() -> list:
    # FIXED #34: явный --no-config предотвращает авточтение ~/.config/yt-dlp/config
    # и ./yt-dlp.conf на сервере. Если локальный yt-dlp.conf существует — подключаем
    # его явно через --config-location, чтобы поведение было предсказуемым.
    args = [PYTHON_EXEC, "-m", "yt_dlp", "--no-config"]
    if Path("yt-dlp.conf").exists():
        args += ["--config-location", "yt-dlp.conf"]
    args += ["--sleep-interval", "2", "--quiet"]
    if COOKIES_FILE.exists():
        args += ["--cookies", str(COOKIES_FILE)]
    elif shutil.which("firefox"):
        args += ["--cookies-from-browser", "firefox"]
    else:
        print("⚠️ Нет cookies — YouTube может блокировать запросы")
    # JS runtime для решения YouTube n challenge
    # FIXED #33: deno первым — по документации yt-dlp первый в списке приоритетен;
    # deno быстрее для YouTube. node — fallback при отсутствии deno.
    js_runtimes = []
    if shutil.which("deno"):
        js_runtimes.append("deno")
    if shutil.which("node"):
        js_runtimes.append("node")
    if js_runtimes:
        args += ["--js-runtimes", ",".join(js_runtimes)]
        args += ["--remote-components", "ejs:github"]
    else:
        print("⚠️ Node.js/Deno не найдены — js-runtimes отключён")
    return args

YTDLP_BASE_ARGS = _build_ytdlp_base_args()

def _get_video_encoder() -> tuple[str, list[str], list[str]]:
    """
    Возвращает (encoder, quality_args, preset_args) для ffmpeg.

    Пробует h264_nvenc (NVIDIA GPU) — если доступен, использует его.
    Fallback: libx264 (CPU).

    nvenc параметры:
    - preset p4   — баланс скорость/качество (p1=быстро, p7=медленно)
    - -cq 23      — constant quality (аналог -crf для nvenc)
    - -rc vbr     — variable bitrate mode (нужен для -cq)

    libx264 параметры:
    - preset veryfast
    - -crf 23
    """
    global _VIDEO_ENCODER
    if _VIDEO_ENCODER is not None:
        if _VIDEO_ENCODER == "h264_nvenc":
            return "h264_nvenc", ["-rc", "vbr", "-cq", "23"], ["-preset", "p4"]
        return "libx264", ["-crf", "23"], ["-preset", "veryfast"]

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        try:
            result = subprocess.run(
                [ffmpeg, "-f", "lavfi", "-i", "nullsrc=s=720x1280:d=0.1",
                 "-c:v", "h264_nvenc", "-f", "null", "-"],
                capture_output=True, timeout=10,
            )
            if result.returncode == 0:
                _VIDEO_ENCODER = "h264_nvenc"
                logger.info("Video encoder: h264_nvenc (NVIDIA GPU) ✅")
                return "h264_nvenc", ["-rc", "vbr", "-cq", "23"], ["-preset", "p4"]
        except Exception:
            pass

    _VIDEO_ENCODER = "libx264"
    logger.info("Video encoder: libx264 (CPU fallback)")
    return "libx264", ["-crf", "23"], ["-preset", "veryfast"]


async def _find_silence_end(
    video_path: Path,
    target_end: float,
    search_window: float = 5.0,
    noise_db: float = -30.0,
    min_duration: float = 0.3,
) -> float:
    """
    Ищет ближайшую паузу к target_end в окне [target_end-2s .. target_end+3s].
    Возвращает скорректированное время конца или исходный target_end если пауз нет.

    Параметры:
      noise_db      — порог тишины в дБ (-30 достаточно для пауз между фразами)
      min_duration  — минимальная длительность паузы (0.3s = 300ms)
      search_window — ширина окна поиска (5s → ищем в ±2.5s от target_end)
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not video_path.exists():
        return target_end

    scan_start = max(0.0, target_end - search_window * 0.4)  # смещение: -2s от target
    scan_duration = search_window

    cmd = [
        ffmpeg,
        "-ss", f"{scan_start:.3f}",
        "-i", str(video_path),
        "-t", f"{scan_duration:.3f}",
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
        "-f", "null", "-",
    ]
    try:
        proc = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=30),
        )
        silences = []
        for line in proc.stderr.splitlines():
            if "silence_end:" in line:
                m = re.search(r"silence_end:\s*([\d.]+)", line)
                if m:
                    # время относительно scan_start → абсолютное время в видео
                    abs_time = scan_start + float(m.group(1))
                    silences.append(abs_time)
        if not silences:
            return target_end
        # Берём паузу ближайшую к исходному target_end
        best = min(silences, key=lambda t: abs(t - target_end))
        return best
    except Exception as e:
        logger.warning(f"_find_silence_end error: {e}")
        return target_end


async def _detect_black_bars(video_path: Path, sample_start: float = 0.0) -> str:
    """
    Запускает ffmpeg cropdetect на трёх точках фрагмента (начало, середина, +10с).
    Возвращает строку 'crop=W:H:X:Y' если найдены полосы, иначе ''.
    limit=32 — ловит серые/тёмные полосы (не только чисто чёрные).
    """
    try:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg or not video_path.exists():
            return ""
        loop = asyncio.get_running_loop()

        # Три точки: начало фрагмента + 2с, середина, + 10с — берём наиболее стабильный результат
        sample_points = [
            sample_start + 2.0,
            sample_start + 10.0,
            sample_start + 20.0,
        ]

        crop_votes: dict[str, int] = {}
        for sp in sample_points:
            cmd = [
                ffmpeg,
                "-ss", str(sp),
                "-i", str(video_path),
                "-t", "8",
                "-vf", "cropdetect=limit=32:round=2:reset=0",
                "-an", "-f", "null", "-",
            ]
            proc = await loop.run_in_executor(
                None,
                lambda c=cmd: subprocess.run(c, capture_output=True, text=True, timeout=30),
            )
            output = proc.stderr or ""
            crop_str = ""
            for line in output.splitlines():
                m = re.search(r"crop=(\d+:\d+:\d+:\d+)", line)
                if m:
                    crop_str = m.group(1)
            if crop_str:
                crop_votes[crop_str] = crop_votes.get(crop_str, 0) + 1

        if not crop_votes:
            return ""

        # Берём самый частый результат
        crop_str = max(crop_votes, key=lambda k: crop_votes[k])

        # Проверяем что реально что-то срезается (порог 4px)
        # Только читаем заголовок файла — ffmpeg выведет метаданные в stderr и сразу выйдет.
        # НЕ используем "-f null -": это читает ВЕСЬ файл и таймаутится на длинных видео.
        probe_cmd = [ffmpeg, "-i", str(video_path)]
        probe = await loop.run_in_executor(
            None,
            lambda: subprocess.run(probe_cmd, capture_output=True, text=True, timeout=5),
        )
        dim_m = re.search(r"(\d{3,4})x(\d{3,4})", probe.stderr or "")
        if dim_m:
            orig_w, orig_h = int(dim_m.group(1)), int(dim_m.group(2))
            parts = crop_str.split(":")
            crop_w, crop_h = int(parts[0]), int(parts[1])
            if crop_w >= orig_w - 4 and crop_h >= orig_h - 4:
                return ""
            logger.info(
                f"Black bars detected: {orig_w}x{orig_h} → crop={crop_str} "
                f"(срезано: {orig_w - crop_w}px по X, {orig_h - crop_h}px по Y)"
                f" | votes={crop_votes}"
            )
        return f"crop={crop_str}"
    except Exception as e:
        logger.warning(f"_detect_black_bars error: {type(e).__name__}: {e}")
        return ""



# Alias
_ytdlp_base_args = _build_ytdlp_base_args
