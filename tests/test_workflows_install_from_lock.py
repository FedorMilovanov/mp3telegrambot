#!/usr/bin/env python3
"""Ни один workflow не должен собирать свой набор версий.

Правило проекта: единственный владелец версий — ``requirements-lock.txt``.
На практике из 13 обязательных workflow'ов четыре ставили пакеты сами
(``pip install --upgrade pytest numpy soundfile``, ``pip install pytest ruff``),
а ``windows-bootstrap-ci.yml`` держал ещё и ``packaging==26.2``. Последствия
не видны в PR: каждый прогон зелёный, просто проверяет другой набор версий,
чем production.

Allowed формы установки:
* ``-r requirements-lock.txt`` — весь lock;
* ``python tools/install_from_lock.py <names>`` — подмножество lock'а;
* точный пин инструмента, которого в рантайм-lock'е нет (``pip-audit==2.10.1``);
* ``--upgrade pip`` — обновление самого pip;
* установка **собранного артефакта** ``--no-deps --no-index dist/*.whl`` — это не
  версия из индекса, а проверка упаковки (job ``Package build``).

Последняя форма разрешена узко: одновременно требуются ``--no-deps``,
``--no-index`` и путь ``.whl``. Иначе исключение превратилось бы в дыру, через
которую снова проходят «pip install чего угодно».
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tools.install_from_lock import canonical, locked_versions

WORKFLOWS = Path(".github/workflows")
PIP_INSTALL = re.compile(r"pip install\b")


def _workflow_files() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yml"))


def _install_commands(text: str) -> list[str]:
    """Строки и run-блоки, где встречается `pip install` или наш установщик."""
    commands: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if PIP_INSTALL.search(stripped) or "tools/install_from_lock.py" in stripped:
            commands.append(stripped)
    return commands


#: Установка локально собранного wheel: одновременно offline, без зависимостей
#: и только из ``dist/``. Проверяется отдельно в test_local_artifact_exception_is_narrow.
LOCAL_ARTIFACT = re.compile(r"pip install\b.*--no-deps\b.*--no-index\b.*\S+\.whl\b")


def _is_allowed(line: str) -> bool:
    if "--upgrade pip" in line and "pip install" in line:
        return True
    if "-r requirements-lock.txt" in line:
        return True
    if "tools/install_from_lock.py" in line:
        return True
    if LOCAL_ARTIFACT.search(line):
        return True
    # Exact pin of a tool that is not part of the runtime closure.
    if re.search(r'pip install [^=]*"?[A-Za-z0-9_.\[\]-]+==[0-9]', line):
        return True
    return False


def test_no_workflow_pins_packages_by_hand() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _workflow_files():
        bad = [line for line in _install_commands(path.read_text(encoding="utf-8")) if not _is_allowed(line)]
        if bad:
            offenders[path.name] = bad
    assert not offenders, (
        "эти workflow'ы ставят пакеты в обход lock'а: "
        f"{offenders}. Используйте `python tools/install_from_lock.py <names>` "
        "или `-r requirements-lock.txt`."
    )


def test_every_name_installed_from_the_lock_exists_in_it() -> None:
    locked = locked_versions()
    missing: dict[str, list[str]] = {}
    for path in _workflow_files():
        text = path.read_text(encoding="utf-8")
        for chunk in text.split("tools/install_from_lock.py")[1:]:
            # Names continue on the following lines of the folded `>-` block.
            block = chunk.split("\n\n", 1)[0].split("      -", 1)[0]
            names = [
                token.strip('"')
                for token in block.split()
                if re.fullmatch(r"[A-Za-z0-9_.\[\]-]+", token)
                and not token.startswith(("-", "tools/"))
                and token not in {"python", "run:", "|", ">-"}
            ]
            unknown = [name for name in names if canonical(name) not in locked]
            if unknown:
                missing[path.name] = unknown
    assert not missing, f"workflow'ы ставят пакеты, которых нет в lock: {missing}"


@pytest.mark.parametrize("workflow", [p.name for p in _workflow_files()])
def test_workflow_is_valid_yaml_with_jobs(workflow: str) -> None:
    """Дешёвая страховка: правка install-строк не должна ломать YAML."""
    import yaml

    data = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert data.get("jobs"), f"{workflow}: нет jobs"


def test_local_artifact_exception_is_narrow() -> None:
    """Исключение для wheel не должно пропускать установку из индекса."""
    assert _is_allowed("python -m pip install --no-deps --no-index dist/mp3bot-4.0.0-py3-none-any.whl")
    assert not _is_allowed("python -m pip install --no-deps requests")
    assert not _is_allowed("python -m pip install --no-index requests")
    assert not _is_allowed("python -m pip install --no-deps --no-index requests")
