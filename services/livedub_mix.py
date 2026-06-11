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
   • конец микса специально продлён на задержку + LIVEDUB_TAIL_MARGIN_MS,
     чтобы последняя русская фраза не обрывалась после сдвига;
   • видеопоток обычно копируется без перекодирования (-c:v copy), а у
     коротких роликов хвост может быть дозаморожен последним кадром.
4. Чистые дорожки (en/ru) остаются в workdir — по ним возможна
   точечная авто-правка после QA (apply_qa_audio_fixes): в местах
   серьёзных искажений перевод приглушается, оригинал поднимается.

Настройки (env, числа):
    LIVEDUB_ORIG_VOLUME  — базовая громкость оригинала (0.45)
    LIVEDUB_TRANS_VOLUME       — громкость перевода (1.3)
    LIVEDUB_DELAY_MS           — задержка перевода в мс (600)
    LIVEDUB_TAIL_MARGIN_MS     — запас в конце сверх задержки (1000)
    LIVEDUB_TAIL_FREEZE_MAX_SEC — до какой длины freeze-frame хвост (180)
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


def _env_int(name: str, default: int, max_value: int = 5000) -> int:
    try:
        v = int(os.getenv(name, "") or default)
        return v if 0 <= v <= max_value else default
    except ValueError:
        return default


