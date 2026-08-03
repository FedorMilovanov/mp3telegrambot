from __future__ import annotations

import ast
from pathlib import Path


def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.AsyncFunctionDef) and item.name == name
    )
    return ast.get_source_segment(source, node) or ""


def test_inprocess_ytdlp_metadata_uses_owned_soft_timeout() -> None:
    path = Path("pipelines/main_pipeline.py")
    source = path.read_text(encoding="utf-8")
    function = _function_source(path, "_ytdlp_info_inprocess")

    assert "from services.async_worker import await_owned_with_soft_timeout" in source
    assert "await await_owned_with_soft_timeout(" in function
    assert "asyncio.to_thread(_run)" in function
    assert "run_in_executor" not in function
    assert "asyncio.wait_for" not in function


def test_late_inprocess_metadata_result_is_used_before_fallback() -> None:
    function = _function_source(
        Path("pipelines/main_pipeline.py"),
        "_ytdlp_info_inprocess",
    )

    assert "deadline_exceeded" in function
    assert "использую поздний результат" in function
    assert "return info if isinstance(info, dict) else None" in function
