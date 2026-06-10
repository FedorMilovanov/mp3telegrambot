#!/usr/bin/env python3
"""
FFmpeg & yt-dlp helpers — базовые аргументы, энкодер, silence/black detection.
Извлечено из bot.py строки 751–780, 9235–9404.
"""
import asyncio
import logging
import os
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
    # AUDIT 2026-06-10: yt-dlp.conf пользователя может содержать
    # --cookies-from-browser; вместе с нашим --cookies это двойная загрузка кук
    # (на Windows при запущенном Firefox его cookie-БД ещё и заблокирована).
    # Подключаем конф только если у нас НЕТ своего источника кук.
    _conf_exists = Path("yt-dlp.conf").exists()
    _conf_has_cookies = False
    if _conf_exists:
        try:
            _conf_has_cookies = "--cookies" in Path("yt-dlp.conf").read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    if COOKIES_FILE.exists():
        if _conf_exists and not _conf_has_cookies:
            args += ["--config-location", "yt-dlp.conf"]
        elif _conf_exists:
            logger.info("yt-dlp.conf содержит cookie-опции — пропускаю конф, использую cookies.txt")
        args += ["--sleep-interval", "2", "--quiet", "--cookies", str(COOKIES_FILE)]
    else:
        if _conf_exists:
            args += ["--config-location", "yt-dlp.conf"]
        args += ["--sleep-interval", "2", "--quiet"]
        if not _conf_has_cookies:
            if shutil.which("firefox"):
                args += ["--cookies-from-browser", "firefox"]
            else:
                logger.warning("⚠️ Нет cookies — YouTube может блокировать запросы")
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
        logger.warning("⚠️ Node.js/Deno не найдены — js-runtimes отключён")
    # PERF 2026-06-10: многопоточная загрузка фрагментов (DASH/HLS) —
    # нативная опция yt-dlp, заметно быстрее на длинных видео.
    # aria2c дал бы ещё больше, но это внешний процесс с краевыми
    # случаями; -N 4 — безопасный консенсус. Отключение: YTDLP_FRAGMENTS=1
    try:
        _frags = max(1, min(int(os.getenv("YTDLP_FRAGMENTS", "4")), 16))
    except ValueError:
        _frags = 4
    if _frags > 1:
        args += ["--concurrent-fragments", str(_frags)]
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
    # 2026-06-10 (уточнено пользователем): NVENC — отдельный аппаратный
    # ASIC-блок на GPU, НЕ использует CUDA-ядра. Глюки CUDA (из-за которых
    # Whisper на CPU) не затрагивают NVENC — он работает стабильно.
    # Поэтому НЕ наследуем WHISPER_FORCE_CPU; автопроба NVENC по умолчанию.
    # VIDEO_FORCE_CPU=1 — явное отключение, если NVENC тоже начнёт глючить.
    _force_cpu = os.getenv("VIDEO_FORCE_CPU", "0").strip() in {"1", "true", "yes", "on"}
    if _force_cpu and _VIDEO_ENCODER is None:
        _VIDEO_ENCODER = "libx264"
        logger.info("Video encoder: libx264 (CPU, принудительно — GPU помечена ненадёжной)")
    if _VIDEO_ENCODER is not None:
        if _VIDEO_ENCODER == "h264_nvenc":
            # QUALITY 2026-06-10: p5 + tune hq + spatial-aq + lookahead —
            # VMAF-паритет с libx264 medium (video.SE #29659); -b:v 0
            # обязателен для честного constant-quality режима.
            return "h264_nvenc", ["-rc", "vbr", "-cq", "23", "-b:v", "0", "-spatial-aq", "1", "-rc-lookahead", "8"], ["-preset", "p5", "-tune", "hq"]
        _preset = os.getenv("VIDEO_CPU_PRESET", "veryfast").strip() or "veryfast"
        if _preset not in {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"}:
            _preset = "veryfast"
        return "libx264", ["-crf", "23"], ["-preset", _preset]

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
                # QUALITY: см. комментарий выше (формула p5+hq+aq)
                return "h264_nvenc", ["-rc", "vbr", "-cq", "23", "-b:v", "0", "-spatial-aq", "1", "-rc-lookahead", "8"], ["-preset", "p5", "-tune", "hq"]
        except Exception:
            pass

    _VIDEO_ENCODER = "libx264"
    logger.info("Video encoder: libx264 (CPU fallback)")
    _preset = os.getenv("VIDEO_CPU_PRESET", "veryfast").strip() or "veryfast"
    if _preset not in {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"}:
        _preset = "veryfast"
    return "libx264", ["-crf", "23"], ["-preset", _preset]


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

        # AUDIT L4: ограничиваем точки длительностью видео — для коротких клипов
        # +20 секунд может уйти за EOF, ffmpeg впустую тратит 30 c CPU.
        try:
            from subprocess import run as _sp_run
            probe = _sp_run(
                [ffmpeg, "-i", str(video_path)],
                capture_output=True, text=True, timeout=10,
            )
            m_dur = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", probe.stderr or "")
            if m_dur:
                _video_total = (
                    int(m_dur.group(1)) * 3600 + int(m_dur.group(2)) * 60 + float(m_dur.group(3))
                )
            else:
                _video_total = sample_start + 30.0  # fallback: считаем что хватит на 30s
        except Exception:
            _video_total = sample_start + 30.0
        candidate_points = [sample_start + 2.0, sample_start + 10.0, sample_start + 20.0]
        sample_points = [p for p in candidate_points if p + 4.0 < _video_total]
        if not sample_points:
            sample_points = [sample_start + 0.5]

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



# AUDIT L3: алиас _ytdlp_base_args = _build_ytdlp_base_args был ловушкой —
# выглядел как список аргументов, на деле был ссылкой на функцию.
# Используйте YTDLP_BASE_ARGS (готовый список).
