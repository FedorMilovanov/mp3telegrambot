from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one old block, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "core/database.py",
    """# ─── Gemini модели (актуализировано 2026-08-03) ─────────────────────────
# История: до v7 — 2.5-pro/1.5-pro (платные); v7-v8 — 3.1-pro (ПЛАТНАЯ, убрана)
# Сейчас: gemini-3.7-flash — GA 21.07.2026, основной production-маршрут
# Сильный fallback: gemini-3.5-flash; механические задачи: gemini-3.5-flash-lite.
# gemini-3.1-flash-lite не используется проектом и имеет запланированную миграцию.
# Preview/legacy-модели не используются как production fallback.
# ВАЖНО: смена GEMINI_MODEL автоматически инвалидирует кэш (model_mismatch в database.py)
""",
    """# ─── Gemini deployment policy (2026-08-17) ──────────────────────────────
# Heavy/semantic production route: gemini-3.7-flash + HIGH.
# Utility/mechanical route: gemini-3.5-flash-lite only.
# Semantic downgrade to 3.6/3.5/Lite is intentionally disabled; temporary
# capacity failures use bounded retries and configured API-key/client rotation.
# ВАЖНО: смена GEMINI_MODEL автоматически инвалидирует кэш (model_mismatch in database.py)
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

replace_once(
    "README.md",
    """## Gemini policy

Runtime выбирает модели до импорта AI-клиентов:

- основной quality-маршрут: `gemini-3.6-flash`;
- сильный fallback: `gemini-3.5-flash`;
- лёгкие механические задачи: `gemini-3.5-flash-lite`.

Рекомендуемая явная настройка:

```dotenv
GEMINI_LIGHT_MODEL=gemini-3.5-flash-lite
GEMINI_LIGHT_FALLBACK_MODELS=gemini-3.5-flash
GEMINI_LIGHT_ALLOW_MAIN_FALLBACK=1
```

Старое значение `GEMINI_LIGHT_MODEL=gemini-3.1-flash-lite` поддерживается только как миграционный вход: startup-policy автоматически заменяет его на актуальную модель. Не используйте 3.1 Lite в новой конфигурации.
""",
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
