#!/usr/bin/env python3
"""
LiveDub QA — проверка качества перевода «Живые голоса».

Два уровня проверки:
1. technical_check() — быстрые ffprobe-проверки целостности файла
   (длительность совпадает с оригиналом, аудиопоток существует).
   Дёшево, выполняется всегда перед отправкой.
2. run_translation_qa() — смысловая проверка через Gemini:
   модель получает ОБА аудио (английский оригинал + русский дубляж)
   и сравнивает напрямую, находя искажения смысла с таймкодами.
   Выполняется только в режиме ENG Full при включённой настройке
   livedub_qa (см. /settings → «🇬🇧 ENG Режим»).
"""
from __future__ import annotations

import asyncio
import html as html_mod
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from core.globals import HAS_GEMINI, GEMINI_CLIENTS

try:
    from google.genai import types  # type: ignore
except Exception:  # pragma: no cover
    types = None  # type: ignore

logger = logging.getLogger(__name__)

# Максимальное время на весь QA-проход (upload обоих файлов + генерация)
_QA_TOTAL_TIMEOUT = 420
# Максимальное ожидание обработки одного файла на стороне Gemini
_QA_UPLOAD_WAIT = 180
# Допустимое расхождение длительности дубляжа с оригиналом
_DURATION_TOLERANCE = 0.05  # 5%


# ══════════════════════════════════════════════════════════════
#  1. Технические проверки (ffprobe)
# ══════════════════════════════════════════════════════════════

def _ffprobe_json(path: Path) -> Optional[dict]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,codec_name,bit_rate",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout)
    except Exception as e:
        logger.warning("[LiveDubQA] ffprobe failed: %s", e)
        return None


