#!/usr/bin/env python3
"""
Quiz Generator — generates Telegram Native Quiz polls from video AI analysis.

Uses Gemini to create high-quality theological quiz questions with:
- 4 answer options per question
- Correct answer identification
- Detailed explanation for each question (shown after answering)
- Scripture references where applicable
- Difficulty gradient (easy → hard)

Output: list of dicts ready for telegram bot.send_poll(type="quiz").
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

from core.globals import GEMINI_CLIENTS, make_text_config_smart, is_quota_error, is_overload_error
from core.database import GEMINI_MODEL
from core.text_utils import _scrub_inline
from core.observability import alog_gemini_response, alog_gemini_run

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, *, min_value: int = 1, max_value: int = 20) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(int(value), max_value))


QUIZ_QUESTION_COUNT = _env_int("QUIZ_QUESTION_COUNT", 10)

QUIZ_PROMPT = """\
Ты — преподаватель богословия и составитель проверочных вопросов.

На основе аудио-материала и переданного анализа составь ровно {count} вопросов
для Telegram Native Quiz. Это не игра в угадайку, а проверка понимания аргумента.

ЖЁСТКИЕ ПРАВИЛА:
1. Вопросы grounded: правильный ответ должен следовать из материала/таймкодов/анализа.
2. Не спрашивай о фактах, которых нет в материале. Не добавляй внешние сведения.
3. Покрой разные части материала: начало, середину и финал.
4. Каждый вопрос проверяет понимание, причинно-следственную связь, термин или применение.
5. У каждого вопроса ровно 4 варианта ответа.
6. Неправильные варианты правдоподобны, но однозначно неверны по материалу.
7. Не делай варианты вроде «всё перечисленное», «нет правильного ответа».
8. Не повторяй один и тот же вопрос другими словами.
9. Не используй third-person wrappers: избегай конструкции «роль/имя автора + показывает/объясняет». Формулируй вопрос напрямую.
10. Объяснение до 200 символов: почему ответ верный, с опорой на аргумент/Писание.

СЛОЖНОСТЬ:
- первые 3 вопроса — базовые;
- следующие 4 — средние;
- последние — сложные, на связь аргументов и богословский смысл.

ДАННЫЕ О МАТЕРИАЛЕ:
Название: {title}
Автор: {author}
Длительность: {duration}

Верни строго JSON-массив без текста вокруг:
[
  {{
    "question": "Текст вопроса до 255 символов",
    "options": ["Вариант A", "Вариант B", "Вариант C", "Вариант D"],
    "correct": 0,
    "explanation": "Краткое объяснение правильного ответа до 200 символов"
  }}
]

