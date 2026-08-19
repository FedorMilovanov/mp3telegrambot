#!/usr/bin/env python3
"""
LiveDub QA — проверка качества перевода «Живые голоса».

Два уровня проверки:
1. technical_check() — быстрые ffprobe-проверки целостности файла.
2. run_translation_qa() — смысловая проверка через Gemini по оригиналу и дубляжу.

Gemini transport remains source-owned here, but all expensive Files/inference
operations use the shared capacity gate and one initial+2 budget per failure
domain. API-key count therefore cannot multiply one 503 event.
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

from core.globals import (
    HAS_GEMINI,
    GEMINI_CLIENTS,
    is_overload_error,
    is_quota_error,
)
from services import gemini_capacity_control as capacity_control

try:
    from google.genai import types  # type: ignore
except Exception:  # pragma: no cover
    types = None  # type: ignore

logger = logging.getLogger(__name__)

_QA_TOTAL_TIMEOUT = 420
_QA_UPLOAD_WAIT = 180
_DURATION_TOLERANCE = 0.05


def _ffprobe_json(path: Path) -> Optional[dict]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name,bit_rate",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout)
    except Exception as exc:
        logger.warning("[LiveDubQA] ffprobe failed: %s", exc)
        return None


def _mean_volume_db(
    path: Path,
    start: float = 0.0,
    dur: float = 120.0,
) -> Optional[float]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    try:
        cmd = [ffmpeg, "-hide_banner"]
        if start and start > 0:
            cmd += ["-ss", str(int(start))]
        cmd += [
            "-t",
            str(int(dur)),
            "-i",
            str(path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        match = re.search(
            r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB",
            proc.stderr or "",
        )
        if match:
            return float(match.group(1))
    except Exception as exc:
        logger.warning("[LiveDubQA] volumedetect failed: %s", exc)
    return None


def technical_check(dub_path: Path, expected_duration: int) -> list[str]:
    warnings: list[str] = []
    info = _ffprobe_json(dub_path)
    if info is None:
        if shutil.which("ffprobe"):
            warnings.append(
                "файл не читается ffprobe — возможно, загрузка оборвалась"
            )
        return warnings

    try:
        dub_duration = float(info.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        dub_duration = 0.0
    if expected_duration > 0 and dub_duration > 0:
        allowed_extra = 0.0
        try:
            from services.livedub_mix import get_mix_params

            allowed_extra = (
                (get_mix_params().get("tail_pad_ms") or 0) / 1000.0 + 0.75
            )
        except Exception:
            allowed_extra = 0.0
        delta = dub_duration - expected_duration
        diff = abs(delta) / expected_duration
        if delta < 0 and diff > _DURATION_TOLERANCE:
            warnings.append(
                f"длительность перевода {dub_duration:.0f}с короче оригинала "
                f"{expected_duration}с на {diff * 100:.0f}% — перевод может быть неполным"
            )
        elif delta > max(allowed_extra, 30.0, expected_duration * 0.25):
            warnings.append(
                f"длительность перевода {dub_duration:.0f}с сильно длиннее оригинала "
                f"{expected_duration}с на {diff * 100:.0f}% — проверьте хвост/тайминги"
            )

    streams = info.get("streams") or []
    has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
    has_video = any(stream.get("codec_type") == "video" for stream in streams)
    if not has_audio:
        warnings.append("в файле нет аудиодорожки — перевод не наложился")
    if not has_video:
        warnings.append("в файле нет видеопотока")

    if has_audio:
        sample_start = (
            expected_duration * 0.1
            if expected_duration and expected_duration > 300
            else 0.0
        )
        mean_db = _mean_volume_db(dub_path, start=sample_start)
        if mean_db is not None and mean_db < -50.0:
            warnings.append(
                f"звук почти тишина (средняя громкость {mean_db:.0f} дБ) — "
                "дубляж мог не наложиться"
            )

    return warnings


_QA_PROMPT = """Ты — профессиональный редактор русского дубляжа христианских проповедей и лекций.

