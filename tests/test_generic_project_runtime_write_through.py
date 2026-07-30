from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from typing import Any

import pytest

from tools.voxcpm2 import generic_clean_custom_runtime as custom_route
from tools.voxcpm2 import generic_clean_gemini_runtime as gemini_route
from tools.voxcpm2 import generic_project_runtime as runtime


def test_project_runtime_imports_write_through_package() -> None:
    assert Path(runtime.__file__).name == "__init__.py"
    assert runtime.POLICY == "generic-project-runtime-write-through-v2"
    source = Path(runtime.__file__).read_text(encoding="utf-8")
    assert "class _WriteThroughModule" in source
    assert "_module.__class__ = _WriteThroughModule" in source


@pytest.mark.parametrize(
    ("name", "replacement"),
    [
        ("acquire_transcript", gemini_route._acquire_transcript_with_actual_language),
        ("translate_groups_max", gemini_route.expressive_translation.translate_groups),
        ("parse_manual_vtt", gemini_route.checked.parse_creator_vtt_preserving_text),
        ("_build_render_segments", gemini_route._build_clean_render_segments),
        ("_run_voxcpm_and_master", gemini_route._run_clean_voxcpm_and_master),
        ("_validate_translation_payload", custom_route.strict_translation_payload.validate_full),
    ],
)
def test_clean_route_assignment_reaches_legacy_function_globals(
    name: str,
    replacement: Any,
) -> None:
    original_package = getattr(runtime, name)
    original_legacy = getattr(runtime._legacy, name)
    try:
        setattr(runtime, name, replacement)
        assert getattr(runtime, name) is replacement
        assert getattr(runtime._legacy, name) is replacement
        assert runtime._legacy.main.__globals__[name] is replacement
    finally:
        setattr(runtime, name, original_package)
        # Package restoration is write-through, but retain the exact original
        # legacy value even if an earlier test imported a configured route.
        setattr(runtime._legacy, name, original_legacy)


@pytest.mark.parametrize(
    "name",
    ["project_root", "load_request", "save_json", "validate_request_payload"],
)
def test_strict_project_hooks_cannot_be_replaced_from_package(name: str) -> None:
    with pytest.raises(RuntimeError, match="cannot be replaced"):
        setattr(runtime, name, lambda *_args, **_kwargs: None)
    assert getattr(runtime._legacy, name) is getattr(runtime, name)


def test_atomic_json_concurrent_writers_leave_one_complete_document(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "state.json"

    def write(index: int) -> None:
        runtime.save_json(
            destination,
            {"schema_version": 1, "index": index, "payload": "ok" * 100},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(32)))

    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["index"] in range(32)
    assert payload["payload"] == "ok" * 100
    assert not list(tmp_path.glob("state.json.tmp.*"))
