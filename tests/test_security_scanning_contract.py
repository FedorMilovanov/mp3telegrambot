#!/usr/bin/env python3
"""Контракт сканирования секретов, SAST и локальных хуков.

Аудит 2026-10-23 зафиксировал единственный непокрытый класс риска: ни один
workflow не искал секреты, и не было статического анализа безопасности — при
174 переменных окружения и токенах пяти внешних сервисов.

Эти тесты сторожат не «есть ли файл», а условия, без которых проверка
бесполезна и при этом выглядит зелёной:

* gitleaks без ``fetch-depth: 0`` сканирует один коммит и не видит секрет,
  добавленный и удалённый внутри ветки;
* gitleaks без ``GITHUB_TOKEN`` в pull_request из форка падает на rate limit;
* CodeQL без ``security-events: write`` не может загрузить результаты и
  «проходит», ничего не проверив;
* pre-commit, который ставит свой ruff, — второй владелец версии рядом с
  ``requirements-lock.txt``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github/workflows"
SECRET_SCAN = WORKFLOWS / "secret-scan.yml"
CODEQL = WORKFLOWS / "codeql.yml"
PRE_COMMIT = ROOT / ".pre-commit-config.yaml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    # PyYAML читает ключ `on:` как布尔 True (YAML 1.1), поэтому обращаемся к нему
    # по обоим вариантам — иначе тест «проходит» на пустом словаре.
    return workflow.get("on") or workflow.get(True) or {}


def test_secret_scan_runs_on_pr_and_full_history() -> None:
    workflow = _load(SECRET_SCAN)
    triggers = _triggers(workflow)
    assert "pull_request" in triggers, "секрет не будет пойман до мержа"
    assert "push" in triggers

    job = workflow["jobs"]["gitleaks"]
    text = SECRET_SCAN.read_text(encoding="utf-8")
    assert "fetch-depth: 0" in text, "без полной истории сканируется только последний коммит"
    assert "gitleaks/gitleaks-action" in text
    assert "GITHUB_TOKEN" in text, "без токена сканирование PR падает на rate limit"
    assert job.get("timeout-minutes"), "job без таймаута может висеть час"


def test_secret_scan_has_read_only_permissions() -> None:
    workflow = _load(SECRET_SCAN)
    assert workflow["permissions"] == {"contents": "read"}


def test_codeql_covers_python_and_the_js_helper() -> None:
    workflow = _load(CODEQL)
    languages = workflow["jobs"]["analyze"]["strategy"]["matrix"]["language"]
    assert {"python", "javascript-typescript"} <= set(languages)
    triggers = _triggers(workflow)
    assert "pull_request" in triggers
    assert "schedule" in triggers, "без weekly-прогона новые правила CodeQL не применяются"
    permissions = workflow["jobs"]["analyze"]["permissions"]
    assert permissions["security-events"] == "write", (
        "без security-events: write результаты не загружаются и job «проходит» вхолостую"
    )
    assert permissions["contents"] == "read"


def test_pre_commit_does_not_own_a_second_ruff_version() -> None:
    """ruff ставится из lock'а; ruff-pre-commit дал бы вторую версию."""
    config = _load(PRE_COMMIT)
    remote = [repo["repo"] for repo in config["repos"]]
    assert not any("ruff-pre-commit" in url for url in remote), (
        "ruff должен вызываться из окружения, установленного по requirements-lock.txt"
    )
    # gitleaks как хук-репозиторий собирается через golang: ~100 МБ тулчейна на
    # каждой машине ради проверки, которую делает CI. Секреты ловит workflow.
    assert not any("gitleaks" in url for url in remote), (
        "gitleaks подключён как хук-репозиторий (language: golang) — используйте "
        "gitleaks protect --staged при наличии бинаря"
    )
    local = [hook for repo in config["repos"] if repo["repo"] == "local" for hook in repo["hooks"]]
    assert any(hook["entry"].startswith("ruff check") for hook in local)
    assert any(hook.get("stages") == ["pre-push"] for hook in local), (
        "verify_repo.py должен запускаться перед пушем, иначе красный PR обнаруживается в CI"
    )


@pytest.mark.parametrize("path", [PRE_COMMIT])
def test_pre_commit_pins_every_remote_repo(path: Path) -> None:
    """`rev: main` означает «проверять тем, что вышло сегодня»."""
    config = _load(path)
    unpinned = {
        repo["repo"]: repo.get("rev")
        for repo in config["repos"]
        if repo["repo"] != "local"
        and not re.fullmatch(r"v?\d+\.\d+(\.\d+)?", str(repo.get("rev", "")))
    }
    assert not unpinned, f"незапиненные репозитории pre-commit: {unpinned}"


def test_gitleaks_allowlist_stays_narrow() -> None:
    """Allowlist не должен разрешать каталоги целиком: секрет чаще всего
    оказывается в тесте при отладке."""
    import tomllib

    with (ROOT / ".gitleaks.toml").open("rb") as handle:
        config = tomllib.load(handle)
    allowlist = config["allowlist"]
    assert allowlist.get("paths", []) == [], f"allowlist разрешает пути: {allowlist.get('paths')}"
    assert allowlist.get("regexes"), "allowlist должен состоять из конкретных заглушек"
    assert config["extend"]["useDefault"] is True, (
        "без useDefault = true отключаются все штатные правила gitleaks"
    )