Тебе дан ОРИГИНАЛ (английская речь, аудиофайл) и русский ДУБЛЯЖ этого
материала (машинный перевод Яндекса) — как второй аудиофайл и/или как
точный текст с таймкодами ниже.

Твоя задача — найти места, где русский дубляж ИСКАЖАЕТ СМЫСЛ оригинала.

Особое внимание — теологическим терминам, которые машинный перевод часто портит:
justification (оправдание), sanctification (освящение), atonement (искупление),
providence (провидение), covenant (завет), grace (благодать), righteousness (праведность),
repentance (покаяние), congregation (община), Scripture (Писание), exegesis (экзегеза),
hermeneutics (герменевтика), sovereignty (суверенитет), depravity (испорченность),
propitiation (умилостивление), imputation (вменение), substitutionary (заместительный),
legalism (законничество), seeker-sensitive (ориентированный на ищущих).

НЕ придирайся к стилистике, перестановке слов и естественным упрощениям —
отмечай только реальные искажения смысла, пропуски важных утверждений
и неверно переведённые термины.

КРАСНЫЕ ФЛАГИ (severity=major всегда):
- перевод склеил две соседние мысли так, что появился новый грех/обвинение,
  которого в оригинале нет;
- любые теологические искажения, меняющие смысл Писания или доктрины
  (например, замена «ответственности» на «очевидность», «веры» на «чувства»,
  «оправдания» на «улучшение»);
- ошибки в ссылках на Писание (например, если в оригинале «стих 15», а в переводе
  «стих 50» или «глава 15») — это major.

{reference_block}

ВАЖНО: если ты не уверен, что искажение реально есть — НЕ включай его.
Ложная тревога хуже пропуска: пользователь получит «исправление» хорошего места.

Ответь СТРОГО в формате JSON без пояснений вокруг.
ПИШИ ВСЕ текстовые поля JSON НА РУССКОМ ЯЗЫКЕ: reasoning, verdict, heard,
problem, should_be. Английские слова допускаются только как цитата термина
(например seeker-sensitive) или если они звучат в оригинале.
Поле reasoning заполняй ПЕРВЫМ — сначала рассуждение (укажи, какие именно
теологические термины или ссылки на Писание ты проверял), потом оценка:
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
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-i",
                str(video_path),
                "-vn",
                "-acodec",
                "libmp3lame",
                "-b:a",
                "48k",
                "-ac",
                "1",
                "-y",
                str(out_path),
            ],
            capture_output=True,
            timeout=600,
        )
        if (
            proc.returncode == 0
            and out_path.exists()
            and out_path.stat().st_size > 1024
        ):
            return out_path
    except Exception as exc:
        logger.warning("[LiveDubQA] audio extract failed: %s", exc)
    return None


def _transient_gemini_error(exc: BaseException) -> bool:
    return (
        is_quota_error(exc)
        or is_overload_error(exc)
        or isinstance(exc, (asyncio.TimeoutError, TimeoutError))
        or "timeout" in type(exc).__name__.casefold()
        or "timed out" in str(exc).casefold()
    )


async def _upload_and_wait(
    client,
    path: Path,
    display_name: str,
    budget: capacity_control.GeminiRetryBudget,
):
    budget.claim()
    uf = None
    try:
        uf = await capacity_control.run_heavy_gemini_call(
            lambda: client.aio.files.upload(
                file=path,
                config=types.UploadFileConfig(
                    mime_type="audio/mpeg",
                    display_name=display_name,
                ),
            ),
            domain="files",
        )
        loop = asyncio.get_running_loop()
        start = loop.time()
        while uf.state == "PROCESSING":
            if loop.time() - start > _QA_UPLOAD_WAIT:
                raise TimeoutError(
                    f"Gemini file processing timeout ({_QA_UPLOAD_WAIT}s)"
                )
            await asyncio.sleep(3)
            uf = await capacity_control.run_heavy_gemini_call(
                lambda _name=uf.name: client.aio.files.get(name=_name),
                domain="files",
            )
        if uf.state == "FAILED":
            raise RuntimeError("Gemini file processing FAILED")
        return uf
    except Exception as exc:
        if is_overload_error(exc):
            capacity_control.note_overload(
                capacity_control.transient_retry_delay(budget.used),
                domain="files",
            )
        if uf is not None and getattr(uf, "name", ""):
            try:
                await client.aio.files.delete(name=uf.name)
            except Exception:
                pass
        raise


