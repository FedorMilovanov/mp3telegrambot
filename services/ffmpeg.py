#!/usr/bin/env python3
"""
FFmpeg & yt-dlp helpers — базовые аргументы, энкодер, silence/black detection.
Извлечено из bot.py строки 751–780, 9235–9404.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, List, Tuple


# FIX #3: определяем здесь — ffmpeg.py не импортирует utils.py (избегаем кругового импорта)
PYTHON_EXEC  = sys.executable
COOKIES_FILE = Path("cookies.txt")

# FIX #4: инициализируем кэш энкодера на уровне модуля
_VIDEO_ENCODER: str | None = None

logger = logging.getLogger(__name__)


def _parse_ytdlp_conf_tokens(conf_text: str) -> list[str]:
    try:
        return shlex.split(conf_text, comments=True, posix=True)
    except ValueError:
        # Битый конфиг лучше не интерпретировать частично: пусть yt-dlp сам
        # выдаст ошибку, если пользователь явно этого хочет.
        return []


def _has_ytdlp_cookie_options(tokens: list[str]) -> bool:
    return any(
        tok in {"--cookies", "--cookies-from-browser"}
        or tok.startswith("--cookies=")
        or tok.startswith("--cookies-from-browser=")
        for tok in tokens
    )


def _extract_cookies_from_browser_specs(conf_text: str) -> list[str]:
    """Достаёт значения --cookies-from-browser из yt-dlp.conf.

    Поддерживает оба синтаксиса: `--cookies-from-browser firefox` и
    `--cookies-from-browser=firefox:Profile`.
    """
    tokens = _parse_ytdlp_conf_tokens(conf_text)
    specs: list[str] = []
    for idx, tok in enumerate(tokens):
        if tok == "--cookies-from-browser" and idx + 1 < len(tokens):
            specs.append(tokens[idx + 1])
        elif tok.startswith("--cookies-from-browser="):
            specs.append(tok.split("=", 1)[1])
    return [s for s in specs if s]


def _firefox_cookie_source_available(spec: str = "firefox") -> bool:
    """Есть ли локальный Firefox cookie-store для yt-dlp.

    Важно: наличие firefox.exe/бинаря НЕ означает наличие профиля. На
    headless-сервере репозиторный `yt-dlp.conf` с `--cookies-from-browser
    firefox` иначе валит все скачивания ошибкой "could not find firefox
    cookies database". Если указан абсолютный путь профиля — проверяем его.
    """
    profile = ""
    if ":" in spec:
        # yt-dlp: BROWSER[:PROFILE][::KEYRING]
        profile = spec.split(":", 1)[1].split(":", 1)[0].strip()
    if profile:
        pp = Path(profile).expanduser()
        if pp.is_absolute():
            if (pp / "cookies.sqlite").exists() or pp.exists():
                return True
            return False

    home = Path.home()
    candidates = [
        home / ".mozilla" / "firefox",
        home / ".config" / "mozilla" / "firefox",
        home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox",
        home / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
        home / "Library" / "Application Support" / "Firefox" / "Profiles",
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "Mozilla" / "Firefox" / "Profiles")
    for root in candidates:
        try:
            if root.exists() and any(root.rglob("cookies.sqlite")):
                return True
        except OSError:
            continue
    return False


def _cookies_from_browser_source_available(spec: str) -> bool:
    browser = (spec.split(":", 1)[0] or "").strip().lower()
    if browser in {"firefox", "firefox-container"}:
        return _firefox_cookie_source_available(spec)
    # Для Chrome/Edge/Yandex/прочих профили и keyring зависят от платформы;
    # не угадываем агрессивно, чтобы не ломать явно настроенный прод.
    return True




def _version_tuple_from_text(text: str) -> tuple[int, ...]:
    m = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", text or "")
    if not m:
        return ()
    return tuple(int(x) for x in m.groups("0"))


def _probe_js_runtime_version(exe: str, args: list[str]) -> tuple[int, ...]:
    try:
        proc = subprocess.run(
            [exe, *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=5,
        )
    except Exception:
        return ()
    return _version_tuple_from_text((proc.stdout or "") + "\n" + (proc.stderr or ""))


def _supported_js_runtimes() -> list[str]:
    """yt-dlp 2026.06.09 поднял минимальные версии JS-runtime.

    Если передать неподдерживаемый runtime через --js-runtimes, yt-dlp может
    считать, что JS-runtime задан, но затем не сможет выполнить YouTube n/SABR
    JS. Поэтому фильтруем не только по наличию бинаря, но и по версии.
    """
    runtimes: list[str] = []
    deno = shutil.which("deno")
    if deno:
        vt = _probe_js_runtime_version(deno, ["--version"])
        if vt >= (2, 3, 0):
            runtimes.append("deno")
        else:
            logger.warning("⚠️ Deno найден, но версия %s < 2.3.0 — не передаю в yt-dlp --js-runtimes", vt or "unknown")
    node = shutil.which("node")
    if node:
        vt = _probe_js_runtime_version(node, ["--version"])
        if vt >= (22, 0, 0):
            runtimes.append("node")
        else:
            logger.warning("⚠️ Node.js найден, но версия %s < 22.0.0 — не передаю в yt-dlp --js-runtimes", vt or "unknown")
    return runtimes

def _build_ytdlp_base_args() -> list:
    # FIXED #34: явный --no-config предотвращает авточтение ~/.config/yt-dlp/config
    # и ./yt-dlp.conf на сервере. Если локальный yt-dlp.conf существует — подключаем
    # его явно через --config-location, чтобы поведение было предсказуемым.
    args = [PYTHON_EXEC, "-m", "yt_dlp", "--no-config"]
    # AUDIT 2026-06-10: yt-dlp.conf пользователя может содержать
    # --cookies-from-browser; вместе sheep с нашим --cookies это двойная загрузка кук
    # (на Windows при запущенном Firefox его cookie-БД ещё и заблокирована).
    # Подключаем конф только если у нас НЕТ своего источника кук.
    _conf_exists = Path("yt-dlp.conf").exists()
    _conf_has_cookies = False
    _conf_cookie_browser_specs: list[str] = []
    _conf_cookie_sources_ok = True
    if _conf_exists:
        try:
            _conf_text = Path("yt-dlp.conf").read_text(encoding="utf-8", errors="replace")
            _conf_tokens = _parse_ytdlp_conf_tokens(_conf_text)
            _conf_has_cookies = _has_ytdlp_cookie_options(_conf_tokens)
            _conf_cookie_browser_specs = _extract_cookies_from_browser_specs(_conf_text)
            _conf_cookie_sources_ok = all(
                _cookies_from_browser_source_available(spec)
                for spec in _conf_cookie_browser_specs
            )
        except OSError:
            pass
    _use_conf = _conf_exists
    if _conf_exists and _conf_cookie_browser_specs and not _conf_cookie_sources_ok:
        # Репозиторный yt-dlp.conf часто содержит `--cookies-from-browser firefox`.
        # На машине без Firefox-профиля yt-dlp падает до скачивания; безопаснее
        # пропустить cookie-конфиг и продолжить без cookies / с cookies.txt.
        _use_conf = False
        logger.warning(
            "yt-dlp.conf просит --cookies-from-browser, но профиль cookies не найден — "
            "пропускаю конфиг; положите cookies.txt или настройте YTDLP_COOKIES_FROM_BROWSER"
        )
    if COOKIES_FILE.exists():
        if _use_conf and not _conf_has_cookies:
            args += ["--config-location", "yt-dlp.conf"]
        elif _conf_exists and _conf_has_cookies:
            logger.info("yt-dlp.conf содержит cookie-опции — пропускаю конф, использую cookies.txt")
        args += ["--sleep-interval", "2", "--quiet", "--cookies", str(COOKIES_FILE)]
    else:
        if _use_conf:
            args += ["--config-location", "yt-dlp.conf"]
        args += ["--sleep-interval", "2", "--quiet"]
        # Явная ручка лучше автоугадывания: YTDLP_COOKIES_FROM_BROWSER=firefox[:profile]
        _env_browser = os.getenv("YTDLP_COOKIES_FROM_BROWSER", "").strip()
        if _env_browser and (not _conf_has_cookies or not _use_conf):
            if _cookies_from_browser_source_available(_env_browser):
                args += ["--cookies-from-browser", _env_browser]
            else:
                logger.warning("⚠️ YTDLP_COOKIES_FROM_BROWSER=%s задан, но профиль cookies не найден", _env_browser)
        elif not _conf_has_cookies and _firefox_cookie_source_available("firefox"):
            args += ["--cookies-from-browser", "firefox"]
        elif not _conf_has_cookies or not _use_conf:
            logger.warning("⚠️ Нет cookies — YouTube может блокировать запросы")
    # JS runtime для решения YouTube n challenge
    # FIXED #33: deno первым — по документации yt-dlp первый в списке приоритетен;
    # deno быстрее для YouTube. node — fallback при отсутствии deno.
    js_runtimes = _supported_js_runtimes()
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
        
    # 2026-06-11: Предпочитаем m4a для аудио, чтобы избежать медленного перекодирования webm в mp3.
    # Но оставляем 'bestaudio' как базу для стабильности.
    args += ["--format-sort", "ext:mp4:m4a"]
    
    return args

YTDLP_BASE_ARGS = _build_ytdlp_base_args()

def normalize_mp3_lossless(mp3_path: Path, target_db: float = 92.0) -> bool:
    """Lossless-нормализация громкости MP3 через mp3gain (если установлен).

    QUALITY 2026-06-10 (round 26, исправление round 25): single-pass
    loudnorm — ДИНАМИЧЕСКАЯ нормализация, на речи с паузами «дышит»
    (pumping) — наше же исследование (round 5): 'Single-pass pumps;
    don't use it for VOD'. mp3gain меняет только поле global_gain в
    заголовках MP3-фреймов: БЕЗ перекодирования, без потерь, обратимо.
    target 92.0 dB ≈ 89 (стандарт ReplayGain) + 3 dB — комфортно для
    речи в Telegram. Шаг точности mp3gain — 1.5 dB (достаточно).

    Возвращает True если нормализация применена. Нет mp3gain — False
    (файл не трогаем; честная деградация без ухудшения качества).
    Отключение: MP3_LOUDNORM=0.
    """
    if os.getenv("MP3_LOUDNORM", "1").strip().lower() in {"0", "false", "no", "off"}:
        return False
    mp3gain = shutil.which("mp3gain") or shutil.which("mp3gain.exe")
    if not mp3gain:
        return False
    try:
        kwargs: dict = {"capture_output": True, "text": True,
                        "encoding": "utf-8", "errors": "replace"}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        # /r: track gain, /d: смещение цели от 89 dB, /c: без вопросов про клиппинг,
        # /t: писать тег APE с undo-информацией
        offset = target_db - 89.0
        proc = subprocess.run(
            [mp3gain, "-r", "-c", "-d", f"{offset:.1f}", str(mp3_path)],
            timeout=600, **kwargs,
        )
        if proc.returncode == 0:
            logger.info("MP3 нормализован lossless (mp3gain, цель %.0f dB)", target_db)
            return True
        logger.warning("mp3gain rc=%s: %s", proc.returncode, (proc.stderr or "")[-200:])
    except Exception as e:
        logger.warning("mp3gain не сработал: %s", e)
    return False


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
    # 2026-06-11: Добавляем поддержку AV1 (NVIDIA) для еще лучшего сжатия при высоком качестве.
    # Telegram в 2026 полностью поддерживает AV1.
    _force_cpu = os.getenv("VIDEO_FORCE_CPU", "0").strip() in {"1", "true", "yes", "on"}
    
    if _VIDEO_ENCODER is not None:
        if _VIDEO_ENCODER == "av1_nvenc":
             return "av1_nvenc", ["-rc", "vbr", "-cq", "25", "-b:v", "0", "-spatial-aq", "1"], ["-preset", "p5"]
        if _VIDEO_ENCODER == "h264_nvenc":
            return "h264_nvenc", ["-rc", "vbr", "-cq", "23", "-b:v", "0", "-spatial-aq", "1", "-rc-lookahead", "8"], ["-preset", "p5", "-tune", "hq"]
        _preset = os.getenv("VIDEO_CPU_PRESET", "veryfast").strip() or "veryfast"
        if _preset not in {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"}:
            _preset = "veryfast"
        return "libx264", ["-crf", "23"], ["-preset", _preset]

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg and not _force_cpu:
        # Пробуем AV1 NVENC (Ada Lovelace+)
        try:
            result = subprocess.run(
                [ffmpeg, "-f", "lavfi", "-i", "nullsrc=s=720x1280:d=0.1",
                 "-c:v", "av1_nvenc", "-f", "null", "-"],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                _VIDEO_ENCODER = "av1_nvenc"
                logger.info("Video encoder: av1_nvenc (NVIDIA AV1) ✅")
                return "av1_nvenc", ["-rc", "vbr", "-cq", "25", "-b:v", "0", "-spatial-aq", "1"], ["-preset", "p5"]
        except Exception:
            pass

        # Пробуем H264 NVENC
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



async def probe_video_language(video_url: str) -> Optional[str]:
    """Определяет язык видео через yt-dlp metadata."""
    try:
        cmd = YTDLP_BASE_ARGS + ["--dump-json", video_url]
        loop = asyncio.get_running_loop()
        proc = await loop.run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            lang = data.get("language")
            if lang:
                # yt-dlp часто возвращает 'en', 'ru', 'de'
                return str(lang).lower()
    except Exception:
        pass
    return None


# AUDIT L3: алиас _ytdlp_base_args = _build_ytdlp_base_args был ловушкой —
# выглядел как список аргументов, на деле был ссылкой на функцию.
# Используйте YTDLP_BASE_ARGS (готовый список).
