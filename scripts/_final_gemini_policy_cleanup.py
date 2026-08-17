from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one occurrence, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_section(path: str, start_anchor: str, end_anchor: str, new: str, label: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    start = text.find(start_anchor)
    if start < 0:
        raise SystemExit(f"{label}: start anchor not found")
    if text.find(start_anchor, start + 1) >= 0:
        raise SystemExit(f"{label}: start anchor is not unique")
    end = text.find(end_anchor, start)
    if end < 0:
        raise SystemExit(f"{label}: end anchor not found")
    p.write_text(text[:start] + new + text[end:], encoding="utf-8")


replace_section(
    "core/database.py",
    "# ─── Gemini модели",
    'GEMINI_MODEL  = os.getenv(',
    """# ─── Gemini deployment policy (2026-08-17) ──────────────────────────────
# Heavy/semantic production route: gemini-3.7-flash + HIGH.
# Utility/mechanical route: gemini-3.5-flash-lite only.
# Semantic downgrade to 3.6/3.5/Lite is intentionally disabled; temporary
# capacity failures use bounded retries and configured API-key/client rotation.
# ВАЖНО: смена GEMINI_MODEL автоматически инвалидирует кэш (model_mismatch в database.py)
""",
    "database policy",
)

replace_once(
    "services/gemini_qa_policy.py",
    '_STRONG_FALLBACK_MODEL = "gemini-3.5-flash"',
    '_DISALLOWED_LEGACY_QA_MODEL = "gemini-3.5-flash"',
    "QA symbol",
)
replace_once(
    "services/gemini_qa_policy.py",
    """    # These are valid fallback/mechanical models, but not the approved primary
    # for semantic translation QA where false positives can trigger auto-muting.
    _STRONG_FALLBACK_MODEL,
""",
    """    # Lower-tier/stale models are migration inputs only, never QA fallbacks.
    # False positives here can trigger auto-muting, so they are upgraded to 3.7.
    _DISALLOWED_LEGACY_QA_MODEL,
""",
    "QA comment",
)

replace_section(
    "README.md",
    "## Gemini policy\n",
    "## LiveDub и два MP3\n",
    """## Gemini policy

Runtime фиксирует quality-policy до импорта AI-клиентов:

- все heavy/semantic задачи: `gemini-3.7-flash` + `HIGH` thinking;
- Shorts Factory MAX, LiveDub QA, публикационный смысловой текст и editorial review не понижаются до 3.6/3.5/Lite;
- дешёвые механические/utility-задачи: только `gemini-3.5-flash-lite` + minimal;
- при временной перегрузке используются bounded retry и следующие настроенные API-ключи/клиенты, а не более слабая модель.

Рекомендуемая явная настройка:

```dotenv
GEMINI_MODEL=gemini-3.7-flash
GEMINI_MAX_MODEL=gemini-3.7-flash
GEMINI_FORCE_THINKING_LEVEL=high
GEMINI_LIGHT_MODEL=gemini-3.5-flash-lite
GEMINI_LIGHT_FALLBACK_MODELS=
GEMINI_LIGHT_ALLOW_MAIN_FALLBACK=0
SHORTS_FACTORY_MODEL=gemini-3.7-flash
```

Старые/слабые значения в QA-контурах поддерживаются только как миграционный вход: startup-policy заменяет их на текущий quality route. Для локального `.env` используйте `scripts/migrate-gemini-37.ps1`.

""",
    "README policy",
)

print("FINAL_GEMINI_POLICY_CLEANUP_OK")
