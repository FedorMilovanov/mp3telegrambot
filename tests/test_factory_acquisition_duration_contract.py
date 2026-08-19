from __future__ import annotations

import ast
from pathlib import Path


def _calls_in_process_factory():
    tree = ast.parse(Path("pipelines/shorts_factory.py").read_text(encoding="utf-8"))
    process = next(
        node
        for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        and node.name == "process_shorts_factory"
    )
    return [node for node in ast.walk(process) if isinstance(node, ast.Call)]


def _name(call: ast.Call) -> str:
    return call.func.id if isinstance(call.func, ast.Name) else ""


def test_factory_audio_and_video_acquisition_receive_expected_duration():
    calls = _calls_in_process_factory()
    for target in ("_download_factory_audio", "download_video_for_shorts"):
        matching = [call for call in calls if _name(call) == target]
        assert matching, target
        assert all(
            any(keyword.arg == "expected_duration" for keyword in call.keywords)
            for call in matching
        ), target
