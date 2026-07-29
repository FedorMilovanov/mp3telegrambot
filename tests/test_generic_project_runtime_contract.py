from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.voxcpm2 import generic_project_runtime as runtime


def _request(**overrides):
    return {
        "schema_version": 1,
        "video_id": "AbCdEf12345",
        "source_url": "https://youtube.com/watch?v=AbCdEf12345",
        "translation_mode": "gemini",
        **overrides,
    }


def test_project_root_requires_canonical_project_id(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runtime._legacy, "studio_root", lambda: tmp_path)
    valid = runtime.project_root("dub-0123456789")
    assert valid == (tmp_path / "projects" / "dub-0123456789").resolve()
    assert valid.is_dir()

    for invalid in ("project-1", "../escape", "dub-XYZ", "dub-123"):
        with pytest.raises(RuntimeError, match="project ID"):
            runtime.project_root(invalid)


def test_request_schema_and_source_identity_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    path = root / "request.json"

    bad_payloads = (
        _request(schema_version=True),
        _request(schema_version=1.5),
        _request(source_url="https://youtube.com/watch?v=OtherId999"),
        _request(source_url="https://youtube.com/playlist?list=PL123"),
        _request(translation_mode="mystery"),
    )
    for payload in bad_payloads:
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(RuntimeError):
            runtime.load_request(root)


def test_valid_request_is_returned_without_rewriting(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    payload = _request(translation_mode="direct")
    (root / "request.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    assert runtime.load_request(root) == payload


def test_atomic_json_rejects_nan_without_replacing_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"stable":true}', encoding="utf-8")

    with pytest.raises(ValueError):
        runtime.save_json(path, {"bad": float("nan")})
    assert path.read_text(encoding="utf-8") == '{"stable":true}'
    assert not list(tmp_path.glob("state.json.tmp.*"))

    runtime.save_json(path, {"stable": False, "value": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "stable": False,
        "value": 1,
    }
    assert not list(tmp_path.glob("state.json.tmp.*"))


def test_project_runtime_facade_patches_legacy_orchestration() -> None:
    assert Path(runtime.__file__).name == "__init__.py"
    assert runtime._legacy.project_root is runtime.project_root
    assert runtime._legacy.load_request is runtime.load_request
    assert runtime._legacy.save_json is runtime.save_json
