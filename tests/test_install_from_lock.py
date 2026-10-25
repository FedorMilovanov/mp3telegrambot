#!/usr/bin/env python3
"""Контракт ``tools/install_from_lock.py`` и отказ от вторых владельцев версий.

Историческая проблема: ``windows-bootstrap-ci.yml`` ставил
``packaging==26.2``/``pytest==8.4.2``/``yt-dlp==2026.7.4`` руками рядом с
lock'ом, где уже были ``pytest 9.1.1`` и ``yt-dlp 2026.10.21``. Ни один тест
не замечал расхождения, потому что обе стороны «выглядели правильными».

Здесь проверяется, что:
* версия берётся из lock (и только из него);
* неизвестное имя — громкая ошибка, а не «поставим что найдётся»;
* в workflow нет собственных пинов вида ``pkg==X.Y``;
* lock остаётся единственным местом, где живёт версия.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tools import install_from_lock as installer

WORKFLOW = Path(".github/workflows/windows-bootstrap-ci.yml")
LOCK = Path("requirements-lock.txt")


def test_dry_run_prints_exactly_the_locked_versions() -> None:
    lines = installer.select(["packaging", "pytest", "yt-dlp", "yt-dlp-ejs"])
    locked = installer.locked_versions()
    assert lines == [
        f"packaging=={locked['packaging']}",
        f"pytest=={locked['pytest']}",
        f"yt-dlp=={locked['yt-dlp']}",
        f"yt-dlp-ejs=={locked['yt-dlp-ejs']}",
    ]


def test_name_normalisation_is_applied() -> None:
    """``Yt_DLP`` и ``yt-dlp`` — один и тот же дистрибутив."""
    assert installer.select(["Yt_DLP"]) == installer.select(["yt-dlp"])


def test_unknown_package_fails_loudly() -> None:
    with pytest.raises(SystemExit, match="not pinned"):
        installer.select(["definitely-not-a-package"])


def test_lock_without_exact_pins_is_rejected(tmp_path: Path) -> None:
    lock = tmp_path / "lock.txt"
    lock.write_text("packaging>=26.2\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="not an exact pin"):
        installer.locked_versions(lock)


def test_windows_workflow_has_no_version_pins_of_its_own() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "python tools/install_from_lock.py" in text

    # `uses: actions/...@v6` — это не пины пакетов; ищем только pip-стиль.
    pip_pins = re.findall(r"(?m)^\s*(?:python -m pip install\s+)?([A-Za-z0-9_.\[\]-]+)==([0-9][^\s]*)", text)
    assert not pip_pins, f"workflow снова фиксирует версии сам: {pip_pins}"


def test_every_package_the_workflow_installs_exists_in_the_lock() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    # The file also lists `tools/install_from_lock.py` in its `paths:` filter,
    # so anchor on the invocation, not on the first mention of the name.
    step = text.split("python tools/install_from_lock.py", 1)[1].split("\n      -", 1)[0]
    names = [token for token in step.split() if re.fullmatch(r"[A-Za-z0-9_.-]+", token)]
    assert names, "шаг установки не перечисляет пакеты"
    locked = installer.locked_versions()
    missing = [name for name in names if installer.canonical(name) not in locked]
    assert not missing, f"этих пакетов нет в lock: {missing}"


def test_declared_floor_and_locked_pin_agree() -> None:
    """``requirements.txt`` задаёт диапазон, lock — точку внутри него.

    Тест намеренно не содержит версий: иначе любой Dependabot-бамп ломал бы
    его, и «одна правка версии» снова превращалась бы в две.
    """
    from packaging.requirements import Requirement

    direct = Path("requirements.txt").read_text(encoding="utf-8")
    locked = installer.locked_versions()
    checked = 0
    for line in direct.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "==" in line or line.startswith("-"):
            continue
        requirement = Requirement(line)
        name = installer.canonical(requirement.name)
        if name not in locked:
            continue
        checked += 1
        assert requirement.specifier.contains(locked[name], prereleases=True), (
            f"{name}: lock {locked[name]} не удовлетворяет {requirement.specifier}"
        )
    assert checked >= 15, f"проверено слишком мало объявлений: {checked}"
