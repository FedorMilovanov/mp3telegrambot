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
from typing import Optional

from core.globals import GEMINI_CLIENTS, make_text_config_smart, is_quota_error, is_overload_error
from core.database import GEMINI_MODEL
from core.text_utils import _scrub_inline

logger = logging.getLogger(__name__)

QUIZ_QUESTION_COUNT = int(os.getenv("QUIZ_QUESTION_COUNT", "10"))

QUIZ_PROMPT = """\
Ты — опытный преподаватель богословия и составитель экзаменационных вопросов.

На основе прослушанного аудио-материала (проповедь/лекция/Q&A) составь ровно {count} вопросов
для проверки понимания ключевых богословских истин, фактов и аргументов.

ТРЕБОВАНИЯ К ВОПРОСАМ:
1. Каждый вопрос должен проверять ПОНИМАНИЕ, а не механическое запоминание.
2. Вопросы должны покрывать РАЗНЫЕ части материала (начало, середину, конец).
3. Неправильные варианты должны быть ПРАВДОПОДОБНЫМИ — не абсурдными.
4. Правильный ответ должен ОДНОЗНАЧНО следовать из материала.
5. Объяснение (explanation) должно КРАТКО объяснить почему этот ответ верный,
   со ссылкой на Писание или аргумент спикера. Макс 200 символов.
6. Сложность: первые 3 вопроса — базовые, следующие 4 — средние, последние 3 — сложные.
7. Типы вопросов: факты, определения терминов, логика аргументов, применение.

ДАННЫЕ О МАТЕРИАЛЕ:
Название: {title}
Автор: {author}
Длительность: {duration}

Ответь строго JSON-массивом без текста вокруг:
[
  {{
    "question": "Текст вопроса (до 300 символов)",
    "options": ["Вариант A", "Вариант B", "Вариант C", "Вариант D"],
    "correct": 0,
    "explanation": "Краткое объяснение правильного ответа (до 200 символов)"
  }},
  ...
]

correct — индекс правильного ответа (0-3).
Все тексты — на РУССКОМ ЯЗЫКЕ.
"""


def _parse_quiz_json(raw: str) -> list[dict] | None:
    """Parse Gemini quiz response into list of question dicts."""
    if not raw:
        return None
    # Strip markdown fences
    text = re.sub(r'^```(?:json)?\s*', '', raw.strip())
    text = re.sub(r'\s*```$', '', text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON array in text
        m = re.search(r'\[.*\]', text, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, list):
        return None
    # Validate each question
    valid = []
    for item in data:
        if not isinstance(item, dict):
            continue
        q = str(item.get("question") or "").strip()
        opts = item.get("options")
        correct = item.get("correct")
        explanation = str(item.get("explanation") or "").strip()
        if not q or not isinstance(opts, list) or len(opts) < 2:
            continue
        # Ensure options are strings
        opts = [str(o).strip() for o in opts if str(o).strip()]
        if len(opts) < 2 or len(opts) > 10:
            continue
        # Validate correct index
        try:
            correct = int(correct)
        except (TypeError, ValueError):
            correct = 0
        if correct < 0 or correct >= len(opts):
            correct = 0
        # Telegram limits: question max 300 chars INCLUDING any prefix
        # send_quiz_polls adds "❓ N/M: " prefix (~10 chars)
        if len(q) > 255:
            q = q[:252] + "..."
        opts = [o[:100] for o in opts]  # Telegram: max 100 chars per option
        if len(explanation) > 200:
            explanation = explanation[:197] + "..."
        valid.append({
            "question": q,
            "options": opts,
            "correct": correct,
            "explanation": explanation,
        })
    return valid if valid else None


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
    
    Uses existing Gemini audio_part if available (no extra upload cost).
    Falls back to text-only prompt from ai_data if audio_part unavailable.
    
    Returns list of question dicts or None on failure.
    """
    if not GEMINI_CLIENTS:
        return None
    
    count = count or QUIZ_QUESTION_COUNT
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
    
    # If we have audio_part — use it for higher quality (Gemini hears the actual content)
    # Otherwise fall back to text context from ai_data
    if not existing_audio_part or not existing_client:
        # Text-only fallback: add ai_data context to prompt
        context_parts = []
        if ai_data:
            if ai_data.get("main_topic"):
                context_parts.append(f"Тема: {ai_data['main_topic'][:500]}")
            if ai_data.get("timestamps"):
                context_parts.append(f"Структура:\n{ai_data['timestamps'][:1000]}")
            if ai_data.get("questions"):
                qs = ai_data["questions"]
                if isinstance(qs, list):
                    context_parts.append("Вопросы для размышления:\n" + "\n".join(str(q) for q in qs[:8]))
        if context_parts:
            prompt += "\n\nКОНТЕКСТ МАТЕРИАЛА:\n" + "\n\n".join(context_parts)
    
    try:
        # Pick client: prefer the one that already has audio_part uploaded
        client = existing_client if existing_client in GEMINI_CLIENTS else GEMINI_CLIENTS[0]
        
        contents = []
        if existing_audio_part and existing_client is client:
            contents.append(existing_audio_part)
        contents.append(prompt)
        
        config = make_text_config_smart(
            max_output_tokens=8000,
            model_name=GEMINI_MODEL,
            thinking_level="medium",  # Balance quality/cost
            response_mime_type="application/json",
        )
        
        resp = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config=config,
            ),
            timeout=120.0,
        )
        
        raw = getattr(resp, "text", "") or ""
        if not raw:
            # Try extracting from candidates
            try:
                for part in resp.candidates[0].content.parts:
                    if not getattr(part, "thought", False) and getattr(part, "text", None):
                        raw = part.text
                        break
            except Exception:
                pass
        
        if not raw:
            logger.warning("[Quiz] Gemini returned empty response")
            return None
        
        questions = _parse_quiz_json(raw)
        if not questions:
            logger.warning("[Quiz] Failed to parse quiz JSON: %s", raw[:200])
            return None
        
        logger.info("[Quiz] Generated %d questions", len(questions))
        return questions[:count]
        
    except Exception as e:
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
            # Telegram quiz question limit: 300 chars total
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
            # Small delay between polls to avoid flood
            if i < len(questions) - 1:
                await asyncio.sleep(1.5)
        except Exception as e:
            logger.warning("[Quiz] Failed to send poll %d: %s", i+1, str(e)[:120])
            # If we get flood-limited, wait and continue
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
