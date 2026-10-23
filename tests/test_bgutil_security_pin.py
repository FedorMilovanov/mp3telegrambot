"""Security contract for the pinned YouTube PO-token provider.

Upstream advisory GHSA-qpv9-8xfj-xx9m (RCE) affects every release below 2.0.0:
the HTTP server bound ``::`` and fell back to ``0.0.0.0``, so anything on the
same network could POST a crafted request and run code.  2.0+ binds loopback,
rejects browser-originated requests and validates proxy schemes.

These tests keep three independent controls true:

1. the pin is not below the patched release;
2. the pin exists in exactly one place (``core/bgutil_pin.py``);
3. our own spawn command binds the server to loopback explicitly, and the
   runtime fails closed when the port turns out to be reachable from a
   non-loopback address.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import bgutil_pin
from services import bgutil_http_runtime as http_runtime
from services import youtube_po_token_runtime as po
from tools import ensure_bgutil_provider as setup

ROOT = Path(__file__).resolve().parents[1]


def test_pin_is_at_or_above_the_rce_fix() -> None:
    assert bgutil_pin.pin_is_patched() is True
    assert bgutil_pin.pinned_version() >= bgutil_pin.patched_version()
    assert bgutil_pin.bgutil_pin_problems() == []


def test_pin_is_a_full_commit_on_the_pinned_release() -> None:
    assert len(bgutil_pin.BGUTIL_COMMIT) == 40
    assert all(char in "0123456789abcdef" for char in bgutil_pin.BGUTIL_COMMIT)
    assert bgutil_pin.BGUTIL_REPOSITORY.endswith("bgutil-ytdlp-pot-provider.git")


def test_pin_has_exactly_one_owner() -> None:
    """Provisioning and the readiness gate must import, never re-declare."""
    for relative in (
        "tools/ensure_bgutil_provider.py",
        "services/youtube_po_token_runtime.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "from core.bgutil_pin import" in source, relative
        assert "a0be2352807e3bd6991f09d2cab685a0ab825b26" not in source, relative
        assert 'BGUTIL_VERSION = "' not in source, relative

    assert setup.BGUTIL_VERSION == bgutil_pin.BGUTIL_VERSION
    assert setup.BGUTIL_COMMIT == bgutil_pin.BGUTIL_COMMIT
    assert po.BGUTIL_EXPECTED_VERSION == bgutil_pin.BGUTIL_VERSION
    assert po.BGUTIL_EXPECTED_COMMIT == bgutil_pin.BGUTIL_COMMIT


def test_no_workflow_hardcodes_the_pin() -> None:
    """CI is part of the pin's blast radius.

    ``cut-policy-ci.yml`` used to assert ``provider_version == '1.3.1'``: the
    pin was in one module, but the workflow kept its own copy and broke the
    moment the pin moved. Workflows that touch the runtime must import the pin.
    """
    offenders: list[str] = []
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        if "youtube_po_token_runtime" not in text and "bgutil" not in text:
            continue
        for literal in (
            bgutil_pin.BGUTIL_VERSION,
            bgutil_pin.BGUTIL_FIRST_PATCHED_VERSION,
            "1.3.1",
            "a0be2352807e3bd6991f09d2cab685a0ab825b26",
        ):
            if f"'{literal}'" in text or f'"{literal}"' in text:
                offenders.append(f"{path.name}: {literal}")
    assert offenders == []

    preflight = (ROOT / ".github" / "workflows" / "cut-policy-ci.yml").read_text(
        encoding="utf-8"
    )
    assert "from core.bgutil_pin import BGUTIL_VERSION" in preflight
    assert "== BGUTIL_VERSION" in preflight


def test_cut_policy_workflow_reacts_to_pin_changes() -> None:
    workflow = (ROOT / ".github" / "workflows" / "cut-policy-ci.yml").read_text(
        encoding="utf-8"
    )
    assert workflow.count('"core/bgutil_pin.py"') == 2, "both push and PR filters"


def test_provisioner_refuses_an_unpatched_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bgutil_pin, "BGUTIL_VERSION", "1.3.2")
    monkeypatch.setattr(bgutil_pin, "pin_is_patched", lambda: False)

    with pytest.raises(setup.ProvisionError, match="remote code execution"):
        setup._require_patched_pin()


def test_marker_text_matches_gate_expectation() -> None:
    assert bgutil_pin.marker_text() == (
        f"{po.BGUTIL_EXPECTED_VERSION}@{po.BGUTIL_EXPECTED_COMMIT}"
    )


def test_server_is_spawned_with_an_explicit_loopback_host() -> None:
    """The binding must be our argv, not an upstream default."""
    source = (ROOT / "services/bgutil_http_runtime.py").read_text(encoding="utf-8")
    assert "_SERVER_HOST_ARG" in source
    assert '"--host"' in source
    assert "0.0.0.0" not in source
    assert '"::"' not in source
    assert http_runtime.DEFAULT_HOST == "127.0.0.1"
    assert http_runtime.DEFAULT_BASE_URL == "http://127.0.0.1:4416"


def test_no_production_module_binds_the_provider_broadly() -> None:
    for relative in (
        "services/bgutil_http_runtime.py",
        "services/youtube_po_token_runtime.py",
        "tools/ensure_bgutil_provider.py",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "0.0.0.0" not in text, relative


def test_loopback_probe_fails_closed_when_the_port_is_externally_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        http_runtime,
        "_external_probe_addresses",
        lambda port: [(2, "192.0.2.10", port)],
    )
    monkeypatch.setattr(http_runtime, "_reachable", lambda *_args, **_kwargs: True)

    with pytest.raises(http_runtime.BgutilHttpRuntimeError) as caught:
        http_runtime.require_loopback_binding(4416)
    assert "192.0.2.10:4416" in str(caught.value)
    assert bgutil_pin.BGUTIL_SECURITY_ADVISORY in str(caught.value)


def test_loopback_probe_passes_when_only_loopback_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        http_runtime,
        "_external_probe_addresses",
        lambda port: [(2, "192.0.2.10", port)],
    )
    monkeypatch.setattr(http_runtime, "_reachable", lambda *_args, **_kwargs: False)

    http_runtime.require_loopback_binding(4416)


def test_loopback_probe_is_a_no_op_without_a_non_loopback_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(http_runtime, "_external_probe_addresses", lambda _port: [])
    monkeypatch.setattr(
        http_runtime,
        "_reachable",
        lambda *_args, **_kwargs: pytest.fail("nothing to probe, nothing to connect"),
    )

    http_runtime.require_loopback_binding(4416)


def test_docs_record_the_security_upgrade() -> None:
    doc = (ROOT / "docs" / "youtube-po-token-runtime.md").read_text(encoding="utf-8")
    assert bgutil_pin.BGUTIL_VERSION in doc
    assert bgutil_pin.BGUTIL_SECURITY_ADVISORY in doc
    assert "127.0.0.1" in doc
    assert "--host" in doc
