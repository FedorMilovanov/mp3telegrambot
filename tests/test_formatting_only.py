"""Safety contract for the formatting-only diff-cover exception."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tomllib

from tools.check_formatting_only import classify, main, same_ast


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repository(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "tests@example.invalid")
    _git(tmp_path, "config", "user.name", "Tests")
    (tmp_path / "services").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "services/example.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "tests/test_example.py").write_text(
        'def test_value():\n    """Original docstring."""\n    assert True\n', encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("before\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def test_ast_comparison_detects_semantics_not_layout() -> None:
    before = "def f(values):\n    return [value for value in values if value]\n"
    after = "def f(values):\n    return [\n        value\n        for value in values\n        if value\n    ]\n"
    assert same_ast(before, after)
    assert not same_ast(before, after.replace("if value", "if not value"))
    assert not same_ast("def broken(:\n", after)
    assert same_ast('"""One."""\n', '"""Two."""\n', ignore_docstrings=True)
    assert not same_ast('"""One."""\n', '"""Two."""\n')


def test_formatting_diff_accepts_docstrings_docs_and_blame_ledger(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "services/example.py").write_text("value = (\n    1\n)\n", encoding="utf-8")
    (repo / "tests/test_example.py").write_text(
        'def test_value():\n    """Reformatted docstring."""\n    assert True\n', encoding="utf-8"
    )
    (repo / "README.md").write_text("after\n", encoding="utf-8")
    (repo / ".git-blame-ignore-revs").write_text("abc\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "format")

    assert classify(repo, "HEAD~1") == (True, [])


def test_semantic_and_configuration_changes_use_ordinary_diff_cover(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    (repo / "services/example.py").write_text("value = 2\n", encoding="utf-8")
    pure, reasons = classify(repo, "HEAD")
    assert not pure
    assert reasons == ["services/example.py: executable AST changed"]

    (repo / "services/example.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
    (repo / "services/new.py").write_text("new = True\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "semantic")
    pure, reasons = classify(repo, "HEAD~1")
    assert not pure
    assert "pyproject.toml: non-formatting file type" in reasons
    assert "services/new.py: Python status A is not modification" in reasons


def test_cli_emits_only_the_action_output_on_stdout(tmp_path: Path, capsys) -> None:
    repo = _repository(tmp_path)
    assert main(["--root", str(repo), "--compare-ref", "HEAD"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "pure=false\n"
    assert "empty diff" in captured.err

    assert main(["--root", str(repo / "missing")]) == 0
    assert capsys.readouterr().out == "pure=false\n"


def test_ruff_never_rewrites_historical_cases_or_markdown_examples() -> None:
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    excluded = set(config["tool"]["ruff"]["exclude"])
    assert "tests/livedub_qa_cases.py" in excluded
    assert "docs" in excluded