def srt_to_timed_text(srt_path: Path, max_chars: int = 12000) -> str:
    try:
        raw = Path(srt_path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    out: list[str] = []
    for block in re.split(r"\n\s*\n", raw):
        lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        ts_idx = 1 if lines[0].isdigit() else 0
        match = (
            re.match(
                r"(\d{2}):(\d{2}):(\d{2})[,.]\d{3}\s*-->",
                lines[ts_idx],
            )
            if ts_idx < len(lines)
            else None
        )
        if not match:
            continue
        h, mm, ss = (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )
        total_m = h * 60 + mm
        text_lines = lines[ts_idx + 1 :]
        if not text_lines:
            continue
        out.append(f"[{total_m:02d}:{ss:02d}] " + " ".join(text_lines))
        if sum(len(item) for item in out) > max_chars:
            break
    return "\n".join(out)


def _parse_qa_json(text: str) -> Optional[dict]:
    if not text:
        return None
    cleaned = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        text.strip(),
        flags=re.MULTILINE,
    ).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


async def _run_translation_qa_base(
    dub_video_path: Path,
    original_audio_path: Optional[Path],
    ai_data: Optional[dict],
    duration: int,
    model_name: str = "",
    dub_srt_path: Optional[Path] = None,
    dub_audio_path: Optional[Path] = None,
    existing_audio_part=None,
    existing_client=None,
    thinking_level: str = "high",
) -> Optional[dict]:
    if not (HAS_GEMINI and GEMINI_CLIENTS and types is not None):
        logger.info("[LiveDubQA] Gemini недоступен — смысловая проверка пропущена")
        return None
    if not model_name:
        from core.database import GEMINI_MODEL

        model_name = GEMINI_MODEL

    # Do not extract/re-upload any media while the inference circuit is already
    # open from an exhausted previous segment/request.
    try:
        capacity_control.require_domain_available("inference")
    except capacity_control.GeminiCapacityCircuitOpen as exc:
        logger.warning("[LiveDubQA] %s", exc)
        return None

    qa_audio = dub_video_path.parent / f"{dub_video_path.stem}_qa.mp3"
    uploaded: list = []
    client_used = None
    temp_original_audio: Path | None = None
    original_upload_budget = capacity_control.GeminiRetryBudget()
    dub_upload_budget = capacity_control.GeminiRetryBudget()
    inference_budget = capacity_control.GeminiRetryBudget()

    try:
        dub_timed_text = ""
        if dub_srt_path and Path(dub_srt_path).exists():
            dub_timed_text = srt_to_timed_text(dub_srt_path)
            if not dub_timed_text:
                logger.warning(
                    "[LiveDubQA] SRT перевода пустой/битый — перехожу на аудио дубляжа"
                )
        have_srt = bool(dub_timed_text)
        dub_audio = None
        if not have_srt:
            if dub_audio_path and Path(dub_audio_path).exists():
                dub_audio = Path(dub_audio_path)
                logger.info(
                    "[LiveDubQA] сравниваю по ЧИСТОЙ RU-дорожке (без EN-фона микса)"
                )
            else:
                dub_audio = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: _extract_audio_for_qa(dub_video_path, qa_audio),
                )
            if dub_audio is None:
                logger.warning("[LiveDubQA] не удалось извлечь аудио дубляжа")
                return None
        else:
            logger.info(
                "[LiveDubQA] есть SRT перевода — сравниваю EN-аудио с текстом"
            )

        original_path_available = bool(
            original_audio_path and Path(original_audio_path).exists()
        )
        existing_original_active = bool(
            existing_audio_part is not None
            and existing_client is not None
            and "ACTIVE" in str(getattr(existing_audio_part, "state", ""))
        )
        will_attach_original = original_path_available or existing_original_active
        if will_attach_original:
            reference_block = ""
        else:
            ref_lines: list[str] = []
            if ai_data:
                if ai_data.get("main_topic"):
                    ref_lines.append(f"Тема: {ai_data['main_topic']}")
                timestamps = ai_data.get("timestamps")
                if isinstance(timestamps, list):
                    ref_lines.extend(str(item) for item in timestamps[:40])
                elif isinstance(timestamps, str):
                    ref_lines.append(timestamps[:4000])
            if not ref_lines and not have_srt:
                logger.info(
                    "[LiveDubQA] нет ни оригинала, ни анализа — проверка невозможна"
                )
                return None
            if not ref_lines:
                logger.info("[LiveDubQA] нет эталона оригинала — проверка пропущена")
                return None
            reference_block = (
                "Оригинальное аудио недоступно. Вместо него используй этот "
                "проверенный конспект оригинала как эталон смысла:\n"
                + "\n".join(ref_lines)
            )

        dub_text_block = ""
        if have_srt:
            dub_text_block = (
                "\n\nТОЧНЫЙ ТЕКСТ русского дубляжа (официальные субтитры "
                "перевода Яндекса с таймкодами — цитируй поле heard ИЗ НЕГО, "
                "таймкоды бери отсюда):\n"
                + dub_timed_text
            )
            logger.info(
                "[LiveDubQA] использую текст перевода из SRT (%d строк)",
                dub_timed_text.count("\n") + 1,
            )

        if reference_block:
            if have_srt:
                reference_block += (
                    "\n\nАудиофайлы НЕ приложены: сравнивай конспект оригинала "
                    "с текстом дубляжа ниже."
                )
            else:
                reference_block += (
                    "\n\nЕдинственный приложенный аудиофайл — русский ДУБЛЯЖ "
                    "(озвучка перевода)."
                )
        elif have_srt:
            reference_block = (
                "Приложен ОДИН аудиофайл — английский ОРИГИНАЛ. Русский дубляж "
                "дан НИЖЕ ТОЛЬКО ТЕКСТОМ (официальные субтитры перевода) — "
                "сравнивай аудио оригинала с этим текстом."
            )
        else:
            reference_block = (
                "Первый файл — ОРИГИНАЛ (англ.), второй — ДУБЛЯЖ (рус.). "
                "Сравнивай их напрямую."
            )

        prompt = _QA_PROMPT.format(
            reference_block=reference_block + dub_text_block
        )

        async def _attempt(client):
            nonlocal client_used, temp_original_audio
            client_used = client
            parts = []
            if existing_original_active and existing_client is client:
                logger.info(
                    "[LiveDubQA] реюз audio_part основного анализа "
                    "(без повторной заливки)"
                )
                parts.append(existing_audio_part)
            elif original_path_available:
                if original_upload_budget.exhausted:
                    raise RuntimeError(
                        "LiveDub QA original Files retry budget exhausted"
                    )
                orig_path = Path(original_audio_path)
                upload_orig = orig_path
                if orig_path.suffix.lower() not in {".mp3", ".mpeg", ".mpga"}:
                    tmp_path = (
                        orig_path.parent / f"{orig_path.stem}_qa_original.mp3"
                    )
                    extracted = await asyncio.get_running_loop().run_in_executor(
                        None,
                        lambda: _extract_audio_for_qa(orig_path, tmp_path),
                    )
                    if extracted is None:
                        logger.warning(
                            "[LiveDubQA] не удалось извлечь оригинальное аудио для QA"
                        )
                        return None
                    temp_original_audio = Path(extracted)
                    upload_orig = temp_original_audio
                uf_orig = await _upload_and_wait(
                    client,
                    upload_orig,
                    "qa_original",
                    original_upload_budget,
                )
                uploaded.append(uf_orig)
                parts.append(uf_orig)

            if dub_audio is not None:
                if dub_upload_budget.exhausted:
                    raise RuntimeError(
                        "LiveDub QA dub Files retry budget exhausted"
                    )
                uf_dub = await _upload_and_wait(
                    client,
                    dub_audio,
                    "qa_dub",
                    dub_upload_budget,
                )
                uploaded.append(uf_dub)
                parts.append(uf_dub)

            from core.globals import make_audio_config

            cfg = make_audio_config(
                max_output_tokens=49152,
                model_name=model_name,
                thinking_level=thinking_level,
                response_mime_type="application/json",
            )

            if inference_budget.exhausted:
                raise RuntimeError("LiveDub QA inference retry budget exhausted")
            inference_budget.claim()
            try:
                return await capacity_control.run_heavy_gemini_call(
                    lambda: asyncio.wait_for(
                        client.aio.models.generate_content(
                            model=model_name,
                            contents=parts + [prompt],
                            config=cfg,
                        ),
                        timeout=600.0,
                    )
                )
            except Exception as first_error:
                if is_overload_error(first_error):
                    capacity_control.note_overload(
                        capacity_control.transient_retry_delay(
                            inference_budget.used
                        )
                    )
                if _transient_gemini_error(first_error):
                    raise
                if inference_budget.exhausted:
                    raise

                logger.info(
                    "[LiveDubQA] JSON-mime недоступен (%s) — один bounded "
                    "повтор в текстовом режиме",
                    str(first_error)[:120],
                )
                inference_budget.claim()
                try:
                    return await capacity_control.run_heavy_gemini_call(
                        lambda: asyncio.wait_for(
                            client.aio.models.generate_content(
                                model=model_name,
                                contents=parts + [prompt],
                                config=make_audio_config(
                                    max_output_tokens=49152,
                                    model_name=model_name,
                                    thinking_level=thinking_level,
                                ),
                            ),
                            timeout=600.0,
                        )
                    )
                except Exception as fallback_error:
                    if is_overload_error(fallback_error):
                        capacity_control.note_overload(
                            capacity_control.transient_retry_delay(
                                inference_budget.used
                            )
                        )
                    raise

        last_err = None
        qa_deadline = asyncio.get_running_loop().time() + _QA_TOTAL_TIMEOUT
        clients_order = list(GEMINI_CLIENTS)
        if existing_client is not None and existing_client in clients_order:
            clients_order.remove(existing_client)
            clients_order.insert(0, existing_client)

        if existing_original_active and not original_path_available:
            if existing_client not in clients_order:
                logger.warning(
                    "[LiveDubQA] key-bound original handle owner is unavailable"
                )
                return None
            clients_order = [existing_client] * inference_budget.limit

        for client in clients_order:
            if inference_budget.exhausted:
                break
            if (
                original_path_available
                and not (
                    existing_original_active and existing_client is client
                )
                and original_upload_budget.exhausted
            ):
                break
            if dub_audio is not None and dub_upload_budget.exhausted:
                break

            left = qa_deadline - asyncio.get_running_loop().time()
            if left < 45:
                logger.warning(
                    "[LiveDubQA] общий бюджет времени исчерпан — стоп ротации ключей"
                )
                break
            try:
                resp = await asyncio.wait_for(_attempt(client), timeout=left)
                raw_text = getattr(resp, "text", "") or ""
                result = _parse_qa_json(raw_text)
                if isinstance(result, dict) and (
                    "issues" in result
                    or "score" in result
                    or "verdict" in result
                ):
                    result.setdefault("issues", [])
                    if not will_attach_original:
                        result.setdefault("_low_confidence", True)
                    return result

                try:
                    candidate = (getattr(resp, "candidates", None) or [None])[0]
                    finish_reason = getattr(candidate, "finish_reason", "?")
                    usage = getattr(resp, "usage_metadata", None)
                    logger.warning(
                        "[LiveDubQA] не распарсился: finish=%s thoughts=%s "
                        "out=%s text_head=%r",
                        finish_reason,
                        getattr(usage, "thoughts_token_count", "?"),
                        getattr(usage, "candidates_token_count", "?"),
                        raw_text[:160],
                    )
                except Exception:
                    pass
                last_err = RuntimeError(
                    "ответ модели не распарсился в QA-JSON"
                )
            except Exception as exc:
                last_err = exc
                logger.warning(
                    "[LiveDubQA] клиент не справился: %s",
                    str(exc)[:200],
                )
            finally:
                for uploaded_file in uploaded:
                    try:
                        await client.aio.files.delete(name=uploaded_file.name)
                    except Exception:
                        pass
                uploaded.clear()

            if last_err is not None and is_overload_error(last_err):
                capacity_control.note_overload(
                    capacity_control.transient_retry_delay(
                        max(1, inference_budget.used)
                    )
                )

        logger.warning(
            "[LiveDubQA] bounded QA budget exhausted/clients unavailable: %s",
            str(last_err)[:200],
        )
        return None
    except Exception as exc:
        logger.warning("[LiveDubQA] неожиданный сбой: %s", exc)
        return None
    finally:
        for uploaded_file in uploaded:
            try:
                if client_used is not None:
                    await client_used.aio.files.delete(name=uploaded_file.name)
            except Exception:
                pass
        try:
            qa_audio.unlink(missing_ok=True)
        except Exception:
            pass
        if temp_original_audio is not None:
            try:
                temp_original_audio.unlink(missing_ok=True)
            except Exception:
                pass


