from __future__ import annotations

from pathlib import Path

from services.dub_worker_release import (
    PREFLIGHT_TRANSPORT_POLICY,
    RELEASE_POLICY,
    WORKER_RUNTIME,
)


def test_worker_release_marker_is_shared_and_advanced() -> None:
    assert WORKER_RUNTIME == "dub-worker-quality-v4.9"
    assert RELEASE_POLICY == "single-source-worker-release-identity-v1"
    assert PREFLIGHT_TRANSPORT_POLICY == "marked-preflight-json-transport-v1"


def test_supervisor_and_worker_entrypoint_use_one_release_identity() -> None:
    root = Path(__file__).resolve().parents[1]
    supervisor = (
        root / "services" / "dub_studio_runtime" / "__init__.py"
    ).read_text(encoding="utf-8")
    worker_main = (
        root / "tools" / "voxcpm2" / "dub_worker_hardened" / "__main__.py"
    ).read_text(encoding="utf-8")

    assert "from services.dub_worker_release import WORKER_RUNTIME" in supervisor
    assert "_WORKER_RUNTIME = WORKER_RUNTIME" in supervisor
    assert "_legacy._WORKER_RUNTIME = WORKER_RUNTIME" in supervisor
    assert "from services.dub_worker_release import WORKER_RUNTIME" in worker_main
    assert "package._RUNTIME_VERSION = WORKER_RUNTIME" in worker_main
    assert "_legacy._RUNTIME_VERSION = WORKER_RUNTIME" in worker_main
    assert worker_main.index("activate_release_identity()") < worker_main.index(
        "install_preflight_json()"
    )
    assert worker_main.index("install_preflight_json()") < worker_main.index("main()")


def test_release_rollout_never_reuses_previous_v48_marker() -> None:
    root = Path(__file__).resolve().parents[1]
    release = (root / "services" / "dub_worker_release.py").read_text(
        encoding="utf-8"
    )
    supervisor = (
        root / "services" / "dub_studio_runtime" / "__init__.py"
    ).read_text(encoding="utf-8")
    worker_main = (
        root / "tools" / "voxcpm2" / "dub_worker_hardened" / "__main__.py"
    ).read_text(encoding="utf-8")

    assert "dub-worker-quality-v4.8" not in release
    assert "dub-worker-quality-v4.8" not in supervisor
    assert "dub-worker-quality-v4.8" not in worker_main
