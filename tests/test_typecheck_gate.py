#!/usr/bin/env python3
"""Контракт гейта типизации: что проверяется и насколько строго.

Смысл этих тестов — не дать гейту тихо ослабнуть. Настройки живут в
``pyproject.toml`` и применяются командой ``tools/check_types.py``; если
кто-то снимет ``strict`` или вернёт ``follow_imports = "normal"``, типизация
формально останется в CI, но перестанет что-либо гарантировать. Здесь это
падение видно как обычный красный тест, а не как «mypy стал добрым».

Отдельно проверяется, что объявленный срез действительно чист: ``pytest``
запускается локально до push, и ошибка типа должна ловиться там же, а не
только в CI.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from tools.check_types import TYPED_SLICES, build_command, check, error_count, missing_mypy

ROOT = Path(__file__).resolve().parents[1]


def _mypy_config() -> dict[str, object]:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["tool"]["mypy"]


def test_strict_mode_is_enabled_in_config_not_only_in_ci() -> None:
    config = _mypy_config()
    assert config.get("strict") is True, "strict=true обязателен: иначе проверка типов декоративна"


def test_config_keeps_the_flags_the_gate_depends_on() -> None:
    config = _mypy_config()
    # explicit_package_bases — иначе корневой __init__.py даёт
    # «Duplicate module named 'database'» и mypy вообще не стартует.
    assert config.get("explicit_package_bases") is True
    assert config.get("ignore_missing_imports") is True
    # silent — граница среза: ошибки вне среза не шумят, ошибки внутри видны.
    assert config.get("follow_imports") == "silent"
    assert "ignore-without-code" in config.get("enable_error_code", [])


def test_config_targets_the_project_floor() -> None:
    config = _mypy_config()
    assert config.get("python_version") == "3.12", "цель mypy обязана совпадать с floor проекта"


def test_typed_slices_are_real_packages() -> None:
    assert TYPED_SLICES, "срез не может быть пустым — иначе гейт ничего не проверяет"
    for slice_path in TYPED_SLICES:
        path = ROOT / slice_path
        assert path.is_dir(), f"срез {slice_path} не существует"
        assert (path / "__init__.py").exists(), f"{slice_path} должен быть пакетом"


def test_command_is_just_mypy_over_the_slice() -> None:
    """Флаги не дублируются в командной строке: единственный источник — pyproject."""
    command = build_command(("core",))
    assert command[1:3] == ["-m", "mypy"]
    assert command[3] == "core"
    assert not any(part.startswith("--") for part in command), (
        f"жёсткость должна задаваться в [tool.mypy], а не аргументами: иначе настройки расходятся. Команда: {command}"
    )


def test_missing_mypy_is_reported_as_environment_problem() -> None:
    """Отсутствие mypy — это сломанное окружение, а не ошибки в коде."""

    class _Result:
        stdout = "/usr/bin/python: No module named mypy\n"
        stderr = ""

    assert missing_mypy(_Result()) is True  # type: ignore[arg-type]  # duck-typed заглушка


def test_error_count_counts_only_error_lines() -> None:
    output = (
        "core/a.py:3: error: Incompatible return value  [return-value]\n"
        "core/a.py:4: note: something else\n"
        "Found 1 error in 1 file\n"
    )
    assert error_count(output) == 1


def test_declared_slice_is_type_clean() -> None:
    """Реальный прогон mypy по срезу — тот же, что делает CI."""
    result = check(TYPED_SLICES)
    output = (result.stdout + result.stderr).strip()
    if missing_mypy(result):  # pragma: no cover - сломанное окружение
        pytest.fail(
            "mypy не установлен: гейт типизации не может быть проверен. "
            "Установите dev-зависимости: pip install -r requirements-dev.txt"
        )
    assert result.returncode == 0, (
        f"срез {TYPED_SLICES} не проходит mypy --strict ({error_count(output)} ошибок):\n{output}"
    )
