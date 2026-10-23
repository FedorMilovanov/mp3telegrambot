#!/usr/bin/env python3
"""Каждый пин в lock обязан работать на минимальном Python проекта.

История: floor был 3.11, а часть аудио-стека (numpy 2.5+, av 19+) требует
``Requires-Python >=3.12``. Пока floor не подняли, в ``dependabot.yml`` лежали
два ignore — иначе Dependabot открывал PR, который не мог быть зелёным.
2026-10-24 floor поднят до 3.12, ignore сняты, а проверка осталась: она ловит
следующий такой случай (пакет, требующий 3.13) до того, как он попадёт в lock.

Проверка офлайновая: ``Requires-Python`` берётся из метаданных уже
установленных дистрибутивов (CI ставит lock перед тестами), а floor — из
матрицы ``ci.yml``, чтобы версия Python не жила третьим местом.
"""
from __future__ import annotations

import re
from importlib import metadata
from pathlib import Path

import pytest

from tools.install_from_lock import canonical, locked_versions

CI = Path(".github/workflows/ci.yml")
LOCK = Path("requirements-lock.txt")


def _floor_from_ci() -> tuple[int, int]:
    """Минимальная версия из матрицы CI (а не литерал в тесте)."""
    text = CI.read_text(encoding="utf-8")
    match = re.search(r"python-version:\s*\[([^\]]+)\]", text)
    assert match, "в ci.yml не найдена матрица python-version"
    versions = [item.strip().strip('"\'') for item in match.group(1).split(",")]
    floors = [tuple(int(part) for part in version.split(".")[:2]) for version in versions]
    return min(floors)


def _requires_python(distribution: str) -> tuple[bool, str | None]:
    """(установлен ли, Requires-Python).

    Пустой ``Requires-Python`` — это «любой Python», а не «пакет не найден»:
    так делают brotli и flatbuffers. Смешивать эти случаи нельзя, иначе
    проверка либо молча пропускает пакеты, либо падает на здоровом дереве.
    """
    try:
        return True, metadata.metadata(distribution).get("Requires-Python")
    except metadata.PackageNotFoundError:
        return False, None


def test_floor_matches_the_declared_project_floor() -> None:
    """Единый источник floor'а: матрица CI, pyproject и проверка в bot_new.py."""
    import tomllib

    source = Path("bot_new.py").read_text(encoding="utf-8")
    assert "sys.version_info[:2]" in source
    floor = _floor_from_ci()
    assert floor == (3, 12), f"floor в CI сместился на {floor} — обновите границы пакетов"

    with Path("pyproject.toml").open("rb") as handle:
        declared = tomllib.load(handle)["project"]["requires-python"]
    assert declared == f">={floor[0]}.{floor[1]}", (
        f"pyproject объявляет {declared}, а матрица CI — {floor}"
    )

    launcher = Path("Start Bot.bat").read_text(encoding="utf-8")
    assert f"({floor[0]}, {floor[1]}) <= sys.version_info[:2]" in launcher, (
        "Windows-лаунчер обязан проверять тот же floor"
    )
    assert f"({floor[0]}, {floor[1]}) <= sys.version_info[:2]" in source


def test_every_locked_pin_supports_the_minimum_python() -> None:
    floor = _floor_from_ci()
    from packaging.specifiers import SpecifierSet

    locked = locked_versions(LOCK)
    violations: dict[str, str] = {}
    unverified: list[str] = []

    for distribution in locked:
        present, requires = _requires_python(distribution)
        if not present:
            unverified.append(distribution)
            continue
        if not requires:
            continue  # пакет не ограничивает версию Python
        if not SpecifierSet(requires).contains(f"{floor[0]}.{floor[1]}.0"):
            violations[distribution] = requires

    assert not violations, (
        "эти пины lock не устанавливаются на минимальном Python "
        f"{floor[0]}.{floor[1]}: {violations}"
    )
    # Не установленный локально пакет — не повод молча пропустить проверку.
    # В CI стоит весь lock, поэтому список должен быть пустым.
    assert not unverified, f"метаданные не найдены для: {sorted(unverified)}"


def test_numpy_major_is_allowed_now_that_the_floor_is_312() -> None:
    """numpy 2.5+ требует 3.12: с floor'ом 3.12 верхняя граница не нужна.

    Пока floor был 3.11, объявление ``numpy<2.5`` было единственным способом
    не получить несовместимость. Теперь граница только мешает Dependabot'у
    предложить мажор, поэтому она снята и это зафиксировано тестом.
    """
    from packaging.requirements import Requirement

    requirements = [
        Requirement(line.split("#", 1)[0].strip())
        for line in Path("requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip().lower().startswith("numpy")
    ]
    assert requirements, "numpy больше не объявлен напрямую"
    assert requirements[0].specifier.contains("2.5.0"), (
        "numpy 2.5 разрешён: floor 3.12 это позволяет, обновите границу"
    )


def test_dependabot_no_longer_ignores_the_312_majors() -> None:
    """Ignore был временным костылём под floor 3.11 и снят вместе с ним.

    Если записи вернуть, мажоры numpy/av перестанут предлагаться вообще —
    и «защита от красного PR» превратится в незаметное отставание.
    """
    import yaml

    config = yaml.safe_load(Path(".github/dependabot.yml").read_text(encoding="utf-8"))
    pip_update = next(
        item for item in config["updates"] if item["package-ecosystem"] == "pip"
    )
    ignored = {entry["dependency-name"] for entry in pip_update.get("ignore", [])}
    assert not {"numpy", "av"} & ignored, (
        "numpy/av больше не требуют ignore: floor 3.12 снимает причину"
    )


@pytest.mark.parametrize("name", ["numpy", "av"])
def test_the_312_majors_are_still_in_the_lock(name: str) -> None:
    """Пакет, ради которого поднимали floor, обязан остаться в lock'е."""
    assert canonical(name) in locked_versions(LOCK)