def get_mix_params() -> dict:
    delay_ms = _env_int("LIVEDUB_DELAY_MS", 600)
    # ВАЖНО: RU-дорожка сдвигается adelay'ем. Без хвоста amix раньше
    # завершался по длине оригинала и съедал последние слова перевода
    # (типично в Shorts: «...вся Цер...»). Поэтому держим микс ещё
    # delay + небольшой запас. LIVEDUB_TAIL_MARGIN_MS=0 оставит только
    # компенсацию задержки; чтобы полностью убрать хвост — ставьте ещё и
    # LIVEDUB_DELAY_MS=0.
    tail_margin_ms = _env_int("LIVEDUB_TAIL_MARGIN_MS", 1000)
    tail_pad_ms = delay_ms + tail_margin_ms
    return {
        "orig_volume": _env_float("LIVEDUB_ORIG_VOLUME", 0.45),
        "trans_volume": _env_float("LIVEDUB_TRANS_VOLUME", 1.3),
        "delay_ms": delay_ms,
        "tail_margin_ms": tail_margin_ms,
        "tail_pad_ms": tail_pad_ms,
        # Для коротких роликов можно физически продлить и видеоряд: последний
        # кадр будет заморожен на хвост, чтобы Telegram/плеер точно не оборвал
        # ролик раньше аудио. 0 = не замораживать (аудиохвост всё равно есть).
        "tail_freeze_max_sec": _env_int("LIVEDUB_TAIL_FREEZE_MAX_SEC", 180, max_value=24 * 3600),
        # 1 = лёгкий voice-EQ (highpass 70 Гц на обеих дорожках: срезает
        # гул зала/рокот ниже речи, голос не задевает). 0 = выключить.
        "voice_eq": _env_int("LIVEDUB_VOICE_EQ", 1),
        # Путь к RNNoise-модели (.rnnn) — AI-шумодав ffmpeg arnndn для
        # EN-оригинала (гул зала/проектора в записях проповедей).
        # Модели: https://github.com/GregorR/rnnoise-models (cb.rnnn —
        # лучший универсал). Пусто = выключено. RU-дорожку Яндекса не
        # трогаем — она студийно чистая.
        "rnnoise_model": os.getenv("LIVEDUB_RNNOISE_MODEL", "").strip(),
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


# Целевая громкость диалога (EBU R128 / стриминговый стандарт)
_TARGET_LUFS = -16.0


_loudness_cache: dict = {}


def measure_loudness(path: Path, timeout: int = 600) -> Optional[float]:
    """Pass-1 loudnorm: возвращает integrated loudness (LUFS) дорожки.

    Используется для выравнивания дорожек ДО микса: громкость у Яндекса
    плавает от ролика к ролику, и фиксированные коэффициенты иначе дают
    разный баланс. None — если измерить не удалось.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    try:
        cache_key = (str(path), Path(path).stat().st_mtime_ns)
    except OSError:
        cache_key = None
    if cache_key is not None and cache_key in _loudness_cache:
        return _loudness_cache[cache_key]
    kwargs: dict = {"capture_output": True, "text": True,
                    "encoding": "utf-8", "errors": "replace"}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.run(
            [ffmpeg, "-i", str(path), "-vn",
             "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
             "-f", "null", "-"],
            timeout=timeout, **kwargs,
        )
        m = re.search(r'"input_i"\s*:\s*"(-?[\d.]+)"', proc.stderr or "")
        if m:
            v = float(m.group(1))
            if -70.0 <= v <= 0.0:
                if cache_key is not None:
                    if len(_loudness_cache) > 64:
                        _loudness_cache.clear()
                    _loudness_cache[cache_key] = v
                return v
    except Exception as e:
        logger.warning("[LiveDubMix] measure_loudness failed: %s", e)
    return None


def loudness_gain_db(input_lufs: Optional[float], target: float = _TARGET_LUFS) -> float:
    """Линейный gain (дБ) для приведения дорожки к target LUFS.

    Линейный gain не качает динамику (в отличие от single-pass loudnorm).
    Ограничен ±20 дБ от греха подальше.
    """
    if input_lufs is None:
        return 0.0
    return max(-20.0, min(20.0, target - input_lufs))


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


def probe_media_duration(path: Path, timeout: int = 60) -> Optional[float]:
    """Длительность медиафайла через ffprobe. None при любой проблеме."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        if proc.returncode == 0:
            dur = float((proc.stdout or "").strip() or "0")
            if dur > 0:
                return dur
    except Exception as e:
        logger.debug("[LiveDubMix] probe_media_duration failed: %s", e)
    return None


def _extend_filter_with_video_tail(audio_fc: str, tail_pad_ms: int) -> str:
    """К audio filter_complex добавляет freeze-frame хвост видеоряда."""
    if tail_pad_ms <= 0:
        return audio_fc
    return (
        f"{audio_fc};"
        f"[0:v]tpad=stop_mode=clone:stop_duration={tail_pad_ms / 1000.0:.3f}[vout]"
    )


def calculate_tail_pad_ms(delay_ms: int, tail_margin_ms: int,
                          orig_duration: Optional[float],
                          ru_duration: Optional[float]) -> int:
    """Сколько хвоста реально нужно после сдвига RU-дорожки.

    База = delay + margin. Но Яндекс иногда отдаёт RU-аудио чуть длиннее
    оригинала: если просто добавить фиксированные 1.6с, последняя фраза всё
    равно может оказаться за концом видеоряда. Поэтому считаем минимум как:
        max(0, ru_duration + delay - orig_duration) + margin.
    """
    base = max(0, int(delay_ms) + int(tail_margin_ms))
    if not (orig_duration and ru_duration and orig_duration > 0 and ru_duration > 0):
        return base
    delay_s = max(0.0, delay_ms / 1000.0)
    margin_s = max(0.0, tail_margin_ms / 1000.0)
    needed_s = max(0.0, ru_duration + delay_s - orig_duration) + margin_s
    needed_ms = int(needed_s * 1000.0 + 0.999)
    return max(base, needed_ms)


_FORBIDDEN_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_livedub_video_filename(title_line: str, fallback: str = "livedub") -> str:
    """Человеческое имя mp4 для Telegram/downloads: «Название - Автор.mp4».

    Telegram берёт basename локального файла. Если отправлять workdir/pro_dub.mp4,
    пользователь получает десятки одинаковых downloads/pro_dub.mp4. Здесь чистим
    Windows-запрещённые символы и режем длину, но сохраняем русский текст.
    """
    text = re.sub(r"<[^>]+>", " ", str(title_line or ""))
    text = text.replace("—", " - ").replace("–", " - ")
    text = _FORBIDDEN_FILENAME_CHARS_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" ._-")
    if not text:
        text = _FORBIDDEN_FILENAME_CHARS_RE.sub(" ", str(fallback or "livedub"))
        text = re.sub(r"\s+", " ", text).strip(" ._-") or "livedub"
    if text.upper() in _RESERVED_WINDOWS_NAMES:
        text = f"{text}_video"
    if len(text) > 140:
        text = text[:140].rstrip(" ._-")
    return f"{text}.mp4"


