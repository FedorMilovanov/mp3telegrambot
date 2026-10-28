"""Executable contracts for opt-in developer-experience configuration."""

from __future__ import annotations

import ast
import configparser
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_editorconfig_preserves_cross_platform_source_conventions() -> None:
    parser = configparser.RawConfigParser()
    source = (ROOT / ".editorconfig").read_text(encoding="utf-8")
    parser.read_string("[editorconfig]\n" + source)

    assert parser["editorconfig"].getboolean("root") is True
    defaults = parser["*"]
    assert defaults["charset"] == "utf-8"
    assert defaults["end_of_line"] == "lf"
    assert defaults.getboolean("insert_final_newline") is True
    assert defaults["indent_style"] == "space"
    assert defaults.getint("indent_size") == 4
    assert parser["*.{json,yml,yaml}"].getint("indent_size") == 2
    assert parser["*.py"].getint("max_line_length") == 120
    assert parser["*.ps1"]["end_of_line"] == "crlf"


def test_slow_marker_is_registered() -> None:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        pytest_config = tomllib.load(stream)["tool"]["pytest"]["ini_options"]

    markers = pytest_config["markers"]
    assert any(marker.startswith("slow:") for marker in markers)


def test_slow_marker_is_limited_to_reviewed_real_wait_tests() -> None:
    marked: set[str] = set()
    for path in (ROOT / "tests").glob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            is_slow = any(
                isinstance(decorator, ast.Attribute)
                and decorator.attr == "slow"
                and isinstance(decorator.value, ast.Attribute)
                and decorator.value.attr == "mark"
                and isinstance(decorator.value.value, ast.Name)
                and decorator.value.value.id == "pytest"
                for decorator in node.decorator_list
            )
            if is_slow:
                marked.add(f"{path.relative_to(ROOT).as_posix()}::{node.name}")

    assert marked == {
        "tests/test_async_process_tree.py::test_timeout_stops_grandchild_that_ignores_graceful_signal",
        "tests/test_livedub_quality_runtime.py::test_info_card_uses_shared_bounded_transport_without_mutating_registry",
    }
