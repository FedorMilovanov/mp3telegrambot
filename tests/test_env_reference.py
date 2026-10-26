#!/usr/bin/env python3
"""``docs/env-reference.md`` обязан совпадать с кодом.

Аудит: 89 переменных читались кодом и не были описаны нигде (``.env.example``
документирует 100 из 174). Требовать, чтобы курируемая инструкция описывала
все технические имена, неправильно; требовать, чтобы полного перечня не было
вообще, — тоже. Поэтому перечень генерируется из кода и проверяется на
актуальность, как каталог моделей.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import scripts.render_env_reference as env_reference
from scripts.render_env_reference import SYSTEM_VARS, collect

REFERENCE = Path("docs/env-reference.md")
SCRIPT = Path("scripts/render_env_reference.py")


def test_reference_is_in_sync_with_the_code() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"docs/env-reference.md устарел: выполните python {SCRIPT.as_posix()}\n{result.stdout}{result.stderr}"
    )


def test_collection_is_invariant_to_multiline_formatting(tmp_path, monkeypatch) -> None:
    services = tmp_path / "services"
    services.mkdir()
    source = services / "example.py"
    monkeypatch.setattr(env_reference, "ROOT", tmp_path)

    source.write_text(
        'import os\nvalue = os.getenv(\n    "MULTILINE_ENV", "fallback"\n)\n',
        encoding="utf-8",
    )
    multiline = collect()
    source.write_text(
        'import os\nvalue = os.getenv("MULTILINE_ENV", "fallback")\n',
        encoding="utf-8",
    )
    joined = collect()

    assert multiline == joined
    assert joined["MULTILINE_ENV"].defaults == {"fallback"}
    assert joined["MULTILINE_ENV"].files == {"services/example.py"}


def test_every_collected_variable_has_a_row() -> None:
    reference = REFERENCE.read_text(encoding="utf-8")
    missing = [name for name in collect() if f"| `{name}` |" not in reference]
    assert not missing, f"в справочнике нет строк для: {sorted(missing)}"


def test_system_variables_are_not_dumped_into_the_reference() -> None:
    """PATH/TEMP/CI — не наша конфигурация; их присутствие только шумит."""
    reference = REFERENCE.read_text(encoding="utf-8")
    leaked = [name for name in SYSTEM_VARS if f"| `{name}` |" in reference]
    assert not leaked, f"в справочник попали системные переменные: {leaked}"


def test_documented_columns_are_populated() -> None:
    reference = REFERENCE.read_text(encoding="utf-8")
    rows = [line for line in reference.splitlines() if line.startswith("| `")]
    assert len(rows) >= 100, f"подозрительно мало строк: {len(rows)}"

    for row in rows:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        assert len(cells) == 5, row
        assert cells[1], f"не указано, где читается: {row}"
        assert cells[3] in {"да", "—"}, row
        assert cells[4] in {"да", "—"}, row


def test_counts_in_the_header_match_the_body() -> None:
    reference = REFERENCE.read_text(encoding="utf-8")
    rows = [line for line in reference.splitlines() if line.startswith("| `")]
    header = re.search(r"Всего переменных: \*\*(\d+)\*\*", reference)
    assert header, "в заголовке нет счётчика переменных"
    assert int(header.group(1)) == len(rows), "счётчик в заголовке врёт"


def test_env_example_still_documents_the_operator_facing_knobs() -> None:
    """Курируемая инструкция не должна опустеть на фоне автогенерации."""
    env = Path(".env.example").read_text(encoding="utf-8")
    for knob in ("BOT_TOKEN", "WHITELIST_IDS", "GEMINI_MODEL", "VOT_API_TOKEN"):
        assert knob in env, f"{knob} исчез из .env.example"