def prepare_livedub_send_path(video_path: Path, title_line: str,
                              fallback: str = "livedub") -> Path:
    """Переименовывает готовый mp4 перед Telegram-отправкой в понятное имя."""
    src = Path(video_path)
    if not src.exists():
        return src
    target = src.with_name(safe_livedub_video_filename(title_line, fallback=fallback))
    if src.resolve() == target.resolve():
        return src
    try:
        if target.exists():
            target.unlink()
        src.replace(target)
        logger.info("[LiveDubMix] send filename: %s", target.name)
        return target
    except Exception as e:
        logger.warning("[LiveDubMix] не удалось переименовать видео для отправки: %s", e)
        return src


# ── Основной микс ────────────────────────────────────────────────

def build_mix_filter(orig_volume: float, trans_volume: float, delay_ms: int,
                     duck: bool = True,
                     ru_extra_expr: str = "", en_extra_expr: str = "",
                     en_gain_db: float = 0.0, ru_gain_db: float = 0.0,
                     voice_eq: bool = True, rnnoise_model: str = "",
                     tail_pad_ms: int = 0) -> str:
    """Собирает filter_complex для микса EN-оригинала и RU-перевода.

    ru_extra_expr / en_extra_expr — дополнительные volume-выражения
    для точечной авто-правки по интервалам (QA-фиксы).
    en_gain_db / ru_gain_db — линейные поправки громкости (из loudnorm
    pass-1), выравнивают дорожки к -16 LUFS ДО применения коэффициентов:
    у Яндекса громкость перевода плавает от ролика к ролику.
    tail_pad_ms — конечный запас микса: EN-ветка дополняется тишиной,
    а amix работает до самой длинной дорожки. Это не даёт задержанному
    RU-переводу обрубиться на последнем слове.
    Финальный alimiter защищает сумму от клиппинга (вместо дефолтного
    normalize амикса, который глушил бы всё на -6 дБ).
    """
    _eq = "highpass=f=70," if voice_eq else ""
    # RNNoise только на EN (оригинал из зала); путь экранируем для ffmpeg
    _dn = ""
    if rnnoise_model and Path(rnnoise_model).exists():
        _m = rnnoise_model.replace("\\", "/").replace(":", "\\:")
        _dn = f"arnndn=m='{_m}',"
    ru_chain = f"[1:a]{_eq}adelay={delay_ms}:all=1"
    if abs(ru_gain_db) > 0.1:
        ru_chain += f",volume={ru_gain_db:.1f}dB"
    ru_chain += f",volume={trans_volume}"
    if ru_extra_expr:
        ru_chain += f",volume='{ru_extra_expr}':eval=frame"
    en_chain = f"[0:a]{_dn}{_eq}"
    if abs(en_gain_db) > 0.1:
        en_chain += f"volume={en_gain_db:.1f}dB,"
    en_chain += f"volume={orig_volume}"
    if en_extra_expr:
        en_chain += f",volume='{en_extra_expr}':eval=frame"
    if tail_pad_ms > 0:
        en_chain += f",apad=pad_dur={tail_pad_ms / 1000.0:.3f}"

    # alimiter: прозрачная защита от клиппинга суммы двух дорожек.
    # 2026-06-11: используем чуть более агрессивные параметры alimiter для
    # выравнивания пиков в громких проповедях.
    _limit = "alimiter=limit=0.92:attack=7:release=100:level=disabled"

    if duck:
        # level_sc=1 — сигнал-детектор без ослабления; release длинный,
        # чтобы оригинал «всплывал» плавно, как у живого звукорежиссёра.
        # Ratio=8 и attack=100 делают дакинг более уверенным.
        return (
            f"{ru_chain}[ru0];"
            f"[ru0]asplit=2[ru1][ru2];"
            f"{en_chain}[en0];"
            f"[en0][ru1]sidechaincompress="
            f"threshold=0.04:ratio=8:attack=100:release=800:level_sc=1[enduck];"
            f"[enduck][ru2]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
            f"{_limit}[aout]"
        )
    return (
        f"{ru_chain}[ru0];"
        f"{en_chain}[en0];"
        f"[en0][ru0]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
        f"{_limit}[aout]"
    )


