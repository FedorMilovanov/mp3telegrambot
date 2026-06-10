#!/usr/bin/env python3
"""
LiveDub Pro Mix — профессиональный микс перевода «Живые голоса».

Зачем: vot-cli --merge-video миксует сам и это чёрный ящик —
оригинал получается почти неслышен, задержку перевода он не умеет,
и финальный микс уже не отредактировать.

Здесь мы:
1. Берём у Яндекса ЧИСТУЮ дорожку перевода (get_live_dub_audio).
2. Скачиваем оригинальное видео с английским звуком.
3. Миксуем сами через ffmpeg:
   • оригинал слышен (LIVEDUB_ORIG_VOLUME, дефолт 0.45);
   • sidechain-ducking — когда говорит перевод, оригинал автоматически
     приглушается, в паузах перевода английский слышен в полный рост
     (как у живых синхронистов);
   • перевод сдвинут на LIVEDUB_DELAY_MS (дефолт 600 мс) — сначала
     слышно английскую фразу, потом догоняет перевод;
   • видеопоток копируется без перекодирования (-c:v copy) — быстро
     и без потери качества.
4. Чистые дорожки (en/ru) остаются в workdir — по ним возможна
   точечная авто-правка после QA (apply_qa_audio_fixes): в местах
   серьёзных искажений перевод приглушается, оригинал поднимается.

Настройки (env, числа):
    LIVEDUB_ORIG_VOLUME  — базовая громкость оригинала (0.45)
    LIVEDUB_TRANS_VOLUME — громкость перевода (1.3)
    LIVEDUB_DELAY_MS     — задержка перевода в мс (600)
Тумблеры (/settings → ENG Режим):
    livedub_pro_mix — использовать этот микс (вкл по умолчанию)
    livedub_autofix — авто-правка по результатам QA (вкл по умолчанию)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Числовые настройки из env (с безопасным парсом) ──────────────

def _env_float(name: str, default: float) -> float:
    try:
        v = float(os.getenv(name, "") or default)
        return v if 0.0 <= v <= 10.0 else default
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        v = int(os.getenv(name, "") or default)
        return v if 0 <= v <= 5000 else default
    except ValueError:
        return default


def get_mix_params() -> dict:
    return {
        "orig_volume": _env_float("LIVEDUB_ORIG_VOLUME", 0.45),
        "trans_volume": _env_float("LIVEDUB_TRANS_VOLUME", 1.3),
        "delay_ms": _env_int("LIVEDUB_DELAY_MS", 600),
    }


# ── Утилиты ──────────────────────────────────────────────────────

def parse_mmss(time_str: str) -> Optional[float]:
    """'MM:SS' / 'H:MM:SS' → секунды. None при мусоре."""
    if not time_str:
        return None
    m = re.match(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})\s*$", str(time_str))
    if not m:
        return None
    h = int(m.group(1) or 0)
    mm = int(m.group(2))
    ss = int(m.group(3))
    return float(h * 3600 + mm * 60 + ss)


def build_interval_volume_expr(intervals: list[tuple[float, float]],
                               inside: float, outside: float = 1.0) -> str:
    """Выражение для ffmpeg volume: inside в интервалах, outside вне.

    between() суммируются — сумма >0 работает как OR.
    """
    if not intervals:
        return str(outside)
    conds = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in intervals)
    return f"if(gt({conds},0),{inside},{outside})"


def _run_ffmpeg(args: list[str], timeout: int = 1800) -> tuple[bool, str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False, "ffmpeg не найден"
    kwargs: dict = {"capture_output": True, "text": True,
                    "encoding": "utf-8", "errors": "replace"}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run([ffmpeg, *args], timeout=timeout, **kwargs)
        if proc.returncode != 0:
            return False, (proc.stderr or "")[-600:]
        return True, ""
    except Exception as e:
        return False, str(e)[:300]


# ── Основной микс ────────────────────────────────────────────────

def build_mix_filter(orig_volume: float, trans_volume: float, delay_ms: int,
                     duck: bool = True,
                     ru_extra_expr: str = "", en_extra_expr: str = "") -> str:
    """Собирает filter_complex для микса EN-оригинала и RU-перевода.

    ru_extra_expr / en_extra_expr — дополнительные volume-выражения
    для точечной авто-правки по интервалам (QA-фиксы).
    """
    ru_chain = f"[1:a]adelay={delay_ms}:all=1,volume={trans_volume}"
    if ru_extra_expr:
        ru_chain += f",volume='{ru_extra_expr}':eval=frame"
    en_chain = f"[0:a]volume={orig_volume}"
    if en_extra_expr:
        en_chain += f",volume='{en_extra_expr}':eval=frame"

    if duck:
        return (
            f"{ru_chain}[ru0];"
            f"[ru0]asplit=2[ru1][ru2];"
            f"{en_chain}[en0];"
            f"[en0][ru1]sidechaincompress="
            f"threshold=0.06:ratio=6:attack=150:release=500[enduck];"
            f"[enduck][ru2]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
        )
    return (
        f"{ru_chain}[ru0];"
        f"{en_chain}[en0];"
        f"[en0][ru0]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
    )


async def mix_tracks(orig_video: Path, ru_audio: Path, out_path: Path,
                     ru_extra_expr: str = "", en_extra_expr: str = "") -> Optional[Path]:
    """Миксует оригинальное видео с дорожкой перевода. Видео не перекодируется."""
    p = get_mix_params()
    fc = build_mix_filter(
        p["orig_volume"], p["trans_volume"], p["delay_ms"],
        duck=True, ru_extra_expr=ru_extra_expr, en_extra_expr=en_extra_expr,
    )
    args = [
        "-i", str(orig_video), "-i", str(ru_audio),
        "-filter_complex", fc,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        "-y", str(out_path),
    ]
    loop = asyncio.get_running_loop()
    ok, err = await loop.run_in_executor(None, lambda: _run_ffmpeg(args))
    if not ok:
        logger.warning("[LiveDubMix] sidechain-микс не удался (%s) — пробую простой микс", err)
        fc2 = build_mix_filter(
            p["orig_volume"], p["trans_volume"], p["delay_ms"],
            duck=False, ru_extra_expr=ru_extra_expr, en_extra_expr=en_extra_expr,
        )
        args[3] = fc2
        ok, err = await loop.run_in_executor(None, lambda: _run_ffmpeg(args))
        if not ok:
            logger.warning("[LiveDubMix] простой микс тоже не удался: %s", err)
            return None
    if out_path.exists() and out_path.stat().st_size > 10240:
        return out_path
    return None


async def build_pro_dub(video_url: str, workdir: Path) -> Optional[Path]:
    """Полный pro-цикл: чистый RU-перевод + оригинал → собственный микс.

    Возвращает путь к готовому видео или None (вызывающий код сделает
    fallback на старый vot-cli --merge-video).
    Оставляет в workdir: original_video.* и RU-mp3 — для QA-автоправки.
    """
    from services.yandex_live_dub import get_live_dub_audio
    from services.eng_subtitles import download_original_video

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    ru_task = asyncio.create_task(get_live_dub_audio(video_url, workdir))
    orig_task = asyncio.create_task(download_original_video(video_url, workdir))

    ru_audio: Optional[Path] = None
    orig_video: Optional[Path] = None
    try:
        ru_audio = await ru_task
    except RuntimeError:
        orig_task.cancel()
        raise  # LIVEDUB_NOT_AVAILABLE и пр. — наверх, там общий fallback
    except Exception as e:
        logger.warning("[LiveDubMix] не получил RU-дорожку: %s", e)
        orig_task.cancel()
        return None
    try:
        orig_video = await orig_task
    except Exception as e:
        logger.warning("[LiveDubMix] не скачал оригинал: %s", e)
        return None

    out = workdir / "pro_dub.mp4"
    result = await mix_tracks(orig_video, ru_audio, out)
    if result:
        p = get_mix_params()
        logger.info(
            "[LiveDubMix] Pro-микс готов: orig=%.2f trans=%.2f delay=%dms duck=on",
            p["orig_volume"], p["trans_volume"], p["delay_ms"],
        )
    return result


def find_pro_tracks(workdir: Path) -> tuple[Optional[Path], Optional[Path]]:
    """Ищет сохранённые дорожки pro-микса (для QA-автоправки)."""
    workdir = Path(workdir)
    orig = None
    for f in workdir.glob("original_video.*"):
        orig = f
        break
    ru = None
    mp3s = sorted(workdir.glob("*.mp3"), key=lambda x: x.stat().st_mtime, reverse=True)
    for f in mp3s:
        if "original_audio" not in f.name and "_qa" not in f.name:
            ru = f
            break
    return orig, ru


# ── Авто-правка по результатам QA ────────────────────────────────

# Окно правки вокруг таймкода проблемы
_FIX_PRE = 0.5    # начать чуть раньше
_FIX_LEN = 6.0    # длительность окна
# Насколько глушим перевод и поднимаем оригинал внутри окна
_FIX_RU_GAIN = 0.15
_FIX_EN_BOOST = 2.2   # 0.45 * 2.2 ≈ 1.0 — оригинал в полный голос


def extract_fix_intervals(issues: list[dict], max_fixes: int = 6) -> list[tuple[float, float]]:
    """major-проблемы QA → интервалы (start, end) для авто-правки."""
    intervals: list[tuple[float, float]] = []
    for issue in issues or []:
        if str(issue.get("severity")) != "major":
            continue
        t = parse_mmss(str(issue.get("time") or ""))
        if t is None:
            continue
        intervals.append((max(0.0, t - _FIX_PRE), t - _FIX_PRE + _FIX_LEN))
        if len(intervals) >= max_fixes:
            break
    # слить пересекающиеся
    intervals.sort()
    merged: list[tuple[float, float]] = []
    for a, b in intervals:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


async def apply_qa_audio_fixes(workdir: Path, issues: list[dict]) -> Optional[Path]:
    """Пересобирает микс с точечными правками: в местах major-искажений
    перевод приглушается, оригинал выводится в полный голос.

    Видео не перекодируется (-c:v copy) — операция занимает секунды.
    Возвращает путь к исправленному видео или None.
    """
    intervals = extract_fix_intervals(issues)
    if not intervals:
        return None
    orig_video, ru_audio = find_pro_tracks(workdir)
    if not (orig_video and ru_audio and orig_video.exists() and ru_audio.exists()):
        logger.info("[LiveDubMix] авто-правка пропущена: чистые дорожки не сохранились")
        return None

    ru_expr = build_interval_volume_expr(intervals, inside=_FIX_RU_GAIN)
    en_expr = build_interval_volume_expr(intervals, inside=_FIX_EN_BOOST)
    out = Path(workdir) / "pro_dub_fixed.mp4"
    result = await mix_tracks(orig_video, ru_audio, out,
                              ru_extra_expr=ru_expr, en_extra_expr=en_expr)
    if result:
        logger.info("[LiveDubMix] авто-правка: %d интервал(ов) приглушено", len(intervals))
    return result
