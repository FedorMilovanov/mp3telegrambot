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


def _mean_volume_db(path: Path, start: float = 0.0, dur: float = 120.0) -> Optional[float]:
    """Средняя громкость участка дорожки (ffmpeg volumedetect), дБ или None.

    AUDIT R42: анализируем ВЫБОРКУ (окно ~2 мин), а не весь файл — иначе на
    40-минутной проповеди technical_check перестал бы быть «дешёвым».
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    try:
        cmd = [ffmpeg, "-hide_banner"]
        if start and start > 0:
            cmd += ["-ss", str(int(start))]
        cmd += ["-t", str(int(dur)), "-i", str(path),
                "-af", "volumedetect", "-f", "null", "-"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", proc.stderr or "")
        if m:
            return float(m.group(1))
    except Exception as e:
        logger.warning("[LiveDubQA] volumedetect failed: %s", e)
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
        # LiveDub Pro-mix намеренно длиннее оригинала на delay + tail margin:
        # иначе последняя русская фраза после нашего сдвига обрывается в Shorts.
        # Это НЕ признак неполного перевода, поэтому положительную разницу в
        # пределах tail guard не ругаем. Отрицательная разница по-прежнему опасна.
        allowed_extra = 0.0
        try:
            from services.livedub_mix import get_mix_params
            allowed_extra = (get_mix_params().get("tail_pad_ms") or 0) / 1000.0 + 0.75
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

    # 2. Аудиопоток
    streams = info.get("streams") or []
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    has_video = any(s.get("codec_type") == "video" for s in streams)
    if not has_audio:
        warnings.append("в файле нет аудиодорожки — перевод не наложился")
    if not has_video:
        warnings.append("в файле нет видеопотока")

    # AUDIT R42: наличие аудиопотока ≠ слышен русский голос. Пустой/молчащий
    # дубляж (Яндекс отдал тишину, или RU-ветка выпала и остался лишь приглушён-
    # ный EN) даёт has_audio=True и полную длину — прежде проходил как «ок».
    # Меряем среднюю громкость выборки: цифровая тишина ≈ −70…−91 дБ, нормальный
    # микс ≈ −16…−26 дБ, поэтому порог −50 дБ разделяет их с запасом.
    if has_audio:
        _sample_start = expected_duration * 0.1 if expected_duration and expected_duration > 300 else 0.0
        mean_db = _mean_volume_db(dub_path, start=_sample_start)
        if mean_db is not None and mean_db < -50.0:
            warnings.append(
                f"звук почти тишина (средняя громкость {mean_db:.0f} дБ) — "
                "дубляж мог не наложиться"
            )

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
        # AUDIT ENG (2026-07-05): текст SRT парсим ДО решения «нужен ли звук
        # дубляжа». Раньше пустой/битый SRT-файл считался «есть текст», аудио
        # дубляжа не извлекалось — и Gemini получал ТОЛЬКО оригинал, без
        # какого-либо дубляжа вообще: весь отчёт был бы галлюцинацией.
        dub_timed_text = ""
        if dub_srt_path and Path(dub_srt_path).exists():
            dub_timed_text = srt_to_timed_text(dub_srt_path)
            if not dub_timed_text:
                logger.warning("[LiveDubQA] SRT перевода пустой/битый — перехожу на аудио дубляжа")
        _have_srt = bool(dub_timed_text)
        dub_audio = None
        if not _have_srt:
            # AUDIT R42: предпочитаем ЧИСТУЮ RU-дорожку (dub_audio_path из
            # find_pro_tracks), если она сохранилась. Извлечение аудио из готового
            # видео даёт БИЛИНГВАЛЬНЫЙ микс (EN 0.45 под RU) — Gemini слышал
            # английский «фон» под русским, хотя промт называет файл «чистый
            # дубляж»: между русскими фразами всплывал EN, и модель могла
            # недооценить искажение RU или зацепиться за слышимый английский.
            # Чистая дорожка = ровно то, что промт обещает модели.
            if dub_audio_path and Path(dub_audio_path).exists():
                dub_audio = Path(dub_audio_path)
                logger.info("[LiveDubQA] сравниваю по ЧИСТОЙ RU-дорожке (без EN-фона микса)")
            else:
                # Аудио дубляжа нужно только когда нет официального текста перевода
                dub_audio = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: _extract_audio_for_qa(dub_video_path, qa_audio)
                )
            if dub_audio is None:
                logger.warning("[LiveDubQA] не удалось извлечь аудио дубляжа")
                return None
        else:
            logger.info("[LiveDubQA] есть SRT перевода — сравниваю EN-аудио с текстом (без извлечения дубляжа)")

        # Референс: если оригинального аудио нет — используем готовый анализ.
        # AUDIT R43: раньше решение смотрело ТОЛЬКО на original_audio_path, но
        # _attempt() ниже может приложить оригинал через реюз existing_audio_part
        # НЕЗАВИСИМО от original_audio_path (например, mp3 уже вычищен с диска,
        # а Gemini-хэндл основного анализа ещё жив). В этом случае промт врал
        # «аудио не приложены — сравнивай с конспектом», хотя оригинал реально
        # прикладывался — модели говорили игнорировать то, что она получила.
        _will_attach_original = bool(
            (original_audio_path and Path(original_audio_path).exists())
            or (existing_audio_part is not None and existing_client is not None
                and "ACTIVE" in str(getattr(existing_audio_part, "state", "")))
        )
        if _will_attach_original:
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
        if _have_srt:
            dub_text_block = (
                "\n\nТОЧНЫЙ ТЕКСТ русского дубляжа (официальные субтитры перевода "
                "Яндекса с таймкодами — цитируй поле heard ИЗ НЕГО, таймкоды бери отсюда):\n"
                + dub_timed_text
            )
            logger.info("[LiveDubQA] использую текст перевода из SRT (%d строк)",
                        dub_timed_text.count("\n") + 1)

        # AUDIT ENG (2026-07-05): описание вложений должно соответствовать
        # РЕАЛЬНОМУ составу файлов. Раньше при «оригинал-аудио + SRT» промт
        # утверждал «второй файл — ДУБЛЯЖ», хотя второго файла не было —
        # модель искала дубляж в несуществующем вложении.
        if reference_block:
            # Оригинального аудио нет (эталон — конспект).
            if _have_srt:
                reference_block += (
                    "\n\nАудиофайлы НЕ приложены: сравнивай конспект оригинала "
                    "с текстом дубляжа ниже."
                )
            else:
                reference_block += (
                    "\n\nЕдинственный приложенный аудиофайл — русский ДУБЛЯЖ "
                    "(озвучка перевода)."
                )
        elif _have_srt:
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

        prompt = _QA_PROMPT.format(reference_block=reference_block + dub_text_block)

        async def _attempt(client):
            # FIX AUDIT R4: без nonlocal присваивание ниже создавало ЛОКАЛЬНУЮ
            # _temp_original_audio — внешняя оставалась None, и cleanup в
            # finally был мёртв: {stem}_qa_original.mp3 утекал на диск после
            # каждого Quick-QA прогона.
            nonlocal client_used, _temp_original_audio
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
                resp = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=model_name,
                        contents=parts + [prompt],
                        config=cfg,
                    ),
                    timeout=600.0,
                )
            except Exception as _je:
                # Fallback: тот же конфиг, но без JSON-mime (текст распарсим сами)
                logger.info("[LiveDubQA] JSON-mime недоступен (%s) — повтор в текстовом режиме",
                            str(_je)[:120])
                resp = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=model_name,
                        contents=parts + [prompt],
                        # тот же thinking_level, что и в основном вызове:
                        # Quick QA работает на minimal — high здесь съедал бы
                        # время/токены лёгкой модели без причины
                        config=make_audio_config(
                            max_output_tokens=49152,
                            model_name=model_name,
                            thinking_level=thinking_level,
                        ),
                    ),
                    timeout=600.0,
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
                # AUDIT R42: чистый перевод модель может вернуть как
                # {"reasoning","score","verdict"} БЕЗ ключа "issues" (жёсткую
                # схему не навязываем). Прежде это считалось «не распарсилось» →
                # ротация всех ключей + утечка файлов + пользователю «проверка не
                # удалась» для по сути ХОРОШЕГО перевода. Принимаем любой dict со
                # score/verdict/issues, дефолтя issues=[].
                if isinstance(result, dict) and (
                    "issues" in result or "score" in result or "verdict" in result
                ):
                    result.setdefault("issues", [])
                    # AUDIT R43: без оригинального аудио эталон — лишь конспект
                    # (main_topic/timestamps), а не полный текст — большая часть
                    # проповеди для QA невидима. Помечаем как низкую уверенность,
                    # чтобы отчёт не создавал ложного ощущения полной проверки.
                    if not _will_attach_original:
                        result.setdefault("_low_confidence", True)
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
                # AUDIT R42: при непарсе ТОЖЕ чистим залитые файлы текущего ключа
                # (как в except-ветке ниже) — иначе они утекали на Gemini, а
                # следующий ключ доливал свои, и uploaded смешивал ключи (finally
                # удаляет только на client_used = последнем).
                for uf in uploaded:
                    try:
                        await client.aio.files.delete(name=uf.name)
                    except Exception:
                        pass
                uploaded.clear()
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
    """Source-owned QA pipeline: evidence -> coverage -> confirmation."""
    from services.livedub_long_qa import run_long_translation_qa
    from services.livedub_qa_hardening import (
        annotate_qa_availability,
        prepare_exact_timeline_inputs,
    )
    from services.livedub_qa_trust import apply_audio_trust, audio_trust_enabled

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


# ══════════════════════════════════════════════════════════════
#  3. Форматирование отчёта
# ══════════════════════════════════════════════════════════════

def _format_qa_report_base(qa: dict, video_url: str = "") -> str:
    """Собирает HTML-сообщение с результатом проверки перевода.

    video_url: если задан — таймкоды проблем становятся кликабельными
    ссылками на момент видео (youtu.be?t=N), юзер сразу прыгает к месту.
    """
    score = qa.get("score")
    verdict = str(qa.get("verdict") or "").strip()
    # AUDIT R39: отбрасываем не-dict элементы (schema-less модель могла вернуть
    # issues списком строк) — иначе .get() ниже ронял весь QA-отчёт в except.
    issues = [i for i in (qa.get("issues") or []) if isinstance(i, dict)]

    if isinstance(score, (int, float)) and score >= 95 and not issues:
        head = f"✅ <b>Проверка перевода: {score:.0f}/100</b>"
    elif isinstance(score, (int, float)):
        head = f"🔍 <b>Проверка перевода: {score:.0f}/100</b>"
    else:
        head = "🔍 <b>Проверка перевода</b>"

    lines = [head]
    # AUDIT R43: без оригинального аудио сверка шла по конспекту (main_topic +
    # таймкоды), а не по полному тексту — честно предупреждаем, что проверка
    # частичная, вместо того чтобы отчёт выглядел как полная сверка.
    if qa.get("_low_confidence"):
        lines.append(
            "⚠️ Оригинальное аудио было недоступно — сверка велась по конспекту, "
            "не по полному тексту. Часть проповеди проверке не подверглась."
        )
    if verdict:
        # AUDIT R42: вердикт — «одно предложение», но модель иногда выдаёт абзац;
        # без кэпа он мог один съесть весь лимит отчёта.
        lines.append(html_mod.escape(verdict[:600]))

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

    # AUDIT R42: раньше был грубый text[:4000] — обрезка могла разрубить <a>/<b>
    # или &…;-сущность, и Telegram отклонял ВЕСЬ отчёт (parse_mode=HTML → 400),
    # а пользователь не видел искажений вовсе. Собираем по ЦЕЛЫМ блокам (каждый
    # HTML-сбалансирован: head/verdict экранированы, _fmt закрывает свои теги) в
    # пределах лимита, а не режем посреди тега.
    _LIMIT = 4000
    _tail = "\n… часть отчёта не поместилась"
    out: list[str] = []
    used = 0
    truncated = False
    for idx, ln in enumerate(lines):
        add = (1 if out else 0) + len(ln)  # +1 за перевод строки
        room = _LIMIT - (len(_tail) if idx < len(lines) - 1 else 0)
        if used + add > room:
            truncated = True
            break
        out.append(ln)
        used += add
    text = "\n".join(out)
    if truncated:
        text += _tail
    return text[:_LIMIT]



def format_qa_report(qa: dict, video_url: str = "") -> str:
    """Render one truthful report through pure source-owned decorators."""
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
    """Startup invariant for the direct QA pipeline."""
    from services.livedub_long_qa import run_long_translation_qa
    from services.livedub_qa_hardening import confirmed_result_one_to_one
    from services.livedub_qa_trust import apply_audio_trust

    if not all(callable(item) for item in (
        _run_translation_qa_base,
        run_translation_qa,
        run_long_translation_qa,
        apply_audio_trust,
        confirmed_result_one_to_one,
        format_qa_report,
    )):
        raise RuntimeError("source-owned LiveDub QA contract is incomplete")
    return "source-owned LiveDub QA: base -> segmented coverage -> focused confirmation"
