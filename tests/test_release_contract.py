#!/usr/bin/env python3
"""Релизы: версия, CHANGELOG и артефакты не должны расходиться.

До этого PR у проекта не было ни одного тега и ни одного релиза: «какая версия
у пользователя» выяснялось по дате `git log`, а собранный артефакт существовал
только на машине автора. Контракт здесь состоит из четырёх проверяемых частей:

* версия объявлена один раз (`core/version.py`) и совпадает с разделом
  `## [X.Y.Z]` в `CHANGELOG.md`;
* changelog ведётся по Keep a Changelog (заголовок, `Unreleased`, даты в ISO);
* релизный workflow запускается по тегу `v*`, проверяет совпадение тега и
  версии до сборки, публикует wheel, sdist и контрольные суммы;
* документ установки существует и описывает автозапуск и откат.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
RELEASE_WORKFLOW = ROOT / ".github/workflows/release.yml"
DEPLOY_DOC = ROOT / "docs/ops/deploy_windows.md"
INSTALL_SCRIPT = ROOT / "scripts/install-service.ps1"


def _version() -> str:
    from core.version import __version__

    return __version__


def test_changelog_follows_keep_a_changelog() -> None:
    text = CHANGELOG.read_text(encoding="utf-8")
    assert text.startswith("# Изменения"), "changelog должен начинаться с заголовка"
    assert "## [Unreleased]" in text, "нет раздела Unreleased — новые изменения некуда писать"

    headings = re.findall(r"^## \[([^\]]+)\](.*)$", text, flags=re.MULTILINE)
    assert headings, "в changelog нет ни одного раздела версии"
    allowed = {"Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"}
    for version, rest in headings:
        if version == "Unreleased":
            assert not rest.strip(), "у Unreleased не должно быть даты"
            continue
        assert re.fullmatch(r" - \d{4}-\d{2}-\d{2}", rest), (
            f"раздел {version}: дата должна быть в формате YYYY-MM-DD, получено {rest!r}"
        )

    for section in re.findall(r"^### (.+)$", text, flags=re.MULTILINE):
        assert section in allowed, f"нестандартный раздел changelog: {section}"


def test_released_version_has_a_dated_section() -> None:
    """Текущая версия обязана быть описана — иначе релиз выходит «молча»."""
    version = _version()
    text = CHANGELOG.read_text(encoding="utf-8")
    assert re.search(rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", text, flags=re.MULTILINE), (
        f"в CHANGELOG.md нет раздела «## [{version}] - YYYY-MM-DD»"
    )


def test_version_is_unique_and_ordered() -> None:
    """Версии в changelog убывают: новый раздел добавляется сверху."""
    text = CHANGELOG.read_text(encoding="utf-8")
    versions = [
        tuple(int(part) for part in match.split("."))
        for match in re.findall(r"^## \[(\d+\.\d+\.\d+)\]", text, flags=re.MULTILINE)
    ]
    assert versions == sorted(versions, reverse=True), f"порядок версий нарушен: {versions}"


def test_release_workflow_triggers_on_semver_tags() -> None:
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on") or workflow.get(True) or {}
    tags = triggers["push"]["tags"]
    assert tags == ["v[0-9]+.[0-9]+.[0-9]+"], tags
    assert "workflow_dispatch" in triggers, "пересборка релиза должна быть возможна вручную"


def test_release_workflow_verifies_before_publishing() -> None:
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    jobs = workflow["jobs"]
    assert jobs["build"]["needs"] == "verify", "сборка не должна идти без проверки тега"
    verify = text.split("build:")[0]
    assert "=== " not in verify
    assert "__version__" in verify, "тег не сверяется с core.version"
    assert "CHANGELOG.md" in verify, "наличие раздела changelog не проверяется"
    assert "python -m pytest" in verify, "полный прогон не запускается до сборки"


def test_release_publishes_artifacts_with_checksums() -> None:
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    permissions = workflow["jobs"]["build"]["permissions"]
    assert permissions == {"contents": "write"}, "публикация релиза требует contents: write и ничего больше"
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "python -m build --outdir dist" in text
    assert "SHA256SUMS" in text
    assert "gh release create" in text, "публикация через gh CLI: сторонний action — лишняя зависимость в supply chain"
    assert "--no-deps --no-index dist/*.whl" in text, "wheel не проверяется установкой без зависимостей"
    # Правило «workflow не ставит пакеты в обход lock'а» проверяется отдельно и
    # для всех workflow сразу (tests/test_workflows_install_from_lock.py) — здесь
    # его дублировать не нужно, иначе появляется вторая, менее точная реализация.


def test_deployment_document_covers_install_update_and_rollback() -> None:
    text = DEPLOY_DOC.read_text(encoding="utf-8")
    for required in (
        "requirements-lock.txt",
        "install-service.ps1",
        "repair-local-bot-api.ps1",
        "-Action Stop",
        "Откат",
        "health",
    ):
        assert required in text, f"в документе установки нет {required!r}"


def test_install_script_is_safe_by_construction() -> None:
    """Скрипт автозапуска: без второго экземпляра, с перезапуском и явной остановкой."""
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    assert "MultipleInstances IgnoreNew" in text, "два экземпляра бота — двойные ответы и гонки за SQLite"
    assert "RestartCount" in text, "падение процесса иначе означает простой до перезагрузки"
    assert "LogonType S4U" in text, "SYSTEM ломает %TEMP%, .env и CUDA: задача должна идти от пользователя"
    assert "ValidateSet('Install', 'Start', 'Stop', 'Uninstall', 'Status')" in text
    assert "ExecutionTimeLimit ([TimeSpan]::Zero)" in text, "бот живёт постоянно: лимит времени задачи его бы убил"


@pytest.mark.parametrize("flag", ["--version"])
def test_packaging_version_matches_changelog(flag: str) -> None:
    """`core/version.py` — единственный источник: pyproject читает его же."""
    with (ROOT / "pyproject.toml").open("rb") as handle:
        dynamic = tomllib.load(handle)["tool"]["setuptools"]["dynamic"]
    assert dynamic["version"] == {"attr": "core.version.__version__"}
    assert _version() == _version().strip()
