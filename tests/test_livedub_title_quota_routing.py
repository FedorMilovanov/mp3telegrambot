from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "pipelines" / "main_pipeline.py"
FUNCTION = "_translate_livedub_title_for_caption"


def _title_function() -> ast.AsyncFunctionDef:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == FUNCTION
    ]
    assert len(matches) == 1, f"expected exactly one {FUNCTION}, found {len(matches)}"
    return matches[0]


def test_livedub_title_fallback_uses_common_project_aware_router() -> None:
    func = _title_function()

    router_calls = [
        node
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "gemini_generate"
    ]
    assert len(router_calls) == 1
    router = router_calls[0]
    assert len(router.args) >= 2
    assert isinstance(router.args[0], ast.Name)
    assert router.args[0].id == "GEMINI_CLIENTS"
    assert isinstance(router.args[1], ast.Name)
    assert router.args[1].id == "_generate_title"

    model_kw = next((kw for kw in router.keywords if kw.arg == "model_name"), None)
    assert model_kw is not None
    assert isinstance(model_kw.value, ast.Name)
    assert model_kw.value.id == "GEMINI_MODEL"

    direct_first_client = [
        node
        for node in ast.walk(func)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "GEMINI_CLIENTS"
        and isinstance(node.slice, ast.Constant)
        and node.slice.value == 0
    ]
    assert direct_first_client == []


def test_livedub_title_router_keeps_sdk_callback_and_total_timeout() -> None:
    func = _title_function()

    callbacks = [
        node
        for node in func.body
        if isinstance(node, ast.Try)
        for node in node.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_generate_title"
    ]
    assert len(callbacks) == 1
    callback = callbacks[0]
    sdk_calls = [
        node
        for node in ast.walk(callback)
        if isinstance(node, ast.Call)
        and ast.unparse(node.func) == "client.aio.models.generate_content"
    ]
    assert len(sdk_calls) == 1

    wait_calls = [
        node
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and ast.unparse(node.func) == "asyncio.wait_for"
    ]
    assert len(wait_calls) == 1
    timeout_kw = next((kw for kw in wait_calls[0].keywords if kw.arg == "timeout"), None)
    assert timeout_kw is not None
    assert isinstance(timeout_kw.value, ast.Constant)
    assert timeout_kw.value.value == 30.0
