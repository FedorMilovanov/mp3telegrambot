#!/usr/bin/env python3
"""Всё, что импортируется напрямую, должно быть объявлено напрямую.

Аудит нашёл три пакета, которые код импортирует сам, но ``requirements.txt``
о них молчал, потому что они «приезжали» транзитивно:

* ``httpx`` — жёсткая зависимость PTB и прямой импорт в
  ``services/local_botapi_required.py``;
* ``socksio`` — опциональный импорт в ``main.py``; без него SOCKS-прокси
  деградирует в HTTP-fallback;
* ``mutagen`` — импорт в ``services/mp3_chapters.py``, приходил только через
  ``yt-dlp[default]`` (чужой extra).

Транзитивная зависимость — это не контракт: апстрим может убрать extra, и
падение случится в проде, а не в CI. Тест сравнивает два списка:
``REQUIRED_DIRECT`` (то, что проект обязан объявлять) и содержимое
``requirements.txt`` с учётом extras, плюс проверяет, что extras реально
разрешились в lock (иначе объявление ничего не значит).
"""

from __future__ import annotations

from pathlib import Path

from packaging.requirements import Requirement

from tools.install_from_lock import canonical, locked_versions

#: Модуль импорта -> дистрибутив, который обязан быть в requirements.txt.
IMPORTED_DIRECTLY = {
    "httpx": "httpx",
    "mutagen": "mutagen",
    "requests": "requests",
    "bs4": "beautifulsoup4",
}

#: Модуль -> (дистрибутив, extra), которым он объявляется осознанно.
#: socksio не ставят отдельной строкой: он нужен только httpx, и extra
#: фиксирует эту связь (у отдельной строки нет причины существовать).
EXTRA_PROVIDED = {"socksio": ("httpx", "socks")}

#: Требования, которые не import-ятся (инструменты), но тоже объявлены.
TOOLING = {"pytest", "ruff", "pytest-asyncio"}


def _declared() -> dict[str, Requirement]:
    declared: dict[str, Requirement] = {}
    for line in Path("requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        requirement = Requirement(line)
        declared[canonical(requirement.name)] = requirement
    return declared


def _extras(requirement: Requirement) -> set[str]:
    return {extra.lower() for extra in requirement.extras}


def test_every_direct_import_is_a_direct_requirement() -> None:
    declared = _declared()
    missing = {module: dist for module, dist in IMPORTED_DIRECTLY.items() if canonical(dist) not in declared}
    for module, (dist, extra) in EXTRA_PROVIDED.items():
        requirement = declared.get(canonical(dist))
        if requirement is None or extra not in _extras(requirement):
            missing[module] = f"{dist}[{extra}]"
    assert not missing, f"эти пакеты импортируются напрямую, но не объявлены в requirements.txt: {missing}"


def test_socks_support_is_declared_as_an_extra() -> None:
    """``httpx[socks]`` — единственный способ объявить socksio осмысленно."""
    requirement = _declared()[canonical("httpx")]
    assert "socks" in _extras(requirement), (
        "httpx объявлен без extras=socks: socksio не установится, и SOCKS-прокси тихо уйдёт в HTTP-fallback (main.py)"
    )


def test_extras_actually_resolved_into_the_lock() -> None:
    """Extra без разрешённой зависимости в lock — объявление на бумаге."""
    locked = locked_versions()
    assert canonical("socksio") in locked, "socksio не попал в lock"
    assert canonical("mutagen") in locked, "mutagen не попал в lock"
    assert canonical("httpx") in locked


def test_locked_pins_satisfy_their_declarations() -> None:
    locked = locked_versions()
    declared = _declared()
    checked = 0
    for name, requirement in declared.items():
        if name not in locked:
            continue
        checked += 1
        assert requirement.specifier.contains(locked[name], prereleases=True), (
            f"{name}: lock {locked[name]} не удовлетворяет {requirement.specifier}"
        )
    assert checked >= len(declared) - 1, "часть объявлений не покрыта lock'ом"


def test_tooling_is_not_pulled_into_the_runtime_closure_by_accident() -> None:
    """pytest/ruff живут в requirements-dev.txt, а не смешиваются с рантаймом.

    Исключение — ``pytest`` в ``requirements.txt`` исторически: он нужен
    ``tools/verify_repo.py`` на целевой машине. Проверяем, что ``ruff``
    объявлен ровно в одном файле, чтобы версия снова не разъехалась.
    """
    runtime = _declared()
    dev_text = Path("requirements-dev.txt").read_text(encoding="utf-8")
    dev_names = {
        canonical(Requirement(line.split("#", 1)[0].strip()).name)
        for line in dev_text.splitlines()
        if line.split("#", 1)[0].strip() and not line.split("#", 1)[0].strip().startswith("-")
    }
    for name in TOOLING:
        assert (name in runtime) != (name in dev_names), (
            f"{name} объявлен и в requirements.txt, и в requirements-dev.txt"
        )
