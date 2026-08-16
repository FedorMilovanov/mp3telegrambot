#!/usr/bin/env python3
"""AUDIT R38 — исправления, найденные глубоким ревью фиксов R26–R37 (три
параллельных ревьюера). Каждый пункт — конкретный баг/регрессия из ревью.
"""
import asyncio
import types
from pathlib import Path

import pytest


# ── R34b: _titles_match больше не подменяет чужую книгу ────────────────────
def test_r34b_generic_one_word_title_no_crossmap():
    from core.source_titles import official_ru_title, normalize_source_card_line
    # «Holiness» Райла (одно слово) НЕ должен цеплять чужие/другие книги
    assert official_ru_title("J.C. Ryle", "The Holiness of God") == ""
    assert official_ru_title("J.C. Ryle", "Holiness, Repentance and Faith") == ""
    # точное совпадение и «модель сократила» — работают
    assert official_ru_title("J.C. Ryle", "Holiness") == "Святость"
    assert official_ru_title("John Owen", "Of the Mortification of Sin") == "Об умерщвлении греха в верующих"
    # через публичный путь: Sproul-книга у Райла не переименовывается в «Святость»
    out = normalize_source_card_line("**The Holiness of God**, Дж. Ч. Райл (J.C. Ryle).")
    assert "Святость Бога" not in out and out.startswith("**The Holiness of God**")


def test_r34b_no_stray_period_before_why():
    from core.source_titles import normalize_source_card_line as N
    out = N("**Strange Fire**, Джон МакАртур (John MacArthur) — критика движения.")
    assert ") — критика" in out          # каноничное тире
    assert "). —" not in out             # без точки перед тире


# ── R35b: хэштеги — фильтр пустых ДО среза [:4] ────────────────────────────
def test_r35b_filter_empties_before_slice():
    from services.render_clips_montage import _hashtags_line
    assert _hashtags_line(["", "#a", "#b", "#c", "#d", "#e"]) == "#a #b #c #d"
    assert _hashtags_line(["# Сила"]) == "#Сила"            # внутренний пробел убран
    assert _hashtags_line(["##X", "#"]) == "#X"             # двойной # и голый # обработаны


# ── R26b: 500/503 в HTTP-контексте, не как голое число ─────────────────────
@pytest.mark.parametrize("msg,want", [
    ("prompt exceeds 500 characters", "PERMANENT"),
    ("400 INVALID_ARGUMENT: field exceeds 500 characters", "SCHEMA"),
    ("503 UNAVAILABLE. high demand", "OVERLOADED"),
    ("500 INTERNAL. {'error': {'code': 500}}", "OVERLOADED"),
    ("429 RESOURCE_EXHAUSTED quota", "QUOTA"),
])
def test_r26b_overload_context(msg, want):
    from services.gemini_error_policy import classify_gemini_error
    assert classify_gemini_error(ValueError(msg)).kind.name == want


# ── R27b: _crop_consensus учитывает число сэмплов ──────────────────────────
def test_r27b_crop_consensus_uses_sample_count():
    from services.ffmpeg import _crop_consensus
    C = "396:270:72:44"
    assert _crop_consensus({C: 3}, 3) == C          # все согласны
    assert _crop_consensus({C: 2}, 3) == C          # 2/3 — кворум
    assert _crop_consensus({C: 1}, 3) == ""         # 1 из 3 (2 сэмпла упали) — НЕ доверяем
    assert _crop_consensus({C: 2}, 2) == C          # 2/2
    assert _crop_consensus({C: 1}, 1) == C          # единственный сэмпл — принимаем
    assert _crop_consensus({"a": 1, "b": 1, "c": 1}, 3) == ""   # раздрай


# ── R30b: обрыв отправки посреди викторины не глотает итог ─────────────────
def test_r30b_send_failure_still_shows_score():
    import services.quiz_sessions as qs
    import services.quiz_generator as qg
    qg._sanitize_poll_payload = lambda q, index, total: {
        "question": q["question"], "options": q["options"], "correct": q["correct"], "explanation": "x"}

    class Bot:
        def __init__(self): self.n = 0; self.polls = []; self.messages = []
        async def send_poll(self, **kw):
            self.n += 1
            if self.n == 3:
                raise RuntimeError("network blip")   # 3-я отправка (Q3) падает
            self.polls.append(kw)
            return types.SimpleNamespace(poll=types.SimpleNamespace(id=f"p{self.n}"))
        async def send_message(self, **kw): self.messages.append(kw)

    def upd():
        return types.SimpleNamespace(effective_chat=types.SimpleNamespace(id=1),
                                     message=types.SimpleNamespace(message_id=1))
    def ans(pid):
        return types.SimpleNamespace(poll_answer=types.SimpleNamespace(poll_id=pid, option_ids=[0]))

    async def run():
        qs._polls.clear()
        Q = [{"question": f"Q{i}", "options": ["a", "b", "c", "d"], "correct": 0} for i in range(5)]
        ctx = types.SimpleNamespace(bot=Bot())
        await qs.start_quiz_session(Q, upd(), ctx, title="T")   # send #1 (Q1)
        await qs.handle_quiz_poll_answer(ans("p1"), ctx)        # correct → Q2 (send #2)
        await qs.handle_quiz_poll_answer(ans("p2"), ctx)        # correct → Q3 send FAILS
        return ctx
    ctx = asyncio.run(run())
    assert ctx.bot.messages, "итоговый счёт не показан — сессия потерялась"
    assert "2/2" in ctx.bot.messages[-1]["text"]   # total = отвеченные, не полный набор
    assert len(qs._polls) == 0                     # ничего не подвисло


def test_r30b_prune_removes_stale():
    import services.quiz_sessions as qs
    qs._polls.clear()
    qs._polls["old"] = {"session": {}, "correct_option": 0, "ts": qs.time.monotonic() - 9999}
    qs._polls["new"] = {"session": {}, "correct_option": 0, "ts": qs.time.monotonic()}
    qs._prune_polls()
    assert list(qs._polls) == ["new"]


# ── R29b: тяжёлые NVENC-проходы серилизованы семафором ─────────────────────
def test_r29b_all_nvenc_encodes_under_semaphore():
    sv = Path("services/shorts_video_impl.py").read_text(encoding="utf-8")
    # render + postprocess + burn = минимум 3 GPU-семафора
    assert sv.count("_sched.gpu_render") >= 3
    mont = Path("services/render_clips_montage.py").read_text(encoding="utf-8")
    # per-fragment render + concat = минимум 2
    assert mont.count("_sched.gpu_render") >= 2


# ── R28b: статикой считаем только доминирующий freeze ──────────────────────
def test_r28b_static_requires_dominant_freeze():
    src = Path("services/shorts_static_policy.py").read_text(encoding="utf-8")
    assert "freeze_duration" in src
    assert "freeze_ratio >= freeze_min" in src
    assert 'SHORTS_STATIC_FREEZE_RATIO_MIN", 0.86' in src

