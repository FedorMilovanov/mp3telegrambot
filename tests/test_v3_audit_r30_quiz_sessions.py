#!/usr/bin/env python3
"""AUDIT R30 (запрос оператора, «полная замена»): вместо пачки из 10 постов-
опросов сразу — один последовательный тест: вопрос → ответ → следующий →
счёт в конце. Продвижение через PollAnswerHandler.
"""
import asyncio
import types
from pathlib import Path

import pytest

import services.quiz_sessions as qs


class _FakePoll:
    def __init__(self, pid):
        self.id = pid


class _FakeMessage:
    def __init__(self, pid):
        self.poll = _FakePoll(pid)


class _FakeBot:
    def __init__(self):
        self.polls = []
        self.messages = []
        self._n = 0

    async def send_poll(self, **kw):
        self._n += 1
        self.polls.append(kw)
        return _FakeMessage(f"poll{self._n}")

    async def send_message(self, **kw):
        self.messages.append(kw)


class _FakeCtx:
    def __init__(self):
        self.bot = _FakeBot()


def _update():
    chat = types.SimpleNamespace(id=555)
    msg = types.SimpleNamespace(message_id=1)
    return types.SimpleNamespace(effective_chat=chat, message=msg)


def _answer(poll_id, option):
    pa = types.SimpleNamespace(poll_id=poll_id, option_ids=[option])
    return types.SimpleNamespace(poll_answer=pa)


@pytest.fixture(autouse=True)
def _passthrough_payload(monkeypatch):
    # Тестируем логику СЕССИИ, а не качество вопросов — payload делаем прямым.
    def _fake(q, *, index, total):
        return {
            "question": q["question"], "options": q["options"],
            "correct": q["correct"], "explanation": q.get("explanation", "x"),
        }
    monkeypatch.setattr("services.quiz_generator._sanitize_poll_payload", _fake)
    qs._polls.clear()
    yield
    qs._polls.clear()


def _questions(n=3):
    return [
        {"question": f"Q{i}?", "options": ["a", "b", "c", "d"], "correct": 0, "explanation": "e"}
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_only_one_poll_sent_at_start():
    ctx = _FakeCtx()
    started = await qs.start_quiz_session(_questions(3), _update(), ctx, title="T")
    assert started == 3
    # НЕ пачка из 3 — только ПЕРВЫЙ вопрос
    assert len(ctx.bot.polls) == 1
    assert ctx.bot.polls[0]["is_anonymous"] is False  # иначе не придёт poll_answer


@pytest.mark.asyncio
async def test_answering_advances_and_final_score():
    ctx = _FakeCtx()
    await qs.start_quiz_session(_questions(3), _update(), ctx, title="Тест")
    # ответить на Q1 верно
    await qs.handle_quiz_poll_answer(_answer("poll1", 0), ctx)
    assert len(ctx.bot.polls) == 2  # пришёл Q2
    # Q2 неверно
    await qs.handle_quiz_poll_answer(_answer("poll2", 1), ctx)
    assert len(ctx.bot.polls) == 3  # пришёл Q3
    # Q3 верно -> конец
    await qs.handle_quiz_poll_answer(_answer("poll3", 0), ctx)
    assert len(ctx.bot.polls) == 3  # больше опросов нет
    assert len(ctx.bot.messages) == 1  # итоговый счёт
    assert "2/3" in ctx.bot.messages[0]["text"]


@pytest.mark.asyncio
async def test_unknown_poll_answer_ignored():
    ctx = _FakeCtx()
    await qs.handle_quiz_poll_answer(_answer("nonexistent", 0), ctx)
    assert ctx.bot.polls == [] and ctx.bot.messages == []


def test_bulk_send_replaced_by_session():
    src = Path("services/quiz_generator.py").read_text(encoding="utf-8")
    assert "start_quiz_session" in src
    # старой пачки (цикл по всем вопросам с send_poll внутри) быть не должно
    assert "is_anonymous=True" not in src


def test_handler_registered_in_main():
    src = Path("main.py").read_text(encoding="utf-8")
    assert "PollAnswerHandler(handle_quiz_poll_answer)" in src
