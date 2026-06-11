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

Тебе дан ОРИГИНАЛ (английская речь, аудиофайл) и русский ДУБЛЯЖ этого
материала (машинный перевод Яндекса) — как второй аудиофайл и/или как
точный текст с таймкодами ниже.

Твоя задача — найти места, где русский дубляж ИСКАЖАЕТ СМЫСЛ оригинала.

Особое внимание — теологическим терминам, которые машинный перевод часто портит:
justification (оправдание), sanctification (освящение), atonement (искупление),
providence (провидение), covenant (завет), grace (благодать), righteousness (праведность),
repentance (покаяние), congregation (община), Scripture (Писание) и подобным.

НЕ придирайся к стилистике, перестановке слов и естественным упрощениям —
отмечай только реальные искажения смысла, пропуски важных утверждений
и неверно переведённые термины.

КРАСНЫЕ ФЛАГИ (severity=major всегда):
- перевод склеил две соседние мысли так, что появился новый грех/обвинение,
  которого в оригинале нет;
- особенно опасны семейные/сексуальные склейки: например, если оригинал говорит
  отдельно «fools bring grief to their parents» и отдельно «fools commit sexual
  immorality», а русский дубляж связывает sexual immorality с родителями — это
  грубейшее искажение, обязательно пометь major;
- любые добавленные обвинения в инцесте, прелюбодеянии с родственниками,
  насилии, богохульстве или ереси, которых нет в оригинале, — major.

{reference_block}

ВАЖНО: если ты не уверен, что искажение реально есть — НЕ включай его.
Ложная тревога хуже пропуска: пользователь получит «исправление» хорошего места.

