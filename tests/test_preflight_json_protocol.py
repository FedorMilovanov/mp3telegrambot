from __future__ import annotations

import json

import pytest

from tools.voxcpm2 import preflight_json_protocol as protocol


def _payload() -> dict[str, object]:
    return {
        "python": r"C:\AI-Archive\VoxCPM2-CPU-TEST\.venv\Scripts\python.exe",
        "loaded": {"voxcpm": r"C:\runtime\voxcpm\__init__.py"},
    }


def test_marked_payload_survives_import_noise_before_and_after() -> None:
    payload = _payload()
    stdout = "\n".join(
        (
            "Loading VoxCPM runtime...",
            "warning: optional acceleration unavailable",
            protocol.encode_payload(payload),
            "diagnostic emitted during interpreter shutdown",
        )
    )

    decoded, noise = protocol.decode_payload(stdout)

    assert decoded == payload
    assert "Loading VoxCPM runtime" in noise
    assert "interpreter shutdown" in noise
    assert protocol.MARKER not in noise


def test_last_valid_marked_payload_wins() -> None:
    old = {"python": "old", "loaded": {}}
    current = _payload()
    stdout = "\n".join(
        (
            protocol.encode_payload(old),
            "third-party banner",
            protocol.encode_payload(current),
        )
    )

    decoded, noise = protocol.decode_payload(stdout)

    assert decoded == current
    assert "third-party banner" in noise


def test_plain_json_is_rejected_fail_closed() -> None:
    stdout = json.dumps(_payload(), ensure_ascii=False)

    with pytest.raises(RuntimeError, match="не вернул маркированный JSON"):
        protocol.decode_payload(stdout)


def test_corrupt_marked_json_is_rejected_with_diagnostics() -> None:
    stdout = "banner\n" + protocol.MARKER + "{broken-json"

    with pytest.raises(RuntimeError, match="не вернул маркированный JSON") as exc:
        protocol.decode_payload(stdout)

    assert "banner" in str(exc.value)


def test_payload_must_be_an_object() -> None:
    stdout = protocol.MARKER + json.dumps(["not", "an", "object"])

    with pytest.raises(RuntimeError, match="не вернул маркированный JSON"):
        protocol.decode_payload(stdout)


def test_worker_module_installs_protocol_before_main() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "voxcpm2"
        / "dub_worker_hardened"
        / "__main__.py"
    ).read_text(encoding="utf-8")

    assert "install_preflight_json()" in source
    assert source.index("install_preflight_json()") < source.index("main()")
