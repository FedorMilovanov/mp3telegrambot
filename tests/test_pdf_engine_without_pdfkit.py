#!/usr/bin/env python3
"""PDF-движок не должен зависеть от python-обёртки pdfkit.

Найденная ошибка: `_gen_wkhtmltopdf` начинался с `if not _HAS_PDFKIT: return
None`, то есть прямой вызов бинарника wkhtmltopdf отключался у всех, у кого
нет пакета pdfkit. Ни одна строка движка pdfkit не использовала — он был
чистым рудиментом, при этом единственным пакетом в lock с неустранимой
уязвимостью (PYSEC-2026-2860, фикса нет). Пакет удалён; здесь проверяется,
что движок действительно работает от одного бинарника и что рудимент не
вернулся.
"""
from __future__ import annotations

import ast
import os
import stat
import sys
from pathlib import Path

import pytest

from services import pdf_generator


def _fake_wkhtmltopdf(tmp_path: Path) -> Path:
    """Скрипт-заглушка: пишет «PDF» больше _MIN_PDF_SIZE в последний аргумент."""
    script = tmp_path / "wkhtmltopdf"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "open(sys.argv[-1], 'wb').write(b'%PDF-1.4\\n' + b'x' * 4096)\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


@pytest.mark.skipif(os.name == "nt", reason="заглушка-шелл-скрипт для POSIX")
def test_cli_engine_runs_without_the_pdfkit_package(tmp_path: Path) -> None:
    out = tmp_path / "sermon.pdf"
    result = pdf_generator._gen_wkhtmltopdf(
        "<h1>Тест</h1>", str(out), "Название", "Исполнитель", str(_fake_wkhtmltopdf(tmp_path))
    )
    assert result == str(out), "движок выключился, хотя бинарник на месте"
    assert out.stat().st_size > pdf_generator._MIN_PDF_SIZE


@pytest.mark.skipif(os.name == "nt", reason="заглушка-шелл-скрипт для POSIX")
def test_cli_engine_reports_missing_binary_honestly(tmp_path: Path) -> None:
    out = tmp_path / "sermon.pdf"
    assert pdf_generator._gen_wkhtmltopdf("<h1>x</h1>", str(out), "t", "p", "") is None
    assert not out.exists()


def test_pdfkit_is_gone_from_the_code_and_the_lock() -> None:
    # Docstrings and comments may still tell the story of the old gate; what
    # must not come back is executable code depending on the wrapper. Parse the
    # AST rather than grep, so prose cannot produce a false green.
    tree = ast.parse(Path("services/pdf_generator.py").read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "pdfkit" not in imported
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "_HAS_PDFKIT" not in names

    for path in ("requirements.txt", "requirements-lock.txt"):
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        declared = [ln for ln in lines if ln.strip().startswith("pdfkit")]
        assert not declared, f"{path} всё ещё объявляет pdfkit: {declared}"


def test_options_helper_is_named_after_the_binary_it_feeds() -> None:
    options = pdf_generator._wkhtmltopdf_options("Заголовок", "Автор")
    assert options["page-size"] == "A4"
    assert options["header-left"] == "Автор \u2014 Заголовок"
    assert options["footer-center"] == "[page] / [topage]"
    assert not hasattr(pdf_generator, "_pdfkit_options")
