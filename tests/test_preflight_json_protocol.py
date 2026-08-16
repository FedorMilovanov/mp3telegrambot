from __future__ import annotations

import json

import pytest

from tools.voxcpm2 import dub_job_preflight as preflight


def _payload() -> dict[str, object]:
    return {
        "python": r"C:\AI-Archive\VoxCPM2-CPU-TEST\.venv\Scripts\python.exe",
        "loaded": {"voxcpm": r"C:\runtime\voxcpm\__init__.py"},
    }


def _encode(payload: object) -> str:
    return preflight.PREFLIGHT_JSON_MARKER + json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def test_marked_payload_survives_import_noise_before_and_after() -> None:
    payload = _payload()
    stdout = "\n".join(("Loading VoxCPM runtime...", _encode(payload), "shutdown diagnostic"))
    decoded, noise = preflight._decode_probe_payload(stdout)
    assert decoded == payload
    assert "Loading VoxCPM runtime" in noise
    assert "shutdown diagnostic" in noise
    assert preflight.PREFLIGHT_JSON_MARKER not in noise


def test_last_valid_marked_payload_wins() -> None:
    current = _payload()
    decoded, noise = preflight._decode_probe_payload(
        "\n".join((_encode({"python": "old", "loaded": {}}), "banner", _encode(current)))
    )
    assert decoded == current
    assert "banner" in noise


def test_plain_or_corrupt_json_is_rejected_fail_closed() -> None:
    with pytest.raises(RuntimeError, match="маркированный JSON"):
        preflight._decode_probe_payload(json.dumps(_payload(), ensure_ascii=False))
    with pytest.raises(RuntimeError, match="маркированный JSON") as exc:
        preflight._decode_probe_payload("banner\n" + preflight.PREFLIGHT_JSON_MARKER + "{broken")
    assert "banner" in str(exc.value)


def test_payload_must_be_an_object() -> None:
    with pytest.raises(RuntimeError, match="маркированный JSON"):
        preflight._decode_probe_payload(_encode(["not", "an", "object"]))


def test_preflight_protocol_is_source_owned() -> None:
    assert preflight.PREFLIGHT_JSON_TRANSPORT_POLICY == "marked-preflight-json-transport-v2"
    assert callable(preflight._runtime_paths)
    assert callable(preflight._probe_imports)
    source = __import__("pathlib").Path(preflight.__file__).read_text(encoding="utf-8")
    assert "def install_preflight_json" not in source
    assert "sys.modules" not in source
