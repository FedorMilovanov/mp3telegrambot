from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from services import dub_title_policy
from services.dub_worker_release import (
    INDEPENDENT_QA_RECOVERY_POLICY,
    WORKER_RUNTIME,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_release_files(tmp_path: Path) -> None:
    _write(
        tmp_path / "services" / "dub_worker_release.py",
        "\n".join(
            (
                f'WORKER_RUNTIME = "{WORKER_RUNTIME}"',
                'RELEASE_POLICY = "single-source-worker-release-identity-v1"',
                'PREFLIGHT_TRANSPORT_POLICY = "marked-preflight-json-transport-v1"',
                f'INDEPENDENT_QA_RECOVERY_POLICY = "{INDEPENDENT_QA_RECOVERY_POLICY}"',
            )
        ),
    )
    _write(
        tmp_path
        / "tools"
        / "voxcpm2"
        / "dub_worker_hardened"
        / "__main__.py",
        "\n".join(
            (
                "from services.dub_worker_release import WORKER_RUNTIME",
                "def activate_release_identity():",
                "    package._RUNTIME_VERSION = WORKER_RUNTIME",
                "    _legacy._RUNTIME_VERSION = WORKER_RUNTIME",
                "activate_release_identity()",
                "install_preflight_json()",
                "main()",
            )
        ),
    )
    _write(
        tmp_path / "services" / "dub_studio_runtime" / "__init__.py",
        "\n".join(
            (
                "from services.dub_worker_release import WORKER_RUNTIME",
                "_WORKER_RUNTIME = WORKER_RUNTIME",
                "_legacy._WORKER_RUNTIME = _WORKER_RUNTIME",
                "class _WriteThroughModule:",
                "    pass",
                "_module.__class__ = _WriteThroughModule",
            )
        ),
    )
    _write(
        tmp_path
        / "tools"
        / "voxcpm2"
        / "generic_clean_direct_runtime"
        / "__main__.py",
        "\n".join(
            (
                "from tools.voxcpm2 import independent_qa_retry",
                "independent_qa_retry.install()",
                "main()",
            )
        ),
    )
    _write(
        tmp_path / "tools" / "voxcpm2" / "independent_qa_retry.py",
        "\n".join(
            (
                f'POLICY = "{INDEPENDENT_QA_RECOVERY_POLICY}"',
                "MAX_RECOVERY_CYCLES = 3",
                "INTERNAL_SEED_ROUNDS_PER_CALL = 2",
                "def _retry_context():",
                "    pass",
                "def _retarget_checkpoints():",
                "    failed_ids=failed_ids",
                '    next_request["base_seed"] = next_base_seed',
                "def install():",
                "    pass",
            )
        ),
    )


def test_release_static_contract_replaces_only_superseded_v48_checks(
    tmp_path: Path,
) -> None:
    health = SimpleNamespace(
        _v47_static_contract=lambda repo: (
            False,
            "v4.8-контракты не прошли: worker-package-cancel-root, worker-runtime-sync",
        )
    )
    _write_release_files(tmp_path)

    ok, detail = dub_title_policy._release_static_contract(health, tmp_path)

    assert ok is True
    assert "worker v5.0" in detail
    assert "marked noise-tolerant JSON transport" in detail
    assert "segment-only independent QA recovery" in detail


def test_release_static_contract_keeps_unrelated_failure_red(tmp_path: Path) -> None:
    health = SimpleNamespace(
        _v47_static_contract=lambda repo: (
            False,
            "v4.8-контракты не прошли: child-python-contract, worker-runtime-sync",
        )
    )

    ok, detail = dub_title_policy._release_static_contract(health, tmp_path)

    assert ok is False
    assert "child-python-contract" in detail
    assert "worker-current-release" in detail


def test_shared_worker_runtime_is_v50() -> None:
    assert WORKER_RUNTIME == "dub-worker-quality-v5.0"
    assert INDEPENDENT_QA_RECOVERY_POLICY == "bounded-independent-qa-segment-retry-v1"
