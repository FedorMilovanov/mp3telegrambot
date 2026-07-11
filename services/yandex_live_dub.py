#!/usr/bin/env python3
"""
Интеграция vot-cli-live (Яндекс "Живые голоса") для MP3Bot.
Windows/Linux-совместимая версия с fallback'ами.

Требует:
    npm install -g vot-cli-live
    (опционально) npm install -g deno
    yt-dlp, ffmpeg (уже есть в боте)

vot-cli-live делает всё сам:
  - запрашивает перевод на серверы Яндекса
  - ждёт готовности (polling до 5 мин)
  - скачивает MP3 with живыми голосами
  - умеет склеивать с видео (--merge-video)

Режимы:
  --quiet       → только URL или путь к файлу (для скриптов)
  --json        → структурированный JSON с audioUrl, mergedVideoPath и т.д.
  --output      → куда сохранить
  --voice-style=live → явно "Живые голоса" (не стандартный TTS)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

VOT_CLI = "vot-cli-live"


def _check_vot_cli() -> str:
    """Проверяет, что vot-cli-live доступен. Возвращает команду для запуска."""
    path = shutil.which(VOT_CLI)
    if path:
        return VOT_CLI

    # Windows: explicit .cmd / .bat search (shutil.which may miss .cmd on some configs)
    if sys.platform == "win32":
        for ext in (".cmd", ".bat", ".exe"):
            path = shutil.which(VOT_CLI + ext)
            if path:
                return path

    # Windows: npm global default path (Python may not have %APPDATA%\npm in PATH)
    if sys.platform == "win32":
        import os
        _appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        npm_global = Path(_appdata) / "npm"
        for ext in (".cmd", ".bat", ".exe", ""):
            candidate = npm_global / (VOT_CLI + ext)
            if candidate.exists():
                return str(candidate)

    # Windows fallback: npx
    npx = shutil.which("npx")
    if npx:
        return f"{npx} {VOT_CLI}"

    # Windows: npm global путь
    if sys.platform == "win32":
        import os
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            for ext in (".cmd", ".bat", ""):
                candidate = Path(appdata) / "npm" / f"{VOT_CLI}{ext}"
                if candidate.exists():
                    return str(candidate)
        # ProgramFiles nodejs
        pf = os.environ.get("ProgramFiles", "C:\\Program Files")
        candidate = Path(pf) / "nodejs" / f"{VOT_CLI}.cmd"
        if candidate.exists():
            return str(candidate)

    raise RuntimeError(
        "vot-cli-live не найден. Установите: npm install -g vot-cli-live\n"
        "На Windows: npx vot-cli-live --help"
    )


def _run_subprocess(cmd_parts: list, cwd: Optional[Path] = None, timeout: int = 600):
    """Универсальный subprocess wrapper. Возвращает (stdout, stderr, returncode)."""
    # FIX AUDIT R4: npx-fallback возвращает argv[0]="/usr/bin/npx vot-cli-live"
    # одной строкой, а все вызовы добавляют свои аргументы — старое условие
    # len==1 никогда не срабатывало и subprocess падал с FileNotFoundError
    # (весь старый протокол и выгрузка перевод-SRT были мертвы на npx-хостах).
    # Сплитим argv[0] с пробелом, если это не существующий путь
    # (защита от "C:\Program Files\...\vot-cli-live.cmd").
    if cmd_parts and " " in str(cmd_parts[0]) and not str(cmd_parts[0]).startswith('"') \
            and not Path(str(cmd_parts[0])).exists():
        cmd_parts = str(cmd_parts[0]).split() + list(cmd_parts[1:])

    # Windows: .cmd/.bat files need shell=True for subprocess.run
    _use_shell = False
    if sys.platform == "win32" and cmd_parts:
        _cmd = cmd_parts[0].lower()
        if _cmd.endswith(".cmd") or _cmd.endswith(".bat") or shutil.which(_cmd + ".cmd"):
            _use_shell = True

    try:
        proc = subprocess.run(
            cmd_parts,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            timeout=timeout,
            shell=_use_shell,
        )
    except FileNotFoundError as e:
        raise RuntimeError(f"Команда не найдена: {cmd_parts[0]}. {e}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"vot-cli-live timed out (> {timeout} сек)")
    return proc.stdout, proc.stderr, proc.returncode


# ── Новый протокол Яндекса (round 39) ───────────────────────────────
# С мая 2025 (VOT 1.10) Яндекс требует OAuth-авторизацию для «Живых
# голосов»: сервер отвечает status=7 SESSION_REQUIRED, который старый
# vot-cli-live показывает как 'Translation not available'. ДОКАЗАНО
# живым тестом: те же ролики через @vot.js/node возвращают status=7
# (live) и status=2 WAITING (tts); ролик, уже переведённый юзером в
# Я.Браузере, отдаёт live-MP3 мгновенно (серверный кэш) — поэтому
# «Shorts раньше работали». vot_helper/vot_live.mjs шлёт
# Authorization: OAuth <VOT_API_TOKEN>, как сам VOT после логина.
# Также поддерживается алиас YANDEX_OAUTH_TOKEN.
VOT_HELPER_DIR = Path(__file__).resolve().parent.parent / "vot_helper"


def _vot_oauth_token_present() -> bool:
    return bool((os.getenv("VOT_API_TOKEN", "") or os.getenv("YANDEX_OAUTH_TOKEN", "")).strip())


def _vot_request_duration(duration: float) -> int:
    """Длительность для VOT cache-key/request.

    Для Shorts/LiveDub важно попадать в кэш Яндекса. Эксперименты показали,
    что лишний паддинг в запросе может приводить к LIVEDUB_NOT_AVAILABLE.
    Используем целое число секунд (floor), как это делает оригинальный VOT.
    """
    if not duration or duration <= 0:
        return 0
    return int(float(duration))


def _find_node() -> Optional[str]:
    for name in ("node", "node.exe"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _ensure_vot_helper() -> Optional[list]:
    """Возвращает команду [node, vot_live.mjs], если новый протокол доступен.

    При первом запуске сам ставит npm-зависимости (@vot.js/node) в
    vot_helper/node_modules. Если node/npm нет — возвращает None,
    и код падает обратно на старый vot-cli-live.
    """
    node = _find_node()
    script = VOT_HELPER_DIR / "vot_live.mjs"
    if not node or not script.exists():
        return None
    dep = VOT_HELPER_DIR / "node_modules" / "@vot.js" / "node"
    if not dep.exists():
        npm = shutil.which("npm") or shutil.which("npm.cmd")
        if not npm:
            return None
        logger.info("[LiveDub] Ставлю зависимости нового протокола (npm install в vot_helper)...")
        try:
            _run_subprocess(
                [npm, "install", "--omit=dev", "--no-audit", "--no-fund"],
                cwd=VOT_HELPER_DIR, timeout=300,
            )
        except Exception as e:
            logger.warning(f"[LiveDub] npm install в vot_helper не удался: {e}")
        if not dep.exists():
            return None
    return [node, str(script)]


async def _get_audio_new_protocol(
    helper: list, video_url: str, output_dir: Path,
    timeout: int, voice_style: str, duration: float = 0.0,
    lang: str = "",
) -> Path:
    """Скачивает перевод через vot_helper (новый протокол, OAuth-токен)."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 2026-06-11: Нормализуем код языка (en-us -> en), т.к. Яндекс предпочитает ISO 639-1.
    _clean_lang = lang.split('-')[0].lower() if lang else ""
    
    cmd = helper + [
        "--url", video_url,
        "--output", str(output_dir),
        "--voice-style", voice_style,
        "--timeout", str(timeout),
    ]
    if _clean_lang:
        cmd += ["--lang", _clean_lang]
    request_duration = _vot_request_duration(duration)
    if request_duration > 0:
        cmd += ["--duration", str(request_duration)]
    if voice_style == "live" and not _vot_oauth_token_present():
        logger.warning(
            "[LiveDub] VOT_API_TOKEN/YANDEX_OAUTH_TOKEN не задан — живые голоса сработают только "
            "если перевод уже есть в кэше Яндекса. Инструкция в .env.example"
        )
    _dur_log = f" duration={request_duration}s" if request_duration > 0 else ""
    if request_duration and duration and request_duration != int(duration):
        _dur_log += f" (source={duration:g}s)"
    logger.info(f"[LiveDub] Новый протокол: vot_live.mjs --voice-style {voice_style}{_dur_log} {video_url}")
    loop = asyncio.get_running_loop()
    stdout, stderr, rc = await loop.run_in_executor(
        None, lambda: _run_subprocess(cmd, timeout=timeout + 60)
    )
    if rc == 0:
        lines = [l.strip() for l in stdout.splitlines() if l.strip()]
        if lines:
            p = Path(lines[-1])
            if p.exists():
                logger.info(f"[LiveDub] Готово аудио (новый протокол): {p}")
                return p
        p = _find_latest_file(output_dir, "*.mp3")
        if p and p.exists():
            return p
        raise RuntimeError(f"vot_live.mjs не сохранил MP3. stdout: {stdout[:300]}")
    if rc == 3 or "LIVEDUB_AUTH_REQUIRED" in stderr:
        raise RuntimeError("LIVEDUB_AUTH_REQUIRED")
    if rc == 4 or "LIVEDUB_NOT_AVAILABLE" in stderr:
        raise RuntimeError("LIVEDUB_NOT_AVAILABLE")
    logger.error(f"[LiveDub] vot_live.mjs stderr: {stderr[-500:]}")
    raise RuntimeError(f"vot_live.mjs exit code {rc}")