correct — индекс правильного ответа 0-3. Все тексты — на русском языке.
"""


def quiz_response_schema() -> dict:
    item = {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}},
            "correct": {"type": "integer"},
            "explanation": {"type": "string"},
        },
        "required": ["question", "options", "correct", "explanation"],
    }
    return {"type": "array", "items": item}


def _safe_trim(text: str, limit: int) -> str:
    text = _scrub_inline(str(text or "").replace("\x00", "").strip())
    text = re.sub(r"\s+", " ", text)
    if len(text) <= limit:
        return text
    cut = text[: max(1, limit - 1)].rstrip()
    if " " in cut and len(cut) > limit * 0.65:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(".,;:—-") + "…"


def _dedupe_keep_order(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = re.sub(r"\W+", "", item.casefold())
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _bad_quiz_option(text: str) -> bool:
    low = str(text or "").strip().casefold()
    return any(
        marker in low
        for marker in (
            "все перечислен", "всё перечислен", "все варианты", "всё варианты",
            "нет правильного", "нет верного", "ни один", "ни один из",
            "оба варианта", "a и b", "а и б", "all of the above", "none of the above",
        )
    )


def _parse_correct_index(value, options: list[str]) -> int | None:
    try:
        idx = int(value)
        return idx if 0 <= idx < len(options) else None
    except (TypeError, ValueError):
        pass
    raw = str(value or "").strip()
    if len(raw) == 1 and raw.upper() in "ABCD":
        idx = ord(raw.upper()) - ord("A")
        return idx if idx < len(options) else None
    # Some models return the full correct option text instead of an index.
    norm = re.sub(r"\W+", "", raw.casefold())
    for idx, option in enumerate(options):
        if norm and norm == re.sub(r"\W+", "", option.casefold()):
            return idx
    return None


def _parse_quiz_json(raw: str, *, expected_count: int | None = None) -> list[dict] | None:
    """Parse and validate Gemini quiz response into Telegram-ready dicts."""
    if not raw:
        return None
    text = re.sub(r'^```(?:json)?\s*', '', raw.strip())
    text = re.sub(r'\s*```$', '', text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'\[.*\]', text, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if isinstance(data, dict):
        for key in ("questions", "quiz", "items"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        return None

    valid: list[dict] = []
    seen_questions: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        q = _safe_trim(item.get("question") or "", 255)
        opts_raw = item.get("options")
        if not q or not isinstance(opts_raw, list):
            continue
        q_key = re.sub(r"\W+", "", q.casefold())
        if not q_key or q_key in seen_questions:
            continue
        opts = _dedupe_keep_order([_safe_trim(o, 100) for o in opts_raw if str(o).strip()])
        if len(opts) != 4 or any(_bad_quiz_option(o) for o in opts):
            continue
        correct = _parse_correct_index(item.get("correct", item.get("correct_index", item.get("answer"))), opts)
        if correct is None:
            continue
        explanation = _safe_trim(item.get("explanation") or "", 200)
        if not explanation:
            continue
        seen_questions.add(q_key)
        valid.append({"question": q, "options": opts, "correct": correct, "explanation": explanation})
        if expected_count and len(valid) >= expected_count:
            break
    return valid if valid else None


def _format_ai_context(ai_data: dict | None) -> str:
    ai = ai_data or {}
    parts: list[str] = []
    for label, key, limit in (
        ("Главная тема", "main_topic", 900),
        ("Аналитика", "analysis_summary", 900),
        ("Ход аргумента", "argument_arc", 900),
    ):
        value = str(ai.get(key) or "").strip()
        if value:
            parts.append(f"{label}: {_safe_trim(value, limit)}")
    timestamps = str(ai.get("timestamps") or "").strip()
    if timestamps:
        parts.append("Таймкоды/структура:\n" + timestamps[:3500])
    cats = ai.get("key_categories") or []
    if isinstance(cats, list) and cats:
        parts.append("Ключевые категории:\n" + "\n".join(f"- {_safe_trim(c, 180)}" for c in cats[:12]))
    td = ai.get("terms_data") or {}
    if isinstance(td, dict):
        term_lines: list[str] = []
        for key in ("concepts", "scripture", "translations", "lexicon_notes"):
            for raw in (td.get(key) or [])[:6]:
                term_lines.append(f"- {_safe_trim(raw, 260)}")
        if term_lines:
            parts.append("Термины/тексты/лексика:\n" + "\n".join(term_lines[:18]))
    qs = ai.get("questions") or []
    if isinstance(qs, list) and qs:
        parts.append("Уже созданные вопросы для размышления (не копировать дословно):\n" + "\n".join(f"- {_safe_trim(q, 220)}" for q in qs[:8]))
    return "\n\n".join(parts)


async def generate_quiz(
    ai_data: dict,
    title: str = "",
    performer: str = "",
    duration: int = 0,
    existing_audio_part=None,
    existing_client=None,
    count: int | None = None,
) -> list[dict] | None:
    """Generate quiz questions from video AI analysis.

    Uses existing Gemini audio_part if available (no extra upload cost). Falls
    back to text-only context from ai_data. Returns list of question dicts or
    None on failure.
    """
    if not GEMINI_CLIENTS:
        return None
    count = _env_int("QUIZ_QUESTION_COUNT", count or QUIZ_QUESTION_COUNT or 10)
    real_title = _scrub_inline((ai_data or {}).get("real_title") or title or "")
    real_author = _scrub_inline((ai_data or {}).get("real_author") or performer or "")
    duration_str = ""
    if duration:
        h, rem = divmod(int(duration), 3600)
        m, s = divmod(rem, 60)
        duration_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    prompt = QUIZ_PROMPT.format(
        count=count,
        title=real_title or "Без названия",
        author=real_author or "Не указан",
        duration=duration_str or "Не указана",
    )
    context = _format_ai_context(ai_data)
    if context:
        prompt += "\n\nКОНТЕКСТ МАТЕРИАЛА:\n" + context

    started = asyncio.get_running_loop().time()
    try:
        client = existing_client if existing_client in GEMINI_CLIENTS else GEMINI_CLIENTS[0]
        contents: list[Any] = []
        if existing_audio_part and existing_client is client:
            contents.append(existing_audio_part)
        contents.append(prompt)
        schema = quiz_response_schema()
        resp = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config=make_text_config_smart(
                    max_output_tokens=9000,
                    model_name=GEMINI_MODEL,
                    thinking_level="high",
                    response_mime_type="application/json",
                    response_schema=schema,
                ),
            ),
            timeout=180.0,
        )
        raw = getattr(resp, "text", "") or ""
        if not raw:
            try:
                for part in resp.candidates[0].content.parts:
                    if not getattr(part, "thought", False) and getattr(part, "text", None):
                        raw = part.text
                        break
            except Exception:
                pass
        if not raw:
            await alog_gemini_response(response=resp, task="quiz_generator", model=GEMINI_MODEL, duration_ms=int((asyncio.get_running_loop().time() - started) * 1000), json_valid=False, error="empty_response")
            logger.warning("[Quiz] Gemini returned empty response")
            return None
        questions = _parse_quiz_json(raw, expected_count=count)
        await alog_gemini_response(response=resp, task="quiz_generator", model=GEMINI_MODEL, thinking_level="high", duration_ms=int((asyncio.get_running_loop().time() - started) * 1000), json_valid=bool(questions), error="" if questions else "parse_failed")
        if not questions:
            logger.warning("[Quiz] Failed to parse quiz JSON: %s", raw[:200])
            return None
        logger.info("[Quiz] Generated %d questions", len(questions))
        return questions[:count]
    except Exception as e:
        await alog_gemini_run(task="quiz_generator", model=GEMINI_MODEL, thinking_level="high", duration_ms=int((asyncio.get_running_loop().time() - started) * 1000), json_valid=False, error=f"{type(e).__name__}: {str(e)[:240]}")
        if is_quota_error(e):
            logger.warning("[Quiz] Gemini quota error: %s", str(e)[:120])
        elif is_overload_error(e):
            logger.warning("[Quiz] Gemini overloaded: %s", str(e)[:120])
        else:
            logger.warning("[Quiz] Generation failed: %s", str(e)[:200])
        return None


async def send_quiz_polls(
    questions: list[dict],
    update,
    context,
    title: str = "",
) -> int:
    """Send quiz questions as Telegram Native Quiz polls.

    Returns number of successfully sent polls.
    """
    if not questions:
        return 0
    sent = 0
    for i, q in enumerate(questions):
        try:
            _q_text = f"❓ {i+1}/{len(questions)}: {q['question']}"
            if len(_q_text) > 300:
                _q_text = _q_text[:297] + "..."
            await context.bot.send_poll(
                chat_id=update.effective_chat.id,
                question=_q_text,
                options=q["options"],
                type="quiz",
                correct_option_id=q["correct"],
                explanation=q.get("explanation") or None,
                is_anonymous=True,
                reply_to_message_id=update.message.message_id if i == 0 else None,
            )
            sent += 1
            if i < len(questions) - 1:
                await asyncio.sleep(1.5)
        except Exception as e:
            logger.warning("[Quiz] Failed to send poll %d: %s", i+1, str(e)[:120])
            retry_after = getattr(e, "retry_after", None)
            if retry_after:
                await asyncio.sleep(float(retry_after) + 1)
                try:
                    await context.bot.send_poll(
                        chat_id=update.effective_chat.id,
                        question=_q_text,
                        options=q["options"],
                        type="quiz",
                        correct_option_id=q["correct"],
                        explanation=q.get("explanation") or None,
                        is_anonymous=True,
                    )
                    sent += 1
                except Exception:
                    pass
    if sent:
        logger.info("[Quiz] Sent %d/%d quiz polls for '%s'", sent, len(questions), title[:40])
    return sent