async def run_translation_qa(
    dub_video_path: Path,
    original_audio_path: Optional[Path],
    ai_data: Optional[dict],
    duration: int,
    model_name: str = "",
    dub_srt_path: Optional[Path] = None,
    dub_audio_path: Optional[Path] = None,
    existing_audio_part=None,
    existing_client=None,
    thinking_level: str = "high",
) -> Optional[dict]:
    from services.livedub_long_qa import run_long_translation_qa
    from services.livedub_qa_hardening import (
        annotate_qa_availability,
        prepare_exact_timeline_inputs,
    )
    from services.livedub_qa_trust import (
        apply_audio_trust,
        audio_trust_enabled,
    )

    options = dict(
        dub_video_path=Path(dub_video_path),
        original_audio_path=original_audio_path,
        ai_data=ai_data,
        duration=int(duration or 0),
        model_name=model_name,
        dub_srt_path=None if audio_trust_enabled() else dub_srt_path,
        dub_audio_path=dub_audio_path,
        existing_audio_part=existing_audio_part,
        existing_client=existing_client,
        thinking_level=thinking_level,
    )
    options, exact_original = prepare_exact_timeline_inputs(options)
    primary = await run_long_translation_qa(_run_translation_qa_base, **options)
    if not isinstance(primary, dict):
        return primary
    primary = annotate_qa_availability(primary, options, exact_original)
    return await apply_audio_trust(
        _run_translation_qa_base,
        primary=primary,
        dub_video_path=options["dub_video_path"],
        original_audio_path=options["original_audio_path"],
        duration=options["duration"],
        model_name=options["model_name"],
        dub_audio_path=options["dub_audio_path"],
        existing_audio_part=options["existing_audio_part"],
        existing_client=options["existing_client"],
    )


