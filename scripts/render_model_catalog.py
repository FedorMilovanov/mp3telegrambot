#!/usr/bin/env python3
"""Render every model identifier in configs/docs from ``core.model_catalog``.

Usage::

    python scripts/render_model_catalog.py            # rewrite stale files
    python scripts/render_model_catalog.py --check    # CI mode: fail if stale

The script owns *all* generated model strings:

* ``.env.example`` / ``config/shorts_factory.env.example`` - ``KEY=value`` lines
* ``README.md`` - model matrix block
* ``docs/gemini-operations.md`` - model policy block
* ``docs/model-catalog.md`` - full generated catalog page
* ``scripts/migrate-gemini-38.ps1`` - ``Set-EnvValue -Name ... -Value ...`` lines

A catalog bump is therefore: edit ``core/model_catalog.py``, run this script,
run ``tools/verify_repo.py``.  Production code needs no edits at all: it imports
roles (``MODEL_PRIMARY`` / ``MODEL_UTILITY`` / ...) from the catalog.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.model_catalog import (  # noqa: E402  (path bootstrap above)
    CATALOG_VERSION,
    _MODELS,
    FREE_TIER_ONLY,
    FREE_TIER_SOURCE,
    ROLE_MODELS,
    MODEL_LIVE,
    MODEL_PRIMARY,
    MODEL_REASONING,
    MODEL_TRANSCRIBE,
    MODEL_TTS,
    MODEL_TTS_LITE,
    MODEL_UTILITY,
    ROLE_ENV,
    access_limited_models,
    deprecated_aliases,
    free_tier_models,
    paid_only_models,
    role_free_tier,
    scheduled_migrations,
    shutdown_models,
    unverified_free_tier_models,
)

def _paid_only_examples(limit: int = 1) -> str:
    """Flagship paid-only model id for prose, straight from the catalog.

    Declaration order, not alphabetical: the catalog lists the flagship first,
    which is the one an operator actually wonders about.
    """
    names = [name for name in _MODELS if name in paid_only_models()]
    if not names:
        return "платные модели"
    return ", ".join(names[:limit])


GENERATED_NOTE = (
    "Сгенерировано scripts/render_model_catalog.py из core/model_catalog.py — "
    "не редактируйте вручную."
)

# Env variables that must follow a catalog role.
ENV_ROLE_MAP: dict[str, str] = {
    "GEMINI_MODEL": MODEL_PRIMARY,
    "SHORTS_FACTORY_MODEL": MODEL_PRIMARY,
    "LIVEDUB_INFO_MODEL": MODEL_PRIMARY,
    "LIVEDUB_QUICK_QA_MODEL": MODEL_PRIMARY,
    "LIVEDUB_LONG_QA_MODEL": MODEL_PRIMARY,
    "LIVEDUB_QA_VERIFY_MODEL": MODEL_PRIMARY,
    "MP3BOT_PRIMARY_MODEL": MODEL_PRIMARY,
    "MP3BOT_UTILITY_MODEL": MODEL_UTILITY,
    "MP3BOT_TTS_MODEL": MODEL_TTS,
    "MP3BOT_TTS_LITE_MODEL": MODEL_TTS_LITE,
    "MP3BOT_TRANSCRIBE_MODEL": MODEL_TRANSCRIBE,
    "MP3BOT_LIVE_MODEL": MODEL_LIVE,
    "MP3BOT_REASONING_MODEL": MODEL_REASONING,
}

_ROLE_ROWS = (
    ("primary", "semantic", "Synopsis, LiveDub QA, Factory, publication, info cards"),
    ("utility", "mechanical", "только служебные/косметические задачи"),
    ("fallback", "downgrade", "по умолчанию выключен: cross-model downgrade запрещён"),
    ("tts", "speech", "доступно, пока не используется"),
    ("tts_lite", "speech", "доступно, пока не используется"),
    ("transcribe", "speech", "доступно, пока не используется"),
    ("live", "realtime", "доступно, пока не используется"),
    ("reasoning", "reasoning", "нет бесплатной модели: 4 Argon только paid tier"),
)

_FREE_TIER_MARK = {True: "✅ free", False: "❌ paid only", None: "—"}


def _role_table() -> str:
    lines = [
        "| Роль | Модель | Free tier | Кто использует |",
        "|---|---|---|---|",
    ]
    for role, kind, usage in _ROLE_ROWS:
        model = ROLE_MODELS.get(role, "")
        free = _FREE_TIER_MARK[role_free_tier(role)]
        lines.append(
            f"| `{role}` ({kind}) | `{model or '(не задан)'}` | {free} | {usage} |"
        )
    return "\n".join(lines) + "\n"


def _free_tier_section() -> str:
    free = sorted(free_tier_models())
    paid = sorted(paid_only_models())
    unknown = sorted(unverified_free_tier_models())
    parts = [
        "### Бесплатные ключи (основное ограничение deployment'а)",
        "",
        f"Режим: `FREE_TIER_ONLY = {FREE_TIER_ONLY}` — проект работает на бесплатных "
        "ключах Gemini, поэтому платная модель не может быть маршрутом по умолчанию.",
        f"Источник: {FREE_TIER_SOURCE}.",
        "",
        "**Бесплатно (free tier):**",
        "",
        ", ".join(f"`{model}`" for model in free),
        "",
        "**Только paid tier — бесплатный ключ получит отказ:**",
        "",
        ", ".join(f"`{model}`" for model in paid) or "—",
        "",
        "**Не проверено / модель выключена:**",
        "",
        ", ".join(f"`{model}`" for model in unknown) or "—",
        "",
        "Что это значит на практике:",
        "",
        "- лимиты free tier считаются **на Google Cloud project и на модель**, а не "
        "на ключ; несколько ключей одного проекта квоту не умножают;",
        "- четыре слота `GEMINI_API_KEY`..`_4` имеют смысл только как четыре разных "
        "проекта (`GEMINI_QUOTA_DOMAIN*` фиксирует это локально);",
        "- апгрейд на платную модель — это не «одна строка», а осознанное решение: "
        "сначала `FREE_TIER_ONLY = False`, потом правка роли;",
        "- `reasoning`-роль пуста намеренно: бесплатной reasoning-модели у Google нет.",
        "",
    ]
    return "\n".join(parts)


def _override_lines() -> str:
    lines = [f"# {GENERATED_NOTE}"]
    lines.append("# Роль можно переопределить; устаревший alias нормализуется автоматически.")
    for role, names in ROLE_ENV.items():
        for name in names:
            # GEMINI_MODEL is already documented (and active) above.
            if name in ENV_ROLE_MAP and name != "GEMINI_MODEL":
                lines.append(f"# {name}={ENV_ROLE_MAP[name]}")
    lines.append("# MP3BOT_FALLBACK_MODEL=")
    lines.append("# ↑ пусто = cross-model downgrade выключен (рекомендуется).")
    lines.append(
        "# ↑ reasoning пуст намеренно: бесплатной reasoning-модели нет; "
        f"{_paid_only_examples()} — только paid tier."
    )
    lines.append(
        "# Платные модели (недоступные на free tier) отклоняются: причина "
        "пишется в лог при старте."
    )
    return "\n".join(lines)


def _route_contract() -> str:
    """Compact role block for feature docs that must name the route."""
    lines = [
        f"Model contract, catalog snapshot {CATALOG_VERSION}. "
        "Generated from core/model_catalog.py by scripts/render_model_catalog.py; "
        "do not edit by hand.",
        "",
        f"- Role `primary` (semantic work): `{MODEL_PRIMARY}`.",
        f"- Role `utility` (mechanical work only): `{MODEL_UTILITY}`.",
    ]
    dep = deprecated_aliases()
    if dep:
        aliases = ", ".join(
            f"`{model}` (deprecated {since}) routes to `{replacement}`"
            for model, (since, replacement) in sorted(dep.items())
        )
        lines.append(f"- Provider-side aliases: {aliases}.")
    lines.append(
        "- Any other identifier (Pro/Lite, previous GA generation, arbitrary "
        "string) is rejected by the runtime gate instead of silently changing "
        "the quality route."
    )
    return "\n".join(lines)


def _catalog_section() -> str:
    dep = deprecated_aliases()
    mig = scheduled_migrations()
    limited = access_limited_models()
    dead = sorted(shutdown_models())
    parts = [
        f"Каталог моделей (снимок {CATALOG_VERSION}). {GENERATED_NOTE}",
        "",
        "### Роли",
        "",
        _role_table(),
        _free_tier_section(),
        "### Устаревшие alias'ы (провайдер сам перенаправляет запрос)",
        "",
        "| Alias | Deprecated | Реальная модель |",
        "|---|---|---|",
    ]
    for model, (since, replacement) in sorted(dep.items()):
        parts.append(f"| `{model}` | {since} | `{replacement}` |")
    parts += [
        "",
        "### Объявленные миграции",
        "",
        "| Модель | Крайний срок | Замена |",
        "|---|---|---|",
    ]
    for model, (deadline, replacement) in sorted(mig.items(), key=lambda kv: kv[1][0]):
        parts.append(f"| `{model}` | {deadline} | `{replacement}` |")
    parts += [
        "",
        "### Legacy, доступ ограничен (не deprecated)",
        "",
        "| Модель | Замена |",
        "|---|---|",
    ]
    for model, replacement in sorted(limited.items()):
        parts.append(f"| `{model}` | `{replacement}` |")
    parts += [
        "",
        "### Отключены",
        "",
        ", ".join(f"`{model}`" for model in dead) or "—",
        "",
    ]
    return "\n".join(parts)


def _replace_block(text: str, block: str, *, marker: str = "MODEL-CATALOG") -> str:
    start = f"<!-- {marker}:START -->"
    end = f"<!-- {marker}:END -->"
    pattern = re.compile(
        re.escape(start) + r".*?" + re.escape(end), re.DOTALL
    )
    replacement = f"{start}\n{block.rstrip()}\n{end}"
    if not pattern.search(text):
        raise SystemExit(f"marker {marker} not found")
    return pattern.sub(lambda _m: replacement, text, count=1)


def _render_env(text: str) -> str:
    for name, value in ENV_ROLE_MAP.items():
        pattern = re.compile(rf"^(\s*#?\s*{re.escape(name)})=(\S*)\s*$", re.MULTILINE)
        if pattern.search(text):
            text = pattern.sub(lambda m, value=value: f"{m.group(1)}={value}", text)
    return text


def _render_ps1(text: str) -> str:
    for name, value in ENV_ROLE_MAP.items():
        pattern = re.compile(
            rf'(Set-EnvValue\s+-Name\s+"{re.escape(name)}"\s+-Value\s+)"[^"]*"'
        )
        if pattern.search(text):
            text = pattern.sub(lambda m, value=value: f'{m.group(1)}"{value}"', text)
    return text


def render_all(root: Path = ROOT) -> dict[Path, str]:
    """Return ``{path: expected_text}`` for every generated artifact."""
    out: dict[Path, str] = {}

    env_path = root / ".env.example"
    if env_path.is_file():
        text = _render_env(env_path.read_text(encoding="utf-8"))
        out[env_path] = _replace_block(text, _override_lines(), marker="MODEL-OVERRIDES")

    factory_env = root / "config" / "shorts_factory.env.example"
    if factory_env.is_file():
        out[factory_env] = _render_env(factory_env.read_text(encoding="utf-8"))

    readme = root / "README.md"
    if readme.is_file():
        out[readme] = _replace_block(
            _render_env(readme.read_text(encoding="utf-8")),
            _catalog_section(),
            marker="MODEL-CATALOG",
        )

    operations = root / "docs" / "gemini-operations.md"
    if operations.is_file():
        out[operations] = _replace_block(
            operations.read_text(encoding="utf-8"),
            _catalog_section(),
            marker="MODEL-CATALOG",
        )

    for feature_doc in ("docs/shorts_factory_max.md", "docs/translation_editorial_review.md"):
        path = root / feature_doc
        if path.is_file():
            out[path] = _replace_block(
                _render_env(path.read_text(encoding="utf-8")),
                _route_contract(),
                marker="MODEL-ROUTE",
            )

    catalog_doc = root / "docs" / "model-catalog.md"
    out[catalog_doc] = (
        "# Каталог моделей Gemini\n\n"
        f"Policy: `project-gemini-routing-{CATALOG_VERSION}`. "
        f"{GENERATED_NOTE}\n\n"
        "## Как менять модель\n\n"
        "1. Отредактируйте одну строку в `core/model_catalog.py` — `ROLE_MODELS[ROLE_PRIMARY]`\n"
        "   (для служебной модели — `ROLE_UTILITY`). Новая модель обязана быть\n"
        "   доступна на free tier: платная модель ломает все бесплатные ключи.\n"
        "2. Запустите `python scripts/render_model_catalog.py`.\n"
        "3. Запустите `python tools/verify_repo.py`.\n\n"
        "Продовый код импортирует роли (`MODEL_PRIMARY`, `MODEL_UTILITY`, ...), а не\n"
        "конкретные идентификаторы, поэтому правки в сервисах не нужны.\n"
        "Генератор обновляет `.env.example`, `config/shorts_factory.env.example`,\n"
        "`README.md`, `docs/gemini-operations.md`, `docs/shorts_factory_max.md`,\n"
        "`docs/translation_editorial_review.md` и `scripts/migrate-gemini-38.ps1`.\n\n"
        "### Проверка схемы (upgrade drill)\n\n"
        "```bash\n"
        # No concrete id: the drill picks a free-tier target from the catalog,
        # and a literal here would go stale on the next upgrade.
        "python scripts/model_upgrade_drill.py --model <free-tier-model-id>\n"
        "```\n\n"
        "Дрилл подменяет `ROLE_PRIMARY`, убеждается, что `--check` фиксирует\n"
        "рассинхронизацию, прогоняет генератор и контрактные тесты, затем\n"
        "восстанавливает файл из бэкапа. Зелёный прогон = апгрейд модели не требует\n"
        "правок в коде. Если тест начнёт падать — значит в нём появился литерал\n"
        "модели вместо роли.\n\n"
        + _catalog_section()
        + "\n## Переопределения\n\n"
        "| Переменная | Роль |\n|---|---|\n"
        + "\n".join(
            f"| `{name}` | `{role}` |" for role, names in ROLE_ENV.items() for name in names
        )
        + "\n| `MP3BOT_FALLBACK_MODEL` | `fallback` (пусто = выключено) |\n"
    )

    migrate = root / "scripts" / "migrate-gemini-38.ps1"
    if migrate.is_file():
        out[migrate] = _render_ps1(migrate.read_text(encoding="utf-8"))

    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when a generated artifact is out of date",
    )
    args = parser.parse_args(argv)

    stale: list[Path] = []
    for path, expected in render_all().items():
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        if current == expected:
            continue
        stale.append(path)
        if not args.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expected, encoding="utf-8")

    if not stale:
        print("model catalog: generated artifacts are up to date")
        return 0
    if args.check:
        print("model catalog: stale generated artifacts:", file=sys.stderr)
        for path in stale:
            print(f"  - {path.relative_to(ROOT)}", file=sys.stderr)
        print(
            "run: python scripts/render_model_catalog.py",
            file=sys.stderr,
        )
        return 1
    for path in stale:
        print(f"updated {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