def _find_latest_file(directory: Path, pattern: str) -> Optional[Path]:
    """Находит самый свежий файл по шаблону, пропуская НАШИ служебные артефакты.

    AUDIT ENG (2026-07-05): в одном workdir параллельно живут скачанный
    оригинал (original_video.* / original_audio.*), наш микс (pro_dub*.mp4)
    и QA-извлечения (*_qa.mp3). Если vot-cli не напечатал путь в stdout,
    старый «самый свежий файл» мог выдать пользователю ОРИГИНАЛ вместо
    перевода. Служебные имена детерминированы — исключаем их.
    """
    files = [
        p for p in directory.glob(pattern)
        if not p.name.startswith(("original_video", "original_audio", "pro_dub", "live_dub_merged"))
        and not p.stem.endswith(("_qa", "_qa_original"))
    ]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


async def get_live_dub_audio(video_url: str, output_dir: Path,
                             timeout: int = 480, retries: int = 2,
                             voice_style: str = "live",
                             duration: float = 0.0,
                             lang: str = "") -> Path:
    """Скачивает MP3-перевод через vot-cli-live.

    AUDIT 2026-06-11: Яндекс готовит перевод длинного видео МИНУТЫ.
    OAuth-токен теперь обязателен для живых голосов.
    """
    # ── Путь 1: новый протокол (@vot.js/node, OAuth) ──
    helper = await asyncio.get_running_loop().run_in_executor(None, _ensure_vot_helper)
    if helper:
        try:
            return await _get_audio_new_protocol(
                helper, video_url, output_dir,
                timeout=max(timeout, 900), voice_style=voice_style, duration=duration,
                lang=lang,
            )
        except RuntimeError as e:
            msg = str(e)
            if "LIVEDUB_AUTH_REQUIRED" in msg or "LIVEDUB_NOT_AVAILABLE" in msg:
                raise  # честные отказы — наверх (там fallback live→tts)
            logger.warning(f"[LiveDub] Новый протокол сбоит ({msg[:120]}) — пробую старый vot-cli")
    else:
        logger.info("[LiveDub] node/vot_helper недоступны — работаю через vot-cli-live (старый протокол)")

    # ── Путь 2: старый vot-cli-live ──
    vot = _check_vot_cli()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    _clean_lang = lang.split('-')[0].lower() if lang else ""
    cmd = [vot, "--output", str(output_dir), "--voice-style", voice_style, "--quiet"]
    if _clean_lang:
        cmd += ["--lang", _clean_lang]
    cmd.append(video_url)
    logger.info(f"[LiveDub] Запуск: {' '.join(cmd)}")

    loop = asyncio.get_running_loop()
    stdout = stderr = ""
    rc = 1
    for _attempt in range(max(1, retries + 1)):
        if _attempt:
            logger.info(f"[LiveDub] Перевод ещё готовится у Яндекса — повтор {_attempt}/{retries} через 90с")
            await asyncio.sleep(90)
        stdout, stderr, rc = await loop.run_in_executor(
            None, lambda: _run_subprocess(cmd, timeout=timeout)
        )
        if rc == 0:
            break
        if "Translation not available" in stderr or "Translation not available" in stdout:
            raise RuntimeError("LIVEDUB_NOT_AVAILABLE")

    if rc != 0:
        if "Translation not available" in stderr or "Translation not available" in stdout:
            raise RuntimeError("LIVEDUB_NOT_AVAILABLE")
        logger.error(f"[LiveDub] stderr: {stderr[-500:]}")
        raise RuntimeError(f"vot-cli-live exit code {rc}")

    lines = [l.strip() for l in stdout.splitlines() if l.strip()]
    downloaded_path = None
    if lines:
        try_path = Path(lines[-1])
        if try_path.exists():
            downloaded_path = try_path

    if not downloaded_path or not downloaded_path.exists():
        downloaded_path = _find_latest_file(output_dir, "*.mp3")

    if not downloaded_path or not downloaded_path.exists():
        raise RuntimeError(f"vot-cli-live не сохранил MP3. stdout: {stdout[:500]}")

    logger.info(f"[LiveDub] Готово аудио: {downloaded_path}")
    return downloaded_path


