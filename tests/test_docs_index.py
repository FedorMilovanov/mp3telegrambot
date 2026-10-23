#!/usr/bin/env python3
"""Индекс документации: генерируется, актуален и ничего не теряет.

До этого файла в ``docs/`` было 73 markdown-файла без индекса: 52 в корне
каталога и 21 в подкаталогах, включая шесть с суффиксом ``_latest``. Найти
нужный документ можно было только `grep`'ом, а «_latest» через месяц означал
уже не последнюю версию, а забытую.

Проверки:

* индекс покрывает **все** файлы `docs/**/*.md` и не содержит ссылок на
  несуществующие;
* сгенерированный `docs/README.md` совпадает с текущим содержимым каталога;
* имена без `_latest`;
* корневые документы (архитектура, безопасность, участие) на месте и
  достижимы из индекса.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.render_docs_index import OUTPUT, check, collect  # noqa: E402

DOCS = ROOT / "docs"


def test_index_is_up_to_date() -> None:
    """Тот же гейт, что в verify_repo, но с внятным сообщением."""
    problems = check(collect())
    assert not problems, "\n".join(problems)


def test_every_document_is_indexed() -> None:
    entries = {entry.path for entry in collect()}
    on_disk = {
        path.relative_to(ROOT).as_posix()
        for path in DOCS.rglob("*.md")
        if path != OUTPUT
    }
    assert entries == on_disk


def test_index_links_point_at_existing_files() -> None:
    import re

    text = OUTPUT.read_text(encoding="utf-8")
    links = re.findall(r"\]\(([^)#]+\.md)\)", text)
    assert links, "в индексе нет ни одной ссылки"
    missing = [link for link in links if not (DOCS / link).is_file()]
    assert not missing, f"индекс ссылается на отсутствующие файлы: {missing}"


def test_no_document_is_called_latest() -> None:
    offenders = sorted(
        path.relative_to(ROOT).as_posix()
        for path in DOCS.rglob("*.md")
        if path.name.endswith("_latest.md")
    )
    assert not offenders, f"суффикс _latest запрещён: {offenders}"


def test_root_documents_exist_and_are_reachable() -> None:
    for name in ("ARCHITECTURE.md", "SECURITY.md", "CONTRIBUTING.md", "README.md"):
        assert (ROOT / name).is_file(), f"нет корневого документа {name}"

    index = OUTPUT.read_text(encoding="utf-8")
    for name in ("ARCHITECTURE.md", "SECURITY.md", "CONTRIBUTING.md"):
        assert f"../{name}" in index, f"{name} не упомянут в docs/README.md"

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in ("ARCHITECTURE.md", "SECURITY.md", "CONTRIBUTING.md", "docs/README.md"):
        assert f"({name})" in readme, f"{name} не достижим из README.md"


def test_check_reports_a_stale_suffix(tmp_path: Path) -> None:
    """Негативный контроль: правило про _latest действительно срабатывает."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "report_latest.md").write_text("# Отчёт\n\nТекст.\n", encoding="utf-8")
    problems = check(collect(docs), output=docs / "README.md")
    assert any("_latest" in problem for problem in problems)


def test_check_reports_a_stale_index(tmp_path: Path) -> None:
    """Негативный контроль: забытый рендер после добавления файла виден."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# Первый\n\nПервый документ с описанием.\n", encoding="utf-8")
    index = docs / "README.md"
    index.write_text("устаревшее содержимое\n", encoding="utf-8")
    problems = check(collect(docs), output=index)
    assert any("устарел" in problem for problem in problems)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("- Result: PASS", ""),
        ("| a | b |", ""),
        ("> цитата", ""),
        ("Обычное предложение без разметки, достаточно длинное для индекса.", "Обычное"),
    ],
)
def test_summary_skips_status_lines(line: str, expected: str) -> None:
    """Отчёты начинаются с «- Result: PASS»; такой обзор бесполезен."""
    from tools.render_docs_index import _title_and_summary

    _, summary = _title_and_summary(f"# Заголовок\n\n{line}\n", fallback="x")
    assert summary.startswith(expected) if expected else not summary
