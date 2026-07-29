from __future__ import annotations

import json
from pathlib import Path

import pytest

from handlers import dub_wizard


def _payload(**overrides):
    return {
        "schema_version": 1,
        "video_id": "AbCdEf12345",
        "source_url": "https://youtube.com/watch?v=AbCdEf12345",
        "translation_mode": "gemini",
        **overrides,
    }


def test_wizard_writes_validated_request_inside_project_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        dub_wizard.generic_project_runtime._legacy,
        "studio_root",
        lambda: tmp_path / "studio",
    )
    path = dub_wizard._write_request("dub-0123456789", _payload())
    expected = (
        tmp_path
        / "studio"
        / "projects"
        / "dub-0123456789"
        / "request.json"
    ).resolve()
    assert path == expected
    assert json.loads(path.read_text(encoding="utf-8")) == _payload()
    assert not list(path.parent.glob("request.json.tmp.*"))


@pytest.mark.parametrize(
    "payload",
    [
        _payload(schema_version=True),
        _payload(schema_version=1.5),
        _payload(source_url="https://youtube.com/watch?v=OtherId999"),
        _payload(source_url="https://youtube.com/playlist?list=PL123"),
        _payload(translation_mode="unknown"),
    ],
)
def test_invalid_wizard_request_is_not_written(
    monkeypatch,
    tmp_path: Path,
    payload: dict,
) -> None:
    monkeypatch.setattr(
        dub_wizard.generic_project_runtime._legacy,
        "studio_root",
        lambda: tmp_path / "studio",
    )
    with pytest.raises(RuntimeError):
        dub_wizard._write_request("dub-0123456789", payload)
    request_path = (
        tmp_path
        / "studio"
        / "projects"
        / "dub-0123456789"
        / "request.json"
    )
    assert not request_path.exists()
    assert not list(request_path.parent.glob("request.json.tmp.*"))


def test_wizard_facade_patches_legacy_request_writer() -> None:
    assert Path(dub_wizard.__file__).name == "__init__.py"
    assert dub_wizard._legacy._write_request is dub_wizard._write_request
    source = Path(dub_wizard.__file__).read_text(encoding="utf-8")
    assert "generic_project_runtime.validate_request_payload(payload)" in source
    assert "generic_project_runtime.save_json(destination, validated)" in source