async def mix_tracks(orig_video: Path, ru_audio: Path, out_path: Path,
                     ru_extra_expr: str = "", en_extra_expr: str = "") -> Optional[Path]:
    """Миксует оригинальное видео с дорожкой перевода.

    Перед миксом обе дорожки измеряются (loudnorm pass-1) и приводятся
    линейным gain к -16 LUFS — баланс EN/RU одинаков на любом ролике,
    независимо от того, как Яндекс отдал громкость в этот раз.

    Финал микса продлевается на delay + LIVEDUB_TAIL_MARGIN_MS. Для коротких
    роликов дополнительно замораживаем последний кадр, чтобы Telegram точно
    не закончил видео раньше, чем прозвучит последняя русская фраза.
    """
    p = get_mix_params()
    loop = asyncio.get_running_loop()
    en_lufs, ru_lufs, orig_duration, ru_duration = await asyncio.gather(
        loop.run_in_executor(None, lambda: measure_loudness(orig_video)),
        loop.run_in_executor(None, lambda: measure_loudness(ru_audio)),
        loop.run_in_executor(None, lambda: probe_media_duration(orig_video)),
        loop.run_in_executor(None, lambda: probe_media_duration(ru_audio)),
    )
    en_gain = loudness_gain_db(en_lufs)
    ru_gain = loudness_gain_db(ru_lufs)
    logger.info(
        "[LiveDubMix] loudness: EN=%s LUFS (gain %+.1f dB), RU=%s LUFS (gain %+.1f dB)",
        f"{en_lufs:.1f}" if en_lufs is not None else "?", en_gain,
        f"{ru_lufs:.1f}" if ru_lufs is not None else "?", ru_gain,
    )

    tail_pad_ms = calculate_tail_pad_ms(
        int(p.get("delay_ms") or 0),
        int(p.get("tail_margin_ms") or 0),
        orig_duration,
        ru_duration,
    )
    freeze_max = int(p.get("tail_freeze_max_sec") or 0)
    freeze_tail = bool(
        tail_pad_ms > 0
        and freeze_max > 0
        and orig_duration is not None
        and orig_duration <= freeze_max
    )

    def _audio_filter(*, duck: bool, rnnoise_model: str, tail: bool = True) -> str:
        return build_mix_filter(
            p["orig_volume"], p["trans_volume"], p["delay_ms"],
            duck=duck, ru_extra_expr=ru_extra_expr, en_extra_expr=en_extra_expr,
            en_gain_db=en_gain, ru_gain_db=ru_gain,
            voice_eq=bool(p["voice_eq"]),
            rnnoise_model=rnnoise_model,
            tail_pad_ms=tail_pad_ms if tail else 0,
        )

    def _make_args(audio_fc: str, *, freeze_video: bool) -> list[str]:
        fc = audio_fc
        video_args: list[str]
        if freeze_video:
            try:
                from services.ffmpeg import _get_video_encoder
                _enc, _q, _pr = _get_video_encoder()
                fc = _extend_filter_with_video_tail(audio_fc, tail_pad_ms)
                video_args = ["-map", "[vout]", "-map", "[aout]",
                              "-c:v", _enc, *_q, *_pr, "-pix_fmt", "yuv420p"]
            except Exception as _enc_err:
                logger.warning("[LiveDubMix] video-tail encoder unavailable: %s", _enc_err)
                video_args = ["-map", "0:v", "-map", "[aout]", "-c:v", "copy"]
        else:
            video_args = ["-map", "0:v", "-map", "[aout]", "-c:v", "copy"]
        args = [
            "-i", str(orig_video), "-i", str(ru_audio),
            "-filter_complex", fc,
            *video_args,
        ]
        args += [
            "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart",
            "-y", str(out_path),
        ]
        return args

    if tail_pad_ms > 0:
        logger.info(
            "[LiveDubMix] tail guard: audio +%.1fs%s (orig=%ss, ru=%ss)",
            tail_pad_ms / 1000.0,
            "; freeze last frame" if freeze_tail else "",
            f"{orig_duration:.1f}" if orig_duration else "?",
            f"{ru_duration:.1f}" if ru_duration else "?",
        )

    fc = _audio_filter(duck=True, rnnoise_model=p["rnnoise_model"])
    args = _make_args(fc, freeze_video=freeze_tail)
    ok, err = await loop.run_in_executor(None, lambda: _run_ffmpeg(args))
    if not ok and freeze_tail:
        logger.warning("[LiveDubMix] freeze-tail микс не удался (%s) — повтор без заморозки видео", err)
        args = _make_args(fc, freeze_video=False)
        ok, err = await loop.run_in_executor(None, lambda: _run_ffmpeg(args))
    if not ok:
        logger.warning("[LiveDubMix] sidechain-микс не удался (%s) — пробую простой микс", err)
        fc2 = _audio_filter(duck=False, rnnoise_model=p["rnnoise_model"])
        args = _make_args(fc2, freeze_video=False)
        ok, err = await loop.run_in_executor(None, lambda: _run_ffmpeg(args))
        if not ok and p["rnnoise_model"]:
            logger.warning("[LiveDubMix] микс с RNNoise не удался (%s) — повтор без шумодава", err)
            fc3 = _audio_filter(duck=True, rnnoise_model="")
            args = _make_args(fc3, freeze_video=False)
            ok, err = await loop.run_in_executor(None, lambda: _run_ffmpeg(args))
        if not ok and tail_pad_ms > 0:
            # Старый ffmpeg теоретически может не принять apad=pad_dur/tpad.
            # Лучше отдать видео без хвоста, чем сорвать LiveDub целиком.
            logger.warning("[LiveDubMix] микс с tail guard не удался (%s) — аварийно без хвоста", err)
            fc4 = _audio_filter(duck=True, rnnoise_model="", tail=False)
            args = _make_args(fc4, freeze_video=False)
            ok, err = await loop.run_in_executor(None, lambda: _run_ffmpeg(args))
        if not ok:
            logger.warning("[LiveDubMix] простой микс тоже не удался: %s", err)
            return None
    if out_path.exists() and out_path.stat().st_size > 10240:
        return out_path
    return None