async def get_live_dub_video(
    video_url: str,
    output_dir: Path,
    original_volume: float = 0.3,
    translation_volume: float = 1.5,
    keep_original_audio: bool = True,
    duration: float = 0.0,
    lang: str = "",
) -> Path:
    """Скачивает/собирает ВИДЕО с переводом строго «Живые голоса».

    Новый путь: сначала получаем live-MP3 через новый протокол/OAuth, потом
    сами миксуем с оригинальным видео. Старый vot-cli --merge-video остаётся
    только аварийным fallback'ом, потому что он ходит по старому протоколу и
    на части роликов ложно отвечает Translation not available.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Сначала — новый протокол. Это закрывает случай, когда pro-mix выключен:
    # раньше этот путь всё равно падал в старый vot-cli --merge-video.
    try:
        ru_audio = await get_live_dub_audio(video_url, output_dir, timeout=900, voice_style="live", duration=duration, lang=lang)
        from services.eng_subtitles import download_original_video
        from services.livedub_mix import mix_tracks
        orig_video = await download_original_video(video_url, output_dir)
        out_path = output_dir / "live_dub_merged.mp4"
        mixed = await mix_tracks(orig_video, ru_audio, out_path)
        if mixed and mixed.exists():
            logger.info(f"[LiveDub] Готово видео (новый протокол + локальный микс): {mixed}")
            return mixed
        logger.warning("[LiveDub] локальный микс нового протокола не удался — пробую старый vot-cli merge")
    except RuntimeError as e:
        if "LIVEDUB_AUTH_REQUIRED" in str(e) or "LIVEDUB_NOT_AVAILABLE" in str(e):
            raise
        logger.warning(f"[LiveDub] новый merge-путь сбоит ({str(e)[:120]}) — пробую старый vot-cli merge")
    except Exception as e:
        logger.warning(f"[LiveDub] новый merge-путь сбоит ({str(e)[:120]}) — пробую старый vot-cli merge")

    vot = _check_vot_cli()

    cmd = [
        vot, "--output", str(output_dir), "--voice-style", "live",
        "--merge-video", "--quiet",
    ]
    _clean_lang = lang.split('-')[0].lower() if lang else ""
    if _clean_lang:
        cmd += ["--lang", _clean_lang]
    if not keep_original_audio:
        cmd.append("--keep-original-audio=false")
    else:
        cmd += [
            "--original-volume", str(original_volume),
            "--translation-volume", str(translation_volume),
        ]
    cmd.append(video_url)

    logger.info(f"[LiveDub] Merge-video: {' '.join(cmd)}")
    loop = asyncio.get_running_loop()
    stdout = stderr = ""
    rc = 1
    for _attempt in range(2):
        if _attempt:
            logger.info(f"[LiveDub] merge-video повтор {_attempt}/1 через 90с (перевод мог ещё готовиться)")
            await asyncio.sleep(90)
        stdout, stderr, rc = await loop.run_in_executor(
            None, lambda: _run_subprocess(cmd, timeout=600)
        )
        if rc == 0:
            break
        if "Translation not available" in stderr or "Translation not available" in stdout:
            raise RuntimeError("LIVEDUB_NOT_AVAILABLE")

    if rc != 0:
        logger.error(f"[LiveDub] stderr: {stderr[-500:]}")
        raise RuntimeError(f"vot-cli-live (merge-video) failed: {rc}")

    lines = [l.strip() for l in stdout.splitlines() if l.strip()]
    downloaded_path = None
    if lines:
        try_path = Path(lines[-1])
        if try_path.exists():
            downloaded_path = try_path

    if not downloaded_path or not downloaded_path.exists():
        downloaded_path = _find_latest_file(output_dir, "*.mp4")

    if not downloaded_path or not downloaded_path.exists():
        raise RuntimeError(f"vot-cli-live не сохранил MP4. stdout: {stdout[:500]}")

    # Добавить русские субтитры (Whisper на переведенном аудио)
    # Отключено: теперь делаем через Gemini в main_pipeline.py
    # _add_russian_subtitles(downloaded_path)

    logger.info(f"[LiveDub] Готово видео: {downloaded_path}")
    return downloaded_path


async def get_live_dub_info(video_url: str) -> dict:
    """Получает JSON-информацию о переводе через vot-cli-live."""
    vot = _check_vot_cli()
    cmd = [vot, "--json", "--voice-style", "live", video_url]

    loop = asyncio.get_running_loop()
    stdout, stderr, rc = await loop.run_in_executor(None, lambda: _run_subprocess(cmd, timeout=120))

    if rc != 0:
        raise RuntimeError(f"vot-cli-live --json failed: {stderr[-500:]}")

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        match = None
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("[") or line.startswith("{"):
                match = line
                break
        if not match:
            raise RuntimeError(f"Не удалось распарсить JSON: {stdout[:500]}")
        data = json.loads(match)

    if isinstance(data, list) and data:
        return data[0]
    return data


async def get_translation_subtitles(video_url: str, output_dir: Path) -> Optional[Path]:
    """Скачивает СУБТИТРЫ ПЕРЕВОДА (текст с таймкодами) через vot-cli-live.

    Это точный текст того, что озвучивает Яндекс, с миллисекундными
    таймкодами — идеальный вход для QA-проверки перевода: дешевле и
    точнее, чем сравнение аудио-с-аудио.
    Возвращает путь к SRT или None (не критично — QA умеет fallback).
    """
    vot = _check_vot_cli()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    before = set(output_dir.glob("*.srt"))
    cmd = [vot, "--subs-srt", "--reslang", "ru", "--output", str(output_dir), video_url]
    logger.info(f"[LiveDub] Субтитры перевода: {' '.join(cmd)}")
    loop = asyncio.get_running_loop()
    try:
        stdout, stderr, rc = await loop.run_in_executor(
            None, lambda: _run_subprocess(cmd, timeout=180)
        )
    except Exception as e:
        logger.warning(f"[LiveDub] subs fetch failed: {e}")
        return None
    if rc != 0:
        logger.info(f"[LiveDub] субтитры перевода недоступны (rc={rc})")
        return None
    # AUDIT R40: берём ТОЛЬКО новые .srt этого прогона. Прежний откат на любой
    # старый *.srt по mtime мог подсунуть QA устаревший файл прошлого видео и
    # исказить сравнение перевода. Нет нового файла → None (QA пропустит шаг).
    new_files = sorted(set(output_dir.glob("*.srt")) - before,
                       key=lambda f: f.stat().st_mtime, reverse=True)
    for f in new_files:
        if f.stat().st_size > 50:
            return f
    return None


async def is_live_available(video_url: str) -> Tuple[bool, Optional[str]]:
    """Проверяет, доступны ли для видео Живые голоса."""
    try:
        info = await get_live_dub_info(video_url)
        if info.get("success") and info.get("voiceType") == "live":
            return True, info.get("audioUrl")
        return False, None
    except Exception as e:
        logger.warning(f"[LiveDub] Проверка доступности не удалась: {e}")
        return False, None


async def download_live_dub_sync(
    video_url: str,
    output_dir: Path,
    mode: str = "audio",
    original_volume: float = 0.3,
) -> Path:
    """Синхронный wrapper — скачать аудио или видео с живым переводом."""
    if mode == "audio":
        return await get_live_dub_audio(video_url, output_dir)
    elif mode == "video":
        return await get_live_dub_video(
            video_url, output_dir,
            original_volume=original_volume,
            keep_original_audio=True,
        )
    else:
        raise ValueError(f"mode must be 'audio' or 'video', got {mode}")
