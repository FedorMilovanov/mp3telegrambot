#!/usr/bin/env python3
"""Каждый пин в lock обязан работать на минимальном Python проекта.

Найденная проблема: ``requirements-lock.txt`` — один набор версий на все
срабатывания CI, а матрица ``ci.yml`` начинается с 3.11. При этом часть
пакетов объявляет ``Requires-Python >=3.12`` (numpy 2.5+, av 19+). Достаточно
одного Dependabot-PR, чтобы lock стал несовместим с floor'ом — и это выяснится
не в ревью, а на 3.11-джобе, где ``pip install`` просто откажется ставить
пакет (или поставит другой, разрушая идею lock'а).

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
    """Единый источник floor'а: матрица CI и проверка в bot_new.py."""
    source = Path("bot_new.py").read_text(encoding="utf-8")
    assert "sys.version_info[:2]" in source
    floor = _floor_from_ci()
    assert floor == (3, 11), f"floor в CI сместился на {floor} — обновите границы пакетов"


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


def test_numpy_is_bounded_below_its_python_312_line() -> None:
    """numpy 2.5+ требует 3.12: объявление должно это фиксировать."""
    from packaging.requirements import Requirement

    requirements = [
        Requirement(line.split("#", 1)[0].strip())
        for line in Path("requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip().lower().startswith("numpy")
    ]
    assert requirements, "numpy больше не объявлен напрямую"
    specifier = requirements[0].specifier
    assert not specifier.contains("2.5.0"), (
        "numpy 2.5.x несовместим с floor'ом 3.11, но объявление его разрешает"
    )


def test_dependabot_does_not_propose_the_python_312_majors() -> None:
    """Плохой PR лучше не открывать, чем открыть и месяц держать красным."""
    import yaml

    config = yaml.safe_load(Path(".github/dependabot.yml").read_text(encoding="utf-8"))
    pip_update = next(
        item for item in config["updates"] if item["package-ecosystem"] == "pip"
    )
    ignored = {entry["dependency-name"] for entry in pip_update.get("ignore", [])}
    assert {"numpy", "av"} <= ignored, (
        "Dependabot снова предложит numpy/av мажоры, несовместимые с 3.11"
    )


@pytest.mark.parametrize("name", ["numpy", "av"])
def test_the_ignored_majors_really_need_python_312(name: str) -> None:
    """Причина ignore'а должна оставаться правдой, иначе его пора снимать.

    Проверяем по уже установленной версии: если бы установленный пин требовал
    3.12, тест выше уже упал бы. Здесь фиксируем, что верхняя граница
    объявлена там, где пакет объявлен напрямую (numpy), либо что мажор
    игнорируется (av объявлен транзитивно через faster-whisper).
    """
    import yaml

    config = yaml.safe_load(Path(".github/dependabot.yml").read_text(encoding="utf-8"))
    pip_update = next(
        item for item in config["updates"] if item["package-ecosystem"] == "pip"
    )
    entry = next(
        item for item in pip_update["ignore"] if item["dependency-name"] == name
    )
    assert entry["update-types"] == ["version-update:semver-major"]
    assert canonical(name) in locked_versions(LOCK)