async def build_pro_dub(video_url: str, workdir: Path, duration: float = 0.0, lang: str = "") -> Optional[Path]:
    """Полный pro-цикл: чистый RU-перевод + оригинал → собственный микс.

    Возвращает путь к готовому видео или None (вызывающий код сделает
    fallback на старый vot-cli --merge-video).
    Оставляет в workdir: original_video.* и RU-mp3 — для QA-автоправки.
    """
    from services.yandex_live_dub import get_live_dub_audio
    from services.eng_subtitles import download_original_video

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    ru_task = asyncio.create_task(get_live_dub_audio(video_url, workdir, duration=duration, lang=lang))
    orig_task = asyncio.create_task(download_original_video(video_url, workdir))

    ru_audio: Optional[Path] = None
    orig_video: Optional[Path] = None
    async def _drain(task):
        """Гасим отменённую задачу, чтобы не было 'exception was never retrieved'."""
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    try:
        ru_audio = await ru_task
    except RuntimeError as _live_err:
        # ROUND 39: настоящая причина отказов — Яндекс требует OAuth-токен
        # для живых голосов (SESSION_REQUIRED). Маркер .auth_required
        # позволяет пайплайну подсказать юзеру про VOT_API_TOKEN.
        if "LIVEDUB_AUTH_REQUIRED" in str(_live_err):
            try:
                (workdir / ".auth_required").write_text("1", encoding="utf-8")
            except OSError:
                pass

        # Принципиально: режим ENG — это ТОЛЬКО «Живые голоса».
        # Обычные TTS-голоса больше НЕ включаются по умолчанию: иначе бот
        # врёт пользователю и маскирует настоящую проблему авторизации.
        # Если когда-нибудь понадобится аварийный резерв — только явный opt-in.
        tts_fallback_enabled = (
            os.getenv("LIVEDUB_TTS_FALLBACK", "0").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        if (("LIVEDUB_NOT_AVAILABLE" in str(_live_err)
                or "LIVEDUB_AUTH_REQUIRED" in str(_live_err))
                and tts_fallback_enabled):
            logger.info("[LiveDubMix] Живые голоса недоступны — LIVEDUB_TTS_FALLBACK=1, пробую обычные (tts)")
            try:
                ru_audio = await get_live_dub_audio(video_url, workdir, voice_style="tts", duration=duration)
                logger.info("[LiveDubMix] Перевод получен обычными голосами (tts)")
                # маркер для caption: перевод не «Живые голоса», а обычные
                try:
                    (workdir / ".voice_style_tts").write_text("1", encoding="utf-8")
                except OSError:
                    pass
            except Exception as _tts_err:
                logger.info("[LiveDubMix] tts тоже недоступен: %s", str(_tts_err)[:120])
                await _drain(orig_task)
                raise _live_err from None
        else:
            await _drain(orig_task)
            raise  # LIVEDUB_AUTH_REQUIRED/LIVEDUB_NOT_AVAILABLE — наверх
    except Exception as e:
        logger.warning("[LiveDubMix] не получил RU-дорожку: %s", e)
        await _drain(orig_task)
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
            "[LiveDubMix] Pro-микс готов: orig=%.2f trans=%.2f delay=%dms base_tail=%dms duck=on",
            p["orig_volume"], p["trans_volume"], p["delay_ms"], p.get("tail_pad_ms", 0),
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


# ── Метаданные для Telegram-отправки ─────────────────────────────

def probe_video_meta(path: Path) -> dict:
    """width/height/duration для send_video.

    Для больших видео (>10 МБ) Telegram НЕ анализирует файл сам:
    без этих полей превью квадратное, длительность 0:00, стриминг
    не работает до полного скачивания (SO #71844601).
    """
    ffprobe = shutil.which("ffprobe")
    meta = {"width": None, "height": None, "duration": None}
    if not ffprobe:
        # Fallback: ffmpeg -i пишет метаданные в stderr (на Windows ffmpeg
        # нередко установлен без ffprobe — метаданные молча терялись)
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return meta
        try:
            kwargs: dict = {"capture_output": True, "text": True,
                            "encoding": "utf-8", "errors": "replace"}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            proc = subprocess.run([ffmpeg, "-i", str(path)], timeout=60, **kwargs)
            err = proc.stderr or ""
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)", err)
            if m:
                meta["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            m = re.search(r"Video:.*?\s(\d{2,5})x(\d{2,5})", err)
            if m:
                meta["width"], meta["height"] = int(m.group(1)), int(m.group(2))
        except Exception as e:
            logger.warning("[LiveDubMix] ffmpeg-fallback meta: %s", e)
        return meta
    try:
        import json as _json
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        data = _json.loads(proc.stdout or "{}")
        streams = data.get("streams") or [{}]
        meta["width"] = streams[0].get("width")
        meta["height"] = streams[0].get("height")
        dur = (data.get("format") or {}).get("duration")
        if dur:
            meta["duration"] = int(float(dur))
    except Exception as e:
        logger.warning("[LiveDubMix] probe_video_meta: %s", e)
    return meta


def make_video_thumbnail(video_path: Path, at_sec: float = 3.0) -> Optional[Path]:
    """JPEG-превью <=320px для send_video (Telegram: max 320, max 200KB)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    out = Path(video_path).with_suffix(".thumb.jpg")
    try:
        kwargs: dict = {"capture_output": True}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        proc = subprocess.run(
            [ffmpeg, "-ss", str(at_sec), "-i", str(video_path),
             "-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "5",
             "-y", str(out)],
            timeout=60, **kwargs,
        )
        if proc.returncode == 0 and out.exists() and 0 < out.stat().st_size < 200 * 1024:
            return out
        out.unlink(missing_ok=True)
    except Exception as e:
        logger.warning("[LiveDubMix] thumbnail: %s", e)
    return None


# ── Авто-правка по результатам QA ────────────────────────────────

# Окно правки вокруг таймкода проблемы
_FIX_PRE = 0.5    # начать чуть раньше
_FIX_LEN = 6.0    # длительность окна
# Насколько глушим перевод и поднимаем оригинал внутри окна.
# Роковые major-ошибки (пример: склейка "родители" + sexual immorality)
# нужно именно вырезать из RU-дубляжа, а не оставлять слышимыми на 15%.
_FIX_RU_GAIN = _env_float("LIVEDUB_AUTOFIX_RU_VOLUME", 0.0)
_FIX_EN_BOOST = _env_float("LIVEDUB_AUTOFIX_EN_BOOST", 2.2)   # 0.45 * 2.2 ≈ 1.0


def extract_fix_intervals(issues: list[dict], max_fixes: int = 6) -> list[tuple[float, float]]:
    """major-проблемы QA → интервалы (start, end) для авто-правки."""
    # Таймкоды QA приходят из SRT Яндекса (без задержки) или из дубляжа
    # (с задержкой) — компенсируем сдвиг перевода в миксе, расширяя окно.
    delay_s = get_mix_params()["delay_ms"] / 1000.0
    intervals: list[tuple[float, float]] = []
    for issue in issues or []:
        if str(issue.get("severity")) != "major":
            continue
        t = parse_mmss(str(issue.get("time") or ""))
        if t is None:
            continue
        intervals.append((max(0.0, t - _FIX_PRE),
                          t - _FIX_PRE + _FIX_LEN + delay_s))
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
    RU-дубляж по умолчанию полностью вырезается, оригинал выводится в полный голос.

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
        # FIX 2026-06-10: короткие ролики (<3 мин) шлются с ВШИТЫМИ
        # русскими субтитрами (gemini_subs.srt); пересборка из чистых
        # дорожек их теряла — фикс-версия выходила хуже оригинальной.
        srt = Path(workdir) / "gemini_subs.srt"
        if srt.exists() and srt.stat().st_size > 50:
            try:
                from services.eng_subtitles import merge_subtitles
                with_subs = await merge_subtitles(result, srt, is_fallback=False)
                if with_subs and Path(with_subs).exists():
                    logger.info("[LiveDubMix] авто-правка: субтитры возвращены в фикс-версию")
                    return Path(with_subs)
            except Exception as _sub_err:
                logger.warning("[LiveDubMix] авто-правка: не удалось вернуть сабы: %s", _sub_err)
    return result
