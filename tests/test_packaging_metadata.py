#!/usr/bin/env python3
"""Упаковка проекта: единый источник версии, лицензия и рабочая точка входа.

Зачем отдельный файл тестов: до этого PR ``pyproject.toml`` содержал только
конфиг pytest/ruff, то есть проект *запускался из папки*, но не устанавливался.
Сборка wheel вскрыла две ошибки, которые не видит ни один существующий гейт:

1. без ``py-modules`` в wheel не попадали ``bot.py``, ``bot_new.py`` и
   ``main.py`` — консольная команда падала бы с ImportError уже после установки;
2. ``mp3bot = "bot_new:main"`` ссылался на несуществующую функцию: ``bot_new``
   исполнял bootstrap на импорте и не имел точки входа.

Проверки офлайновые: файлы и метаданные читаются напрямую, сборка wheel
выполняется отдельным job'ом CI (``Package build``), потому что требует сети.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
LICENSE = ROOT / "LICENSE"


def _pyproject() -> dict:
    import tomllib

    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def _project() -> dict:
    return _pyproject()["project"]


def test_license_file_is_the_declared_licence() -> None:
    """Файл лицензии обязан существовать и совпадать с объявлением."""
    assert LICENSE.is_file(), "нет файла LICENSE — проект юридически 'all rights reserved'"
    text = LICENSE.read_text(encoding="utf-8")
    assert text.startswith("MIT License"), "LICENSE не является текстом MIT"
    assert "Copyright (c) 2026" in text, "в LICENSE нет строки копирайта"
    project = _project()
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]


def test_version_has_exactly_one_source() -> None:
    """Версия живёт в core/version.py; pyproject читает её через attr."""
    from core.version import __version__

    dynamic = _pyproject()["tool"]["setuptools"]["dynamic"]
    assert dynamic["version"] == {"attr": "core.version.__version__"}
    assert "version" in _project()["dynamic"]
    assert "version" not in _project(), "статическая версия в [project] разошлась бы с core.version"
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), f"версия не SemVer: {__version__}"


def test_console_script_target_is_callable() -> None:
    """`mp3bot = bot_new:main` должен указывать на реальную функцию."""
    scripts = _project()["scripts"]
    assert scripts == {"mp3bot": "bot_new:main"}
    module_name, _, attribute = scripts["mp3bot"].partition(":")
    source = (ROOT / f"{module_name}.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    assert attribute in functions, (
        f"{module_name}.{attribute} не найдена — консольная команда упадёт с ImportError"
    )


def test_launchers_are_inside_the_wheel_package_set() -> None:
    """Без py-modules wheel не содержит точки входа (проверено сборкой)."""
    modules = _pyproject()["tool"]["setuptools"]["py-modules"]
    assert {"bot", "bot_new", "main"} <= set(modules)


def test_requires_python_matches_the_ci_matrix_floor() -> None:
    """Floor объявлен дважды (pyproject и ci.yml) — обязан совпадать."""
    declared = _project()["requires-python"]
    assert declared == ">=3.12", (
        "поднимаете floor — обновите матрицу ci.yml, bot_new.py, Start Bot.bat, "
        "pyproject classifiers/target-version и tests/test_lock_python_floor.py одним PR"
    )
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    matrix = re.search(r"python-version:\s*\[([^\]]+)\]", ci)
    assert matrix, "в ci.yml не найдена матрица python-version"
    versions = sorted(item.strip().strip("\"'") for item in matrix.group(1).split(","))
    assert declared.removeprefix(">=") == versions[0]


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_flag_answers_without_a_runtime(flag: str) -> None:
    """Версия — единственное, что обязано работать без зависимостей и сети."""
    from core.version import __version__

    proc = subprocess.run(
        [sys.executable, "bot_new.py", flag],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip() == f"mp3bot {__version__}"


def test_readme_documents_the_licence() -> None:
    """Ссылка в README — то, что видит человек, открывший репозиторий."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Лицензия" in readme, "в README нет секции «Лицензия»"
    assert "LICENSE" in readme and "MIT" in readme
