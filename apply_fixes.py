#!/usr/bin/env python3
"""
apply_fixes.py — автоматически вносит 5 критических фиксов в код MP3Bot.
Просто положи этот файл в папку mp3telegrambot и запусти:
    python apply_fixes.py
"""
import sys
from pathlib import Path

def read_file(p: Path) -> str:
    return p.read_text(encoding="utf-8")

def write_file(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8")
    print(f"  [OK] {p}")

# ── FIX 1: core/database.py — default GEMINI_MODEL ──────────────────
def fix_database():
    p = Path("core/database.py")
    t = read_file(p)
    old = 'GEMINI_MODEL  = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")'
    new = 'GEMINI_MODEL  = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")'
    if old not in t:
        print(f"  [SKIP] {p}: pattern not found (maybe already fixed)")
        return
    t = t.replace(old, new, 1)
    write_file(p, t)

# ── FIX 2: core/globals.py — убрать audio_timestamp (1.x SDK не поддерживает) ──
def fix_globals():
    p = Path("core/globals.py")
    t = read_file(p)
    old = '''def make_audio_config(temperature: float = 0.1, max_output_tokens: int = 65536):
    if not HAS_GEMINI or types is None:
        return None
    try:
        return types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            audio_timestamp=True,
        )
    except TypeError:
        # Старая версия google-genai SDK без audio_timestamp — fallback
        logger.warning("google-genai SDK не поддерживает audio_timestamp — обновите версию")
        return types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )'''

    new = '''def make_audio_config(temperature: float = 0.1, max_output_tokens: int = 65536):
    if not HAS_GEMINI or types is None:
        return None
    # AUDIT FIX 2026-05-20: google-genai 1.x не поддерживает audio_timestamp на уровне API.
    # В будущем (SDK >=2.0.0) можно вернуть: audio_timestamp=True для точных таймкодов audio-only.
    return types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )'''
    if old not in t:
        print(f"  [SKIP] {p}: pattern not found (maybe already fixed)")
        return
    t = t.replace(old, new, 1)
    write_file(p, t)

# ── FIX 3: converters/md_telegraph.py — author_url parameter ───────
def fix_md_telegraph():
    p = Path("converters/md_telegraph.py")
    t = read_file(p)
    old = "async def _create_telegraph_page_single(title: str, author: str,\n                                         nodes: list, loop) -> tuple:"
    new = "async def _create_telegraph_page_single(title: str, author: str,\n                                         nodes: list, loop, author_url: str = \"\") -> tuple:"
    if old not in t:
        print(f"  [SKIP] {p}: pattern not found (maybe already fixed)")
        return
    t = t.replace(old, new, 1)
    write_file(p, t)

# ── FIX 4: main.py — warning про gemini-2.5-pro + paid-only ──────────
def fix_main():
    p = Path("main.py")
    t = read_file(p)
    anchor = '''            "'gemini-3-flash-preview' (новая, free tier).",
            GEMINI_MODEL,
        )
    elif GEMINI_MODEL not in _KNOWN_LIVE_MODELS:'''
    insert = '''            "'gemini-3-flash-preview' (новая, free tier).",
            GEMINI_MODEL,
        )
    elif GEMINI_MODEL == "gemini-2.5-pro":
        logger.warning(
            "⚠️  GEMINI_MODEL='gemini-2.5-pro' — с 1 апреля 2026 Pro-модели "
            "требуют платного биллинга (free tier больше не работает). "
            "Если ключи free tier — все запросы получат 429/quota error, "
            "и бот будет выдавать только базовую информацию без анализа. "
            "Рекомендуется GEMINI_MODEL='gemini-2.5-flash' в .env"
        )
    elif GEMINI_MODEL not in _KNOWN_LIVE_MODELS:'''
    if insert in t:
        print(f"  [SKIP] {p}: already fixed")
        return
    if anchor not in t:
        print(f"  [FAIL] {p}: anchor not found")
        return
    t = t.replace(anchor, insert, 1)
    write_file(p, t)

# ── FIX 5: services/gemini_analyze.py — log finish_reason ───────────
def fix_gemini_analyze():
    p = Path("services/gemini_analyze.py")
    t = read_file(p)
    anchor = '''        if response is None:
            raise last_err or RuntimeError("Все Gemini-клиенты недоступны")

        # BUG-B04: response.text может быть None (thinking-only) или ValueError (safety filter)'''
    insert = '''        if response is None:
            raise last_err or RuntimeError("Все Gemini-клиенты недоступны")

        # AUDIT DIAG: логируем финальный finish_reason и длину ответа
        try:
            _fin = response.candidates[0].finish_reason if response.candidates else "NO_CANDIDATES"
            logger.info(f"Gemini response: finish_reason={_fin}")
        except Exception:
            pass

        # BUG-B04: response.text может быть None (thinking-only) или ValueError (safety filter)'''
    if insert in t:
        print(f"  [SKIP] {p}: already fixed")
        return
    if anchor not in t:
        print(f"  [FAIL] {p}: anchor not found")
        return
    t = t.replace(anchor, insert, 1)
    write_file(p, t)

# ── RUN ─────────────────────────────────────────────────────────────
def main():
    print("Applying MP3Bot critical fixes (2026-05-20)...\n")
    fix_database()
    fix_globals()
    fix_md_telegraph()
    fix_main()
    fix_gemini_analyze()
    print("\nDone. Restart the bot: python bot_new.py")

if __name__ == "__main__":
    main()
