from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import services
import services.runtime_manifest as runtime_manifest

ROOT = Path(__file__).resolve().parents[1]


def test_services_import_is_silent_under_strict_cp1252() -> None:
    script = r"""
import io
import sys
original = sys.__stdout__
out_bytes = io.BytesIO()
err_bytes = io.BytesIO()
sys.stdout = io.TextIOWrapper(out_bytes, encoding="cp1252", errors="strict", write_through=True)
sys.stderr = io.TextIOWrapper(err_bytes, encoding="cp1252", errors="strict", write_through=True)
import services
original.write(f"SERVICES_IMPORT_OUTPUT={len(out_bytes.getvalue())}:{len(err_bytes.getvalue())}\n")
"""
    process = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == "SERVICES_IMPORT_OUTPUT=0:0"
    assert process.stderr == ""


def test_manifest_events_are_structured_and_emitted_only_on_request(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_manifest,
        "runtime_manifest_payload",
        lambda: {
            "policy": "fixture-policy",
            "features": {
                "fixture-ok": {"state": "installed", "detail": "ready"},
                "fixture-bad": {"state": "failed", "detail": "ValueError: broken"},
            },
        },
    )
    events = services.service_bootstrap_events()
    output: list[str] = []
    assert services.emit_service_bootstrap_diagnostics(output.append) == 2
    assert events[0]["component"] == "fixture-ok"
    assert events[0]["ok"] is True
    assert events[1]["component"] == "fixture-bad"
    assert events[1]["ok"] is False
    assert output[0].startswith("✅ fixture-ok: ready")
    assert output[1].startswith("⚠️ fixture-bad: ValueError: broken")


def test_bot_entrypoint_configures_stdio_before_emoji_diagnostics() -> None:
    source = (ROOT / "bot_new.py").read_text(encoding="utf-8")
    configure_call = source.index("\n_configure_stdio()\n")
    first_emoji_print = min(source.index('print("❌'), source.index('print("⚠️'))
    service_import = source.index("from services import emit_service_bootstrap_diagnostics")
    assert configure_call < first_emoji_print
    assert configure_call < service_import
    assert "emit_service_bootstrap_diagnostics()" in source


def test_services_package_is_side_effect_free_runtime_facade() -> None:
    source = (ROOT / "services" / "__init__.py").read_text(encoding="utf-8")
    assert "sys.meta_path.insert" not in source
    assert "sys.meta_path.remove" not in source
    assert "_record_bootstrap" not in source
    assert "_capture_bootstrap_output" not in source
    assert "bootstrap_pre_main()" not in source
    assert "service_bootstrap_events" in source