Ответь СТРОГО в формате JSON без пояснений вокруг.
Поле reasoning заполняй ПЕРВЫМ — сначала рассуждение, потом оценка:
{{
  "reasoning": "<2-4 предложения: как ты сравнивал, что заметил в целом>",
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


def srt_to_timed_text(srt_path: Path, max_chars: int = 12000) -> str:
    """SRT → компактный текст «[MM:SS] реплика» для передачи в Gemini.

    Точный текст того, что озвучивает Яндекс, повышает точность QA:
    модель цитирует реальные фразы перевода вместо распознавания на слух.
    """
    try:
        raw = Path(srt_path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    out: list[str] = []
    for block in re.split(r"\n\s*\n", raw):
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        ts_idx = 1 if lines[0].isdigit() else 0
        m = re.match(r"(\d{2}):(\d{2}):(\d{2})[,.]\d{3}\s*-->", lines[ts_idx]) if ts_idx < len(lines) else None
        if not m:
            continue
        h, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3))
        total_m = h * 60 + mm
        text_lines = lines[ts_idx + 1:]
        if not text_lines:
            continue
        out.append(f"[{total_m:02d}:{ss:02d}] " + " ".join(text_lines))
        if sum(len(x) for x in out) > max_chars:
            break
    return "\n".join(out)


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
    dub_srt_path: Optional[Path] = None,
    existing_audio_part=None,
    existing_client=None,
    thinking_level: str = "high",
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
    _temp_original_audio: Path | None = None
    try:
        _have_srt = bool(dub_srt_path and Path(dub_srt_path).exists())
        dub_audio = None
        if not _have_srt:
            # Аудио дубляжа нужно только когда нет официального текста перевода
            dub_audio = await asyncio.get_running_loop().run_in_executor(
                None, lambda: _extract_audio_for_qa(dub_video_path, qa_audio)
            )
            if dub_audio is None:
                logger.warning("[LiveDubQA] не удалось извлечь аудио дубляжа")
                return None
        else:
            logger.info("[LiveDubQA] есть SRT перевода — сравниваю EN-аудио с текстом (без извлечения дубляжа)")

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
            if not ref_lines and not _have_srt:
                logger.info("[LiveDubQA] нет ни оригинала, ни анализа — проверка невозможна")
                return None
            if not ref_lines:
                # Есть только SRT дубляжа без какого-либо эталона оригинала —
                # сравнивать не с чем, нужен хотя бы дубляж-аудио против EN.
                logger.info("[LiveDubQA] нет эталона оригинала — проверка пропущена")
                return None
            reference_block = (
                "Оригинальное аудио недоступно. Вместо него используй этот проверенный "
                "конспект оригинала как эталон смысла:\n" + "\n".join(ref_lines)
            )

        dub_text_block = ""
        if dub_srt_path and Path(dub_srt_path).exists():
            timed = srt_to_timed_text(dub_srt_path)
            if timed:
                dub_text_block = (
                    "\n\nТОЧНЫЙ ТЕКСТ русского дубляжа (официальные субтитры перевода "
                    "Яндекса с таймкодами — цитируй поле heard ИЗ НЕГО, таймкоды бери отсюда):\n"
                    + timed
                )
                logger.info("[LiveDubQA] использую текст перевода из SRT (%d строк)",
                            timed.count("\n") + 1)

        prompt = _QA_PROMPT.format(
            reference_block=(reference_block or
            "Первый файл — ОРИГИНАЛ (англ.), второй — ДУБЛЯЖ (рус.). Сравнивай их напрямую.")
            + dub_text_block
        )

        async def _attempt(client):
            nonlocal client_used
            client_used = client
            parts = []
            # PERF 2026-06-10: оригинал уже залит в Gemini основным анализом
            # (used_audio_part жив до finally) — реюзим вместо повторной
            # заливки 20-50МБ mp3. Files API привязан к ключу: реюз только
            # на том же клиенте; не добавляем в uploaded (удалит пайплайн).
            if (existing_audio_part is not None and existing_client is client
                    and "ACTIVE" in str(getattr(existing_audio_part, "state", ""))):
                logger.info("[LiveDubQA] реюз audio_part основного анализа (без повторной заливки)")
                parts.append(existing_audio_part)
            elif original_audio_path and Path(original_audio_path).exists():
                _orig_path = Path(original_audio_path)
                _upload_orig = _orig_path
                if _orig_path.suffix.lower() not in {".mp3", ".mpeg", ".mpga"}:
                    _tmp = _orig_path.parent / f"{_orig_path.stem}_qa_original.mp3"
                    _extracted = await asyncio.get_running_loop().run_in_executor(
                        None, lambda: _extract_audio_for_qa(_orig_path, _tmp)
                    )
                    if _extracted is None:
                        logger.warning("[LiveDubQA] не удалось извлечь оригинальное аудио для QA")
                        return None
                    _temp_original_audio = Path(_extracted)
                    _upload_orig = _temp_original_audio
                uf_orig = await _upload_and_wait(client, _upload_orig, "qa_original")
                uploaded.append(uf_orig)
                parts.append(uf_orig)
            if dub_audio is not None:
                uf_dub = await _upload_and_wait(client, dub_audio, "qa_dub")
                uploaded.append(uf_dub)
                parts.append(uf_dub)
            # PROD-FIX (лог 2026-06-10): свой конфиг наступил на 2 мины:
            # 1) audio_timestamp не поддерживается Gemini API (ошибка на ВЫЗОВЕ,
            #    не на конструкторе) — это уже задокументировано в make_audio_config;
            # 2) max_output_tokens=4096 съедался thinking-токенами 3.x (в проде
            #    thoughts=6-8K!) — JSON обрезался в ноль на ВСЕХ 4 ключах.
            # Используем общий боевой make_audio_config: правильный thinking-бюджет,
            # без audio_timestamp, c нативным JSON-mime.
            from core.globals import make_audio_config
            cfg = make_audio_config(
                max_output_tokens=49152,   # high-thinking может съесть до ~30K до ответа
                model_name=model_name,
                thinking_level=thinking_level,  # Full=high; Quick QA can use minimal/low
                response_mime_type="application/json",
            )
            try:
                resp = await client.aio.models.generate_content(
                    model=model_name,
                    contents=parts + [prompt],
                    config=cfg,
                )
            except Exception as _je:
                # Fallback: тот же конфиг, но без JSON-mime (текст распарсим сами)
                logger.info("[LiveDubQA] JSON-mime недоступен (%s) — повтор в текстовом режиме",
                            str(_je)[:120])
                resp = await client.aio.models.generate_content(
                    model=model_name,
                    contents=parts + [prompt],
                    config=make_audio_config(
                        max_output_tokens=49152,
                        model_name=model_name,
                        thinking_level="high",
                    ),
                )
            return resp

        last_err = None
        _qa_deadline = asyncio.get_running_loop().time() + _QA_TOTAL_TIMEOUT
        _clients_order = list(GEMINI_CLIENTS)
        if existing_client is not None and existing_client in _clients_order:
            _clients_order.remove(existing_client)
            _clients_order.insert(0, existing_client)
        for client in _clients_order:
            _left = _qa_deadline - asyncio.get_running_loop().time()
            if _left < 45:
                logger.warning("[LiveDubQA] общий бюджет времени исчерпан — стоп ротации ключей")
                break
            try:
                resp = await asyncio.wait_for(_attempt(client), timeout=_left)
                _raw_text = getattr(resp, "text", "") or ""
                result = _parse_qa_json(_raw_text)
                if result is not None and "issues" in result:
                    return result
                # Диагностика вместо немого фейла (прод 2026-06-10)
                try:
                    _cand = (getattr(resp, "candidates", None) or [None])[0]
                    _fr = getattr(_cand, "finish_reason", "?")
                    _um = getattr(resp, "usage_metadata", None)
                    logger.warning(
                        "[LiveDubQA] не распарсился: finish=%s thoughts=%s out=%s text_head=%r",
                        _fr,
                        getattr(_um, "thoughts_token_count", "?"),
                        getattr(_um, "candidates_token_count", "?"),
                        _raw_text[:160],
                    )
                except Exception:
                    pass
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
        if _temp_original_audio is not None:
            try:
                _temp_original_audio.unlink(missing_ok=True)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════
#  3. Форматирование отчёта
# ══════════════════════════════════════════════════════════════

def format_qa_report(qa: dict, video_url: str = "") -> str:
    """Собирает HTML-сообщение с результатом проверки перевода.

    video_url: если задан — таймкоды проблем становятся кликабельными
    ссылками на момент видео (youtu.be?t=N), юзер сразу прыгает к месту.
    """
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

    def _ts_link(t_raw: str) -> str:
        t_esc = html_mod.escape(t_raw)
        if not video_url:
            return f"<b>{t_esc}</b>"
        from services.livedub_mix import parse_mmss
        secs = parse_mmss(t_raw)
        if secs is None:
            return f"<b>{t_esc}</b>"
        sep = "&" if "?" in video_url else "?"
        href = html_mod.escape(f"{video_url}{sep}t={int(secs)}", quote=True)
        return f'<a href="{href}"><b>{t_esc}</b></a>'

    def _fmt(issue: dict, icon: str) -> str:
        t = _ts_link(str(issue.get("time") or "—"))
        heard = html_mod.escape(str(issue.get("heard") or "")[:120])
        should = html_mod.escape(str(issue.get("should_be") or "")[:120])
        problem = html_mod.escape(str(issue.get("problem") or "")[:160])
        parts = [f"{icon} {t} — {problem}"]
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