def _format_qa_report_base(qa: dict, video_url: str = "") -> str:
    score = qa.get("score")
    verdict = str(qa.get("verdict") or "").strip()
    issues = [
        issue
        for issue in (qa.get("issues") or [])
        if isinstance(issue, dict)
    ]

    if isinstance(score, (int, float)) and score >= 95 and not issues:
        head = f"✅ <b>Проверка перевода: {score:.0f}/100</b>"
    elif isinstance(score, (int, float)):
        head = f"🔍 <b>Проверка перевода: {score:.0f}/100</b>"
    else:
        head = "🔍 <b>Проверка перевода</b>"

    lines = [head]
    if qa.get("_low_confidence"):
        lines.append(
            "⚠️ Оригинальное аудио было недоступно — сверка велась по конспекту, "
            "не по полному тексту. Часть проповеди проверке не подверглась."
        )
    if verdict:
        lines.append(html_mod.escape(verdict[:600]))

    majors = [
        issue
        for issue in issues
        if str(issue.get("severity")) == "major"
    ]
    minors = [
        issue
        for issue in issues
        if str(issue.get("severity")) != "major"
    ]

    def _ts_link(raw_time: str) -> str:
        escaped = html_mod.escape(raw_time)
        if not video_url:
            return f"<b>{escaped}</b>"
        from services.livedub_mix import parse_mmss

        seconds = parse_mmss(raw_time)
        if seconds is None:
            return f"<b>{escaped}</b>"
        separator = "&" if "?" in video_url else "?"
        href = html_mod.escape(
            f"{video_url}{separator}t={int(seconds)}",
            quote=True,
        )
        return f'<a href="{href}"><b>{escaped}</b></a>'

    def _fmt(issue: dict, icon: str) -> str:
        timestamp = _ts_link(str(issue.get("time") or "—"))
        heard = html_mod.escape(str(issue.get("heard") or "")[:120])
        should = html_mod.escape(str(issue.get("should_be") or "")[:120])
        problem = html_mod.escape(str(issue.get("problem") or "")[:160])
        parts = [f"{icon} {timestamp} — {problem}"]
        if heard:
            parts.append(f"    Звучит: «{heard}»")
        if should:
            parts.append(f"    Верно: «{should}»")
        return "\n".join(parts)

    if majors:
        lines.append("")
        lines.append("<b>Серьёзные искажения:</b>")
        lines.extend(_fmt(issue, "🔴") for issue in majors[:5])
    if minors:
        lines.append("")
        lines.append("<b>Мелкие неточности:</b>")
        lines.extend(_fmt(issue, "🟡") for issue in minors[:5])
    if not issues:
        lines.append(
            "Искажений смысла не найдено — перевод можно публиковать."
        )

    limit = 4000
    tail = "\n… часть отчёта не поместилась"
    output: list[str] = []
    used = 0
    truncated = False
    for index, line in enumerate(lines):
        add = (1 if output else 0) + len(line)
        room = limit - (len(tail) if index < len(lines) - 1 else 0)
        if used + add > room:
            truncated = True
            break
        output.append(line)
        used += add
    text = "\n".join(output)
    if truncated:
        text += tail
    return text[:limit]


def format_qa_report(qa: dict, video_url: str = "") -> str:
    from services.livedub_long_qa import decorate_segment_report
    from services.livedub_qa_hardening import decorate_hardened_report
    from services.livedub_qa_trust import decorate_trust_report

    text = _format_qa_report_base(qa, video_url=video_url)
    text = decorate_segment_report(text, qa)
    text = decorate_trust_report(text, qa)
    text = decorate_hardened_report(text, qa)
    try:
        from converters.md_telegraph import safe_trim_caption

        return safe_trim_caption(text, 3900)
    except Exception:
        return text[:3900]


def validate_livedub_qa_contract() -> str:
    from services.livedub_long_qa import run_long_translation_qa
    from services.livedub_qa_hardening import confirmed_result_one_to_one
    from services.livedub_qa_trust import apply_audio_trust

    if not all(
        callable(item)
        for item in (
            _run_translation_qa_base,
            run_translation_qa,
            run_long_translation_qa,
            apply_audio_trust,
            confirmed_result_one_to_one,
            format_qa_report,
        )
    ):
        raise RuntimeError("source-owned LiveDub QA contract is incomplete")
    return (
        "source-owned LiveDub QA: base -> segmented coverage -> "
        "focused confirmation"
    )
