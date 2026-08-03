from __future__ import annotations

import ast
from pathlib import Path


PATH = Path("pipelines/main_pipeline.py")


def _module() -> tuple[str, ast.Module]:
    source = PATH.read_text(encoding="utf-8")
    return source, ast.parse(source)


def _function_source(source: str, tree: ast.Module, name: str) -> str:
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.AsyncFunctionDef) and item.name == name
    )
    return ast.get_source_segment(source, node) or ""


def _imported_names(tree: ast.Module, module: str) -> set[str]:
    return {
        alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == module
        for alias in node.names
    }


def test_inprocess_ytdlp_metadata_uses_owned_soft_timeout() -> None:
    source, tree = _module()
    function = _function_source(source, tree, "_ytdlp_info_inprocess")

    assert "await_owned_with_soft_timeout" in _imported_names(
        tree,
        "services.async_worker",
    )
    assert "await await_owned_with_soft_timeout(" in function
    assert "asyncio.to_thread(_run)" in function
    assert "run_in_executor" not in function
    assert "asyncio.wait_for" not in function


def test_late_inprocess_metadata_result_is_used_before_fallback() -> None:
    source, tree = _module()
    function = _function_source(source, tree, "_ytdlp_info_inprocess")

    assert "deadline_exceeded" in function
    assert "использую поздний результат" in function
    assert "return info if isinstance(info, dict) else None" in function