def technical_check(dub_path: Path, expected_duration: int) -> list[str]:
    """Быстрые проверки целостности переведённого видео.

    Возвращает список предупреждений (пустой = всё в порядке).
    Не бросает исключений — QA не должен ломать отправку.
    """
    warnings: list[str] = []
    info = _ffprobe_json(dub_path)
    if info is None:
        # ffprobe нет или файл не парсится — для нечитаемого файла это важно
        if shutil.which("ffprobe"):
            warnings.append("файл не читается ffprobe — возможно, загрузка оборвалась")
        return warnings

    # 1. Длительность
    try:
        dub_duration = float(info.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        dub_duration = 0.0
    if expected_duration > 0 and dub_duration > 0:
        diff = abs(dub_duration - expected_duration) / expected_duration
        if diff > _DURATION_TOLERANCE:
            warnings.append(
                f"длительность перевода {dub_duration:.0f}с отличается от оригинала "
                f"{expected_duration}с на {diff * 100:.0f}% — перевод может быть неполным"
            )

    # 2. Аудиопоток
    streams = info.get("streams") or []
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    has_video = any(s.get("codec_type") == "video" for s in streams)
    if not has_audio:
        warnings.append("в файле нет аудиодорожки — перевод не наложился")
    if not has_video:
        warnings.append("в файле нет видеопотока")

    return warnings


# ══════════════════════════════════════════════════════════════
#  2. Смысловая проверка через Gemini
# ══════════════════════════════════════════════════════════════

_QA_PROMPT = """Ты — профессиональный редактор русского дубляжа христианских проповедей и лекций.

Тебе даны два аудиофайла:
1. ОРИГИНАЛ — английская речь.
2. ДУБЛЯЖ — русская озвучка этого же материала (машинный перевод Яндекса).

Твоя задача — найти места, где русский дубляж ИСКАЖАЕТ СМЫСЛ оригинала.

Особое внимание — теологическим терминам, которые машинный перевод часто портит:
justification (оправдание), sanctification (освящение), atonement (искупление),
providence (провидение), covenant (завет), grace (благодать), righteousness (праведность),
repentance (покаяние), congregation (община), Scripture (Писание) и подобным.

НЕ придирайся к стилистике, перестановке слов и естественным упрощениям —
отмечай только реальные искажения смысла, пропуски важных утверждений
и неверно переведённые термины.

{reference_block}

Ответь СТРОГО в формате JSON без пояснений вокруг:
{{
  "score": <целое 0-100, общая точность перевода>,
  "verdict": "<одно предложение: общая оценка качества>",
  "issues": [
    {{
      "time": "MM:SS",
      "heard": "<что звучит в русском дубляже>",
      "problem": "<в чём искажение>",
      "should_be": "<как правильно>",
      "severity": "minor|major"
    }}
  ]
}}

Если перевод точный и проблем нет — верни "issues": [].
Максимум 10 наиболее важных проблем, отсортируй по severity (major первыми).
"""


def _extract_audio_for_qa(video_path: Path, out_path: Path) -> Optional[Path]:
    """Извлекает аудио из переведённого видео в компактный mp3 для Gemini."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    try:
        proc = subprocess.run(
            [ffmpeg, "-i", str(video_path), "-vn", "-acodec", "libmp3lame",
             "-b:a", "48k", "-ac", "1", "-y", str(out_path)],
            capture_output=True, timeout=600,
        )
        if proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 1024:
            return out_path
    except Exception as e:
        logger.warning("[LiveDubQA] audio extract failed: %s", e)
    return None


async def _upload_and_wait(client, path: Path, display_name: str):
    """Загружает файл в Gemini Files API и ждёт окончания обработки."""
    uf = await client.aio.files.upload(
        file=path,
        config=types.UploadFileConfig(mime_type="audio/mpeg", display_name=display_name),
    )
    loop = asyncio.get_running_loop()
    start = loop.time()
    while uf.state == "PROCESSING":
        if loop.time() - start > _QA_UPLOAD_WAIT:
            raise TimeoutError(f"Gemini file processing timeout ({_QA_UPLOAD_WAIT}s)")
        await asyncio.sleep(3)
        uf = await client.aio.files.get(name=uf.name)
    if uf.state == "FAILED":
        raise RuntimeError("Gemini file processing FAILED")
    return uf


def _parse_qa_json(text: str) -> Optional[dict]:
    """Достаёт JSON из ответа модели (терпимо к ```json-обёрткам)."""
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    return data


async def run_translation_qa(
    dub_video_path: Path,
    original_audio_path: Optional[Path],
    ai_data: Optional[dict],
    duration: int,
    model_name: str = "",
) -> Optional[dict]:
    """Смысловая проверка дубляжа через Gemini.

    Возвращает dict {"score", "verdict", "issues"} или None при сбое.
    Никогда не бросает исключений наружу.
    """
    if not (HAS_GEMINI and GEMINI_CLIENTS and types is not None):
        logger.info("[LiveDubQA] Gemini недоступен — смысловая проверка пропущена")
        return None
    if not model_name:
        from core.database import GEMINI_MODEL
        model_name = GEMINI_MODEL

    qa_audio = dub_video_path.parent / f"{dub_video_path.stem}_qa.mp3"
    uploaded: list = []
    client_used = None
    try:
        dub_audio = await asyncio.get_running_loop().run_in_executor(
            None, lambda: _extract_audio_for_qa(dub_video_path, qa_audio)
        )
        if dub_audio is None:
            logger.warning("[LiveDubQA] не удалось извлечь аудио дубляжа")
            return None

        # Референс: если оригинального аудио нет — используем готовый анализ
        if original_audio_path and Path(original_audio_path).exists():
            reference_block = ""
        else:
            ref_lines = []
            if ai_data:
                if ai_data.get("main_topic"):
                    ref_lines.append(f"Тема: {ai_data['main_topic']}")
                ts = ai_data.get("timestamps")
                if isinstance(ts, list):
                    ref_lines.extend(str(t) for t in ts[:40])
                elif isinstance(ts, str):
                    ref_lines.append(ts[:4000])
            if not ref_lines:
                logger.info("[LiveDubQA] нет ни оригинала, ни анализа — проверка невозможна")
                return None
            reference_block = (
                "Оригинальное аудио недоступно. Вместо него используй этот проверенный "
                "конспект оригинала как эталон смысла:\n" + "\n".join(ref_lines)
            )

        prompt = _QA_PROMPT.format(
            reference_block=reference_block or
            "Первый файл — ОРИГИНАЛ (англ.), второй — ДУБЛЯЖ (рус.). Сравнивай их напрямую."
        )

        async def _attempt(client):
            nonlocal client_used
            client_used = client
            parts = []
            if original_audio_path and Path(original_audio_path).exists():
                uf_orig = await _upload_and_wait(client, Path(original_audio_path), "qa_original")
                uploaded.append(uf_orig)
                parts.append(uf_orig)
            uf_dub = await _upload_and_wait(client, dub_audio, "qa_dub")
            uploaded.append(uf_dub)
            parts.append(uf_dub)
            resp = await client.aio.models.generate_content(
                model=model_name,
                contents=parts + [prompt],
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=4096,
                ),
            )
            return resp

        last_err = None
        for client in GEMINI_CLIENTS:
            try:
                resp = await asyncio.wait_for(_attempt(client), timeout=_QA_TOTAL_TIMEOUT)
                result = _parse_qa_json(getattr(resp, "text", "") or "")
                if result is not None and "issues" in result:
                    return result
                last_err = RuntimeError("ответ модели не распарсился в QA-JSON")
            except Exception as e:
                last_err = e
                logger.warning("[LiveDubQA] клиент не справился: %s", str(e)[:200])
                # очистка залитых файлов перед следующим ключом
                for uf in uploaded:
                    try:
                        await client.aio.files.delete(name=uf.name)
                    except Exception:
                        pass
                uploaded.clear()
                continue
        logger.warning("[LiveDubQA] все клиенты исчерпаны: %s", str(last_err)[:200])
        return None
    except Exception as e:
        logger.warning("[LiveDubQA] неожиданный сбой: %s", e)
        return None
    finally:
        for uf in uploaded:
            try:
                if client_used is not None:
                    await client_used.aio.files.delete(name=uf.name)
            except Exception:
                pass
        try:
            qa_audio.unlink(missing_ok=True)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════
#  3. Форматирование отчёта
# ══════════════════════════════════════════════════════════════

def format_qa_report(qa: dict) -> str:
    """Собирает HTML-сообщение с результатом проверки перевода."""
    score = qa.get("score")
    verdict = str(qa.get("verdict") or "").strip()
    issues = qa.get("issues") or []

    if isinstance(score, (int, float)) and score >= 95 and not issues:
        head = f"✅ <b>Проверка перевода: {score:.0f}/100</b>"
    elif isinstance(score, (int, float)):
        head = f"🔍 <b>Проверка перевода: {score:.0f}/100</b>"
    else:
        head = "🔍 <b>Проверка перевода</b>"

    lines = [head]
    if verdict:
        lines.append(html_mod.escape(verdict))

    majors = [i for i in issues if str(i.get("severity")) == "major"]
    minors = [i for i in issues if str(i.get("severity")) != "major"]

    def _fmt(issue: dict, icon: str) -> str:
        t = html_mod.escape(str(issue.get("time") or "—"))
        heard = html_mod.escape(str(issue.get("heard") or "")[:120])
        should = html_mod.escape(str(issue.get("should_be") or "")[:120])
        problem = html_mod.escape(str(issue.get("problem") or "")[:160])
        parts = [f"{icon} <b>{t}</b> — {problem}"]
        if heard:
            parts.append(f"    Звучит: «{heard}»")
        if should:
            parts.append(f"    Верно: «{should}»")
        return "\n".join(parts)

    if majors:
        lines.append("")
        lines.append("<b>Серьёзные искажения:</b>")
        lines.extend(_fmt(i, "🔴") for i in majors[:5])
    if minors:
        lines.append("")
        lines.append("<b>Мелкие неточности:</b>")
        lines.extend(_fmt(i, "🟡") for i in minors[:5])
    if not issues:
        lines.append("Искажений смысла не найдено — перевод можно публиковать.")

    text = "\n".join(lines)
    return text[:4000]
