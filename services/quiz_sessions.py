"""AUDIT R30 — последовательная викторина (по запросу пользователя, режим
«полная замена»).

Раньше бот кидал в чат 10 отдельных постов-опросов сразу пачкой. Теперь
один сгенерированный набор = один тест: бот шлёт ОДИН вопрос, ждёт ответ
пользователя (poll_answer), затем шлёт следующий, а в конце — счёт.

Состояние держим в памяти по poll_id (викторина короткая, живёт минуты).
На каждый чат — своя независимая сессия (одно видео = одна викторина).
Если сессия потеряется при рестарте бота — Telegram-quiz всё равно показал
правильный ответ и пояснение нативно, просто авто-следующий не придёт.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# poll_id -> {"session": <dict>, "correct_option": int}
_polls: dict[str, dict] = {}


def _payloads_from_questions(questions: list[dict]) -> list[dict]:
    from services.quiz_generator import _sanitize_poll_payload

    total = len(questions)
    out = []
    for i, q in enumerate(questions):
        p = _sanitize_poll_payload(q, index=i + 1, total=total)
        if p:
            out.append(p)
    return out


async def _send_next_poll(context, session: dict, reply_to=None) -> bool:
    """Отправляет текущий (session['index']) вопрос сессии. True если ушёл."""
    i = session["index"]
    payloads = session["payloads"]
    if i >= len(payloads):
        return False
    p = payloads[i]
    try:
        message = await context.bot.send_poll(
            chat_id=session["chat_id"],
            question=p["question"],
            options=p["options"],
            type="quiz",
            correct_option_id=p["correct"],
            explanation=p["explanation"],
            # НЕ анонимный — иначе Telegram не пришлёт poll_answer, и мы не
            # узнаем, что пользователь ответил, чтобы прислать следующий.
            is_anonymous=False,
            reply_to_message_id=reply_to,
        )
    except Exception as e:
        retry_after = getattr(e, "retry_after", None)
        if retry_after:
            await asyncio.sleep(float(retry_after) + 1)
            try:
                message = await context.bot.send_poll(
                    chat_id=session["chat_id"],
                    question=p["question"], options=p["options"], type="quiz",
                    correct_option_id=p["correct"], explanation=p["explanation"],
                    is_anonymous=False,
                )
            except Exception as e2:
                logger.warning("[QuizSession] send_poll retry failed: %s", str(e2)[:120])
                return False
        else:
            logger.warning("[QuizSession] send_poll failed: %s", str(e)[:120])
            return False
    poll = getattr(message, "poll", None)
    poll_id = getattr(poll, "id", None)
    if not poll_id:
        return False
    _polls[poll_id] = {"session": session, "correct_option": p["correct"]}
    return True


async def start_quiz_session(questions, update, context, title: str = "") -> int:
    """Запускает последовательную викторину: шлёт ПЕРВЫЙ вопрос.
    Возвращает число вопросов в тесте (0 если ничего не отправлено)."""
    payloads = _payloads_from_questions(questions)
    if not payloads:
        return 0
    session = {
        "chat_id": update.effective_chat.id,
        "title": title or "",
        "payloads": payloads,
        "index": 0,
        "correct": 0,
    }
    reply_to = getattr(getattr(update, "message", None), "message_id", None)
    ok = await _send_next_poll(context, session, reply_to=reply_to)
    if not ok:
        return 0
    logger.info("[QuizSession] started: %d вопросов '%s'", len(payloads), title[:40])
    return len(payloads)


async def handle_quiz_poll_answer(update, context) -> None:
    """PollAnswerHandler: пользователь ответил на вопрос — считаем результат
    и шлём следующий вопрос (или итоговый счёт)."""
    answer = getattr(update, "poll_answer", None)
    if not answer:
        return
    entry = _polls.pop(answer.poll_id, None)
    if entry is None:
        return  # чужой/устаревший опрос (напр. после рестарта)
    session = entry["session"]
    chosen = answer.option_ids[0] if answer.option_ids else -1
    if chosen == entry["correct_option"]:
        session["correct"] += 1
    session["index"] += 1

    if session["index"] < len(session["payloads"]):
        await asyncio.sleep(0.3)
        await _send_next_poll(context, session)
        return

    # Викторина окончена — итог.
    correct = session["correct"]
    total = len(session["payloads"])
    _pct = round(100 * correct / total) if total else 0
    _mark = "🏆" if _pct >= 80 else ("👍" if _pct >= 50 else "📚")
    title = session.get("title") or ""
    try:
        await context.bot.send_message(
            chat_id=session["chat_id"],
            text=(
                f"{_mark} <b>Викторина окончена</b>\n"
                f"Правильных ответов: <b>{correct}/{total}</b> ({_pct}%)"
                + (f"\n«{title[:80]}»" if title else "")
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.info("[QuizSession] final score not sent: %s", str(e)[:120])
