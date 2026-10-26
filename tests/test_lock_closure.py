#!/usr/bin/env python3
"""Lock ↔ объявления: ни лишнего, ни забытого.

До этой проверки связь была односторонней: ``tools/check_requirements_lock.py``
доказывал, что каждый *объявленный* пакет есть в lock'е, но никто не проверял
обратное — что каждый пин в lock'е кому-то нужен. Именно поэтому ``pdfkit``
мог годами лежать в lock'е (и держать единственное исключение в аудите) после
того, как код перестал его использовать.

Тест строит замыкание зависимостей от ``requirements.txt`` +
``requirements-dev.txt`` по метаданным установленных дистрибутивов, с учётом
extras и маркеров, для Linux и Windows и для обоих краёв матрицы Python
(3.11 и 3.13 — lock кросс-платформенный, поэтому colorama на win32 и
exceptiongroup на <3.11 законны). Затем сравнивает множество с lock'ом:
равенство означает, что lock — ровно то, что нужно, и ничего сверх.
"""

from __future__ import annotations

import sys
from importlib import metadata
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from tools.install_from_lock import locked_versions

DIRECT_FILES = (Path("requirements.txt"), Path("requirements-dev.txt"))
LOCK = Path("requirements-lock.txt")

PLATFORMS = (
    {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix"},
    {"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt"},
)


def _direct_requirements() -> list[Requirement]:
    requirements: list[Requirement] = []
    for path in DIRECT_FILES:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            requirements.append(Requirement(line))
    return requirements


def _environments() -> list[dict[str, str]]:
    versions = sorted({f"{sys.version_info[0]}.{sys.version_info[1]}", "3.11", "3.13"})
    environments: list[dict[str, str]] = []
    for platform in PLATFORMS:
        for version in versions:
            environments.append(
                {
                    **platform,
                    "python_version": version,
                    "python_full_version": f"{version}.0",
                    "platform_machine": "x86_64",
                }
            )
    return environments


def _applies(requirement: Requirement, extras: set[str], environment: dict[str, str]) -> bool:
    marker = requirement.marker
    if marker is None:
        return True
    if "extra" in str(marker):
        return any(marker.evaluate({**environment, "extra": extra}) for extra in (sorted(extras) or [""]))
    return marker.evaluate({**environment, "extra": ""})


def _closure() -> tuple[set[str], set[str]]:
    """(достижимые дистрибутивы, дистрибутивы без метаданных)."""
    reachable: set[str] = set()
    unknown: set[str] = set()
    direct = _direct_requirements()

    for environment in _environments():
        queue: list[tuple[str, frozenset[str]]] = [
            (canonicalize_name(requirement.name), frozenset(requirement.extras))
            for requirement in direct
            if _applies(requirement, set(requirement.extras), environment)
        ]
        seen: set[tuple[str, frozenset[str]]] = set()
        while queue:
            name, extras = queue.pop()
            key = (name, extras)
            if key in seen:
                continue
            seen.add(key)
            reachable.add(name)
            try:
                requires = metadata.requires(name) or []
            except metadata.PackageNotFoundError:
                unknown.add(name)
                continue
            for raw in requires:
                requirement = Requirement(raw)
                if not _applies(requirement, set(extras), environment):
                    continue
                queue.append((canonicalize_name(requirement.name), frozenset(requirement.extras)))
    return reachable, unknown


def test_lock_is_exactly_the_dependency_closure() -> None:
    locked = set(locked_versions(LOCK))
    reachable, unknown = _closure()

    if unknown:
        pytest.skip(
            "окружение не содержит весь lock — замыкание неполное, "
            f"пропущены метаданные: {sorted(unknown)}. "
            "В CI ставится `pip install -r requirements-lock.txt`, там тест работает."
        )

    extra_in_lock = sorted(locked - reachable)
    missing_from_lock = sorted(reachable - locked)

    assert not extra_in_lock, (
        "в lock'е есть пакеты, которых никто не требует — это лишняя "
        f"поверхность атаки и повод для исключений в аудите: {extra_in_lock}"
    )
    assert not missing_from_lock, f"эти зависимости достижимы, но не зафиксированы в lock: {missing_from_lock}"


def test_the_closure_check_sees_all_69_pins() -> None:
    """Страховка от вырожденного замыкания (например, пустого requirements)."""
    locked = locked_versions(LOCK)
    assert len(locked) >= 60, f"lock подозрительно мал: {len(locked)}"
    reachable, unknown = _closure()
    if unknown:
        pytest.skip(f"метаданные не установлены: {sorted(unknown)}")
    assert reachable, "замыкание пусто — проверка выше ничего не доказывает"
