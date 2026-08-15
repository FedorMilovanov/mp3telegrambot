from __future__ import annotations

import services
import services.runtime_manifest as runtime_manifest


def test_manifest_installer_detail_is_preserved_in_explicit_diagnostics(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_manifest,
        "runtime_manifest_payload",
        lambda: {
            "policy": "declarative-runtime-composition-v2",
            "features": {
                "quality": {
                    "state": "installed",
                    "detail": "services.pre_main_policy.configure_pre_main_policy",
                }
            },
        },
    )
    event = services.service_bootstrap_events()[0]
    assert event["component"] == "quality"
    assert event["ok"] is True
    assert event["detail"] == "services.pre_main_policy.configure_pre_main_policy"
