from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import services

ROOT = Path(__file__).resolve().parents[1]


def test_services_import_is_silent_under_strict_cp1252() -> None:
    script = r'''
import io
import sys

# Avoid installing the optional after-import finder in this focused probe.
sys.argv.append("pytest-probe")
original = sys.__stdout__
out_bytes = io.BytesIO()
err_bytes = io.BytesIO()
sys.stdout = io.TextIOWrapper(out_bytes, encoding="cp1252", errors="strict", write_through=True)
sys.stderr = io.TextIOWrapper(err_bytes, encoding="cp1252", errors="strict", write_through=True)
import services
out_size = len(out_bytes.getvalue())
err_size = len(err_bytes.getvalue())
original.write(f"SERVICES_IMPORT_OUTPUT={out_size}:{err_size}\n")
'''
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


def test_bootstrap_events_are_structured_and_emitted_explicitly(monkeypatch) -> None:
    monkeypatch.setattr(services, "_BOOTSTRAP_EVENTS", [])
    services._record_bootstrap("Fixture success", ok=True, detail="installed")
    services._record_bootstrap("Fixture failure", ok=False, detail=ValueError("broken"))

    events = services.service_bootstrap_events()
    output: list[str] = []
    emitted = services.emit_service_bootstrap_diagnostics(output.append)

    assert emitted == 2
    assert events == (
        {
            "sequence": 1,
            "component": "Fixture success",
            "ok": True,
            "detail": "installed",
            "policy": services.SERVICE_BOOTSTRAP_DIAGNOSTICS_POLICY,
        },
        {
            "sequence": 2,
            "component": "Fixture failure",
            "ok": False,
            "detail": "broken",
            "policy": services.SERVICE_BOOTSTRAP_DIAGNOSTICS_POLICY,
        },
    )
    assert output[0].startswith("✅ Fixture success: installed")
    assert output[1].startswith("⚠️ Fixture failure: broken")


def test_transitive_output_is_captured_without_being_lost(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(services, "_BOOTSTRAP_EVENTS", [])

    with services._capture_bootstrap_output("Fixture import"):
        print("stdout sentinel")
        print("stderr sentinel", file=sys.stderr)

    captured = capsys.readouterr()
    events = services.service_bootstrap_events()

    assert captured.out == ""
    assert captured.err == ""
    assert [event["component"] for event in events] == [
        "Fixture import captured stdout",
        "Fixture import captured stderr",
    ]
    assert events[0]["detail"] == "stdout sentinel"
    assert events[1]["detail"] == "stderr sentinel"


def test_bot_entrypoint_configures_stdio_before_emoji_diagnostics() -> None:
    source = (ROOT / "bot_new.py").read_text(encoding="utf-8")

    configure_call = source.index("\n_configure_stdio()\n")
    first_emoji_print = min(
        source.index('print("❌'),
        source.index('print("⚠️'),
    )
    service_import = source.index("from services import emit_service_bootstrap_diagnostics")

    assert configure_call < first_emoji_print
    assert configure_call < service_import
    assert "emit_service_bootstrap_diagnostics()" in source


def test_services_package_has_no_import_time_print_calls() -> None:
    source = (ROOT / "services" / "__init__.py").read_text(encoding="utf-8")
    before_finder = source.split("class _AfterImportLoader", 1)[0]

    assert "print(" not in before_finder
    assert "_record_bootstrap(" in before_finder
    assert "_capture_bootstrap_output(" in before_finder
