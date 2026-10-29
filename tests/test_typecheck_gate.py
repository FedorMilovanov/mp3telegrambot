#!/usr/bin/env python3
"""Контракт гейта типизации: что проверяется и насколько строго.

Смысл этих тестов — не дать гейту тихо ослабнуть. Настройки живут в
``pyproject.toml`` и применяются командой ``tools/check_types.py``; если
кто-то снимет ``strict`` или вернёт ``follow_imports = "normal"``, типизация
формально останется в CI, но перестанет что-либо гарантировать. Здесь это
падение видно как обычный красный тест, а не как «mypy стал добрым».

Отдельно проверяется, что полный repository-wide срез действительно чист:
``pytest`` запускается локально до push, и ошибка типа должна ловиться там
же, а не только в CI.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest

from tools.check_types import (
    TYPED_SLICES,
    Slice,
    build_command,
    check,
    error_count,
    missing_mypy,
)

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "typechecking.md"


def _mypy_config() -> dict[str, object]:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        tool = tomllib.load(fh)["tool"]
    assert isinstance(tool, dict)
    config = tool["mypy"]
    assert isinstance(config, dict)
    return {str(key): value for key, value in config.items()}


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
    error_codes = config.get("enable_error_code", [])
    assert isinstance(error_codes, list)
    assert "ignore-without-code" in error_codes


def test_config_targets_the_project_floor() -> None:
    config = _mypy_config()
    assert config.get("python_version") == "3.12", "цель mypy обязана совпадать с floor проекта"


def test_typed_slices_exist_and_are_packages() -> None:
    assert TYPED_SLICES, "срез не может быть пустым — иначе гейт ничего не проверяет"
    for slice_ in TYPED_SLICES:
        path = ROOT / slice_.path
        assert path.exists(), f"срез {slice_.path} не существует"
        if path.is_dir():
            assert (path / "__init__.py").exists(), f"{slice_.path} должен быть пакетом"


def test_excluded_files_exist_and_have_supporting_lines() -> None:
    """Исключения не «теряются»: файл существует, и причину видно в коде."""
    source = Path("tools/check_types.py").read_text(encoding="utf-8")
    for slice_ in TYPED_SLICES:
        for excluded in slice_.exclude:
            assert (ROOT / excluded).is_file(), f"исключённый файл {excluded} не найден"
            assert excluded in source, f"{excluded} должен быть объявлен в TYPED_SLICES с причиной"
            assert excluded not in {s.path for s in TYPED_SLICES}


def test_command_carries_only_slice_paths_and_anchored_excludes() -> None:
    """Флаги не дублируются в командной строке: единственный источник — pyproject."""
    command = build_command((Slice("core"), Slice("pipelines", exclude=("pipelines/main_pipeline.py",))))
    assert command[1:3] == ["-m", "mypy"]
    assert command[3:5] == ["core", "pipelines"]
    assert command[5] == r"--exclude=^pipelines/main_pipeline\.py$", (
        "исключение должно быть заякорено, иначе под него попадёт лишний файл"
    )
    assert not any(part.startswith("--") and part != command[5] for part in command), (
        f"жёсткость задаётся в [tool.mypy], а не аргументами: иначе настройки расходятся. Команда: {command}"
    )


def test_typed_slices_match_the_documentation_table() -> None:
    """Таблица в docs — не украшение: расхождение означает, что документ врёт."""
    cells = re.findall(r"^\| `([^`]+)`", DOC.read_text(encoding="utf-8"), flags=re.MULTILINE)
    # В документе несколько таблиц; пути среза — только те ячейки, что
    # действительно указывают на каталог/файл репозитория.
    documented = {cell.rstrip("/") for cell in cells if (ROOT / cell.rstrip("/")).exists()}
    declared = {slice_.path for slice_ in TYPED_SLICES}
    assert documented == declared, f"docs/typechecking.md перечисляет {documented}, а проверяется {declared}"


def test_default_slice_is_the_whole_repository_without_exclusions() -> None:
    assert TYPED_SLICES == (Slice("."),)
    assert all(not slice_.exclude for slice_ in TYPED_SLICES)


def test_historical_livedub_stub_covers_every_top_level_case() -> None:
    source = ast.parse((ROOT / "tests" / "livedub_qa_cases.py").read_text(encoding="utf-8"))
    stub = ast.parse((ROOT / "tests" / "livedub_qa_cases.pyi").read_text(encoding="utf-8"))
    source_functions = {
        node.name: node for node in source.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    stub_functions = {
        node.name: node for node in stub.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert stub_functions.keys() == source_functions.keys()

    for name, source_node in source_functions.items():
        stub_node = stub_functions[name]
        source_args = [arg.arg for arg in source_node.args.args]
        stub_args = [arg.arg for arg in stub_node.args.args]
        assert stub_args == source_args, name
        annotations = {
            "monkeypatch": "pytest.MonkeyPatch",
            "tmp_path": "Path",
        }
        expected_return = "None"
        if name in {"_chain", "_flat"}:
            annotations["nodes"] = "list[Any]"
            expected_return = "list[Any]" if name == "_chain" else "str"
        stub_annotations = []
        for arg in stub_node.args.args:
            assert arg.annotation is not None, name
            stub_annotations.append(ast.unparse(arg.annotation))
        assert stub_annotations == [annotations[arg] for arg in source_args], name
        assert stub_node.returns is not None
        assert ast.unparse(stub_node.returns) == expected_return, name


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
        f"срез {[s.path for s in TYPED_SLICES]} не проходит mypy --strict ({error_count(output)} ошибок):\n{output}"
    )
