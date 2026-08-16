from __future__ import annotations

from pathlib import Path

from services import dub_studio_runtime, dub_worker
from services.dub_worker_release import (
    PREFLIGHT_TRANSPORT_POLICY,
    RELEASE_POLICY,
    WORKER_RUNTIME,
)


ROOT = Path(__file__).resolve().parents[1]


def test_worker_release_marker_is_shared_and_advanced() -> None:
    assert WORKER_RUNTIME.startswith("dub-worker-quality-v")
    assert WORKER_RUNTIME not in {
        "dub-worker-quality-v4.8",
        "dub-worker-quality-v4.9",
        "dub-worker-quality-v5.0",
        "dub-worker-quality-v6.8",
    }
    assert RELEASE_POLICY == "single-source-worker-release-identity-v1"
    assert PREFLIGHT_TRANSPORT_POLICY == "marked-preflight-json-transport-v1"
    assert dub_studio_runtime._WORKER_RUNTIME == WORKER_RUNTIME
    assert dub_worker.WORKER_RUNTIME == WORKER_RUNTIME


def test_supervisor_and_worker_entrypoint_use_one_release_identity() -> None:
    supervisor = (ROOT / "services" / "dub_studio_runtime.py").read_text(
        encoding="utf-8"
    )
    worker = (ROOT / "services" / "dub_worker.py").read_text(encoding="utf-8")
    entrypoint = (ROOT / "tools" / "voxcpm2" / "dub_worker.py").read_text(
        encoding="utf-8"
    )

    assert "from services.dub_worker_release import WORKER_RUNTIME" in supervisor
    assert "_WORKER_RUNTIME = WORKER_RUNTIME" in supervisor
    assert '"tools.voxcpm2.dub_worker"' in supervisor
    assert "from services.dub_worker_release import WORKER_RUNTIME" in worker
    assert "from services.dub_worker import main" in entrypoint
    for source in (supervisor, worker, entrypoint):
        assert "dub_worker_hardened" not in source
        assert "sys.modules[" not in source
        assert "install_hardening" not in source
