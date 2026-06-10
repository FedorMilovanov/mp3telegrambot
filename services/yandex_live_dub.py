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
  - скачивает MP3 с живыми голосами
  - умеет склеивать с видео (--merge-video)

Режимы:
  --quiet       → только URL или путь к файлу (для скриптов)
  --json        → структурированный JSON с audioUrl, mergedVideoPath и т.д.
  --output      → куда сохранить
  --voice-style=live → явно "Живые голоса" (не стандартный TTS)
"""
import asyncio
import json
import logging
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
    if len(cmd_parts) == 1 and " " in cmd_parts[0] and not cmd_parts[0].startswith('"'):
        cmd_parts = cmd_parts[0].split()

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


def _find_latest_file(directory: Path, pattern: str) -> Optional[Path]:
    """Находит самый свежий файл по шаблону в директории."""
    files = list(directory.glob(pattern))
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


async def get_live_dub_audio(video_url: str, output_dir: Path,
                             timeout: int = 480, retries: int = 2) -> Path:
    """Скачивает только MP3-перевод (Живые голоса) через vot-cli-live.

    AUDIT 2026-06-10: Яндекс готовит перевод длинного видео МИНУТЫ
    (официальный блог: час видео == минуты обработки); vot-cli поллит
    ~5 минут и сдаётся, хотя сервер продолжает готовить перевод —
    повторный запрос обычно получает готовый кэш. Поэтому retries.
    """
    vot = _check_vot_cli()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [vot, "--output", str(output_dir), "--voice-style", "live", "--quiet", video_url]
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
) -> Path:
    """Скачивает ВИДЕО с встроенным переводом (Живые голоса) через vot-cli-live."""
    vot = _check_vot_cli()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        vot, "--output", str(output_dir), "--voice-style", "live",
        "--merge-video", "--quiet",
    ]
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
    new_files = sorted(set(output_dir.glob("*.srt")) - before,
                       key=lambda f: f.stat().st_mtime, reverse=True)
    candidates = new_files or sorted(output_dir.glob("*.srt"),
                                     key=lambda f: f.stat().st_mtime, reverse=True)
    for f in candidates:
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
