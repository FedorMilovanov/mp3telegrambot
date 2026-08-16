#!/usr/bin/env python3
"""Replace legacy source-string Dub health with canonical source-owner checks."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "services" / "dub_release_health_v64.py"

SOURCE = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical-source release health for the production Dub stack.

This check validates durable production owners and release policies. It never
requires legacy package shadows, importlib loaders, monkey-patch assignments or
test-source strings to exist. Repo-wide no-surgery enforcement remains a separate
CI gate; this module answers whether the Dub production contract is structurally
present in the files Python actually imports.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from services.dub_worker_release import (
    BACKEND_COMMAND_POLICY,
    BACKEND_ENVIRONMENT_POLICY,
    MASTER_MIX_POLICY,
    PRODUCTION_CAPABILITY_POLICY,
    REFERENCE_POLICY,
    REFERENCE_SELECTION_POLICY,
    SEMANTIC_BLOCK_POLICY,
    SOURCE_BED_POLICY,
    SOURCE_PROSODY_ROLE_POLICY,
    TAIL_BRACKETING_POLICY,
    WORKER_RUNTIME,
)

POLICY = "canonical-source-dub-release-health-v5"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _all(text: str, *markers: str) -> bool:
    return bool(text) and all(marker in text for marker in markers)


def _clean_source(text: str, *forbidden: str) -> bool:
    return bool(text) and all(marker not in text for marker in forbidden)


def _no_shadow(root: Path, relative_stem: str) -> bool:
    canonical = root / f"{relative_stem}.py"
    package_init = root / relative_stem / "__init__.py"
    return canonical.is_file() and not package_init.exists()


def _v68_quality_contract(repo: Path) -> tuple[bool, str]:
    root = Path(repo).resolve()
    voxcpm = root / "tools" / "voxcpm2"

    worker = _read(root / "services" / "dub_worker.py")
    preflight = _read(voxcpm / "dub_job_preflight.py")
    runtime_contract = _read(voxcpm / "clean_runtime_contract.py")
    source_bed = _read(voxcpm / "spatial_bed_contract.py")
    master = _read(voxcpm / "master_monolithic_mix.py")
    final_qa = _read(voxcpm / "final_media_qa.py")
    reference = _read(voxcpm / "continuous_reference_policy.py")
    tail = _read(voxcpm / "direct_tail_artifact.py")
    backend_base = _read(root / "services" / "speech_backends" / "base.py")
    backend_vox = _read(root / "services" / "speech_backends" / "voxcpm2.py")
    project_runtime = _read(voxcpm / "generic_project_runtime.py")
    semantic_blocks = _read(voxcpm / "semantic_block_runtime.py")
    direct_cli = _read(voxcpm / "direct_max_quality_cli.py")
    source_policy = _read(voxcpm / "source_prosody_policy.py")
    wizard = _read(root / "handlers" / "dub_wizard.py")
    title_policy = _read(root / "core" / "media_title_policy.py")

    shadow_stems = (
        "tools/voxcpm2/clean_production_core",
        "tools/voxcpm2/clean_runtime_contract",
        "tools/voxcpm2/clean_source_download",
        "tools/voxcpm2/continuous_reference_policy",
        "tools/voxcpm2/direct_max_quality_analysis",
        "tools/voxcpm2/direct_max_quality_cli",
        "tools/voxcpm2/direct_max_quality_render",
        "tools/voxcpm2/direct_monolith_contract",
        "tools/voxcpm2/direct_russian_cadence",
        "tools/voxcpm2/direct_source_prosody",
        "tools/voxcpm2/direct_tail_artifact",
        "tools/voxcpm2/direct_timeline_delivery_qa",
        "tools/voxcpm2/dub_job_preflight",
        "tools/voxcpm2/dub_quality_v4",
        "tools/voxcpm2/expressive_continuity",
        "tools/voxcpm2/final_media_qa",
        "tools/voxcpm2/final_media_spatial_bed",
        "tools/voxcpm2/generic_clean_audio_repair_runtime",
        "tools/voxcpm2/generic_clean_direct_runtime",
        "tools/voxcpm2/generic_project_runtime",
        "tools/voxcpm2/professional_audio_qa_v45",
        "handlers/dub_audio_repair",
        "handlers/dub_health",
        "handlers/dub_wizard",
    )

    checks: dict[str, bool] = {
        "source-owned-worker": _all(
            worker,
            "class WorkerDubStore(DubStore):",
            "def execute_job(",
            "studio=store.root",
            "WORKER_RUNTIME",
        ) and _clean_source(
            worker,
            "sys.modules[__name__].__class__",
            "install_hardening()",
            "exec(compile(",
        ),
        "source-owned-preflight": _all(
            preflight,
            'PREFLIGHT_JSON_TRANSPORT_POLICY = "marked-preflight-json-transport-v2"',
            'PREFLIGHT_RUNTIME_PATH_POLICY = "backend-owned-preflight-runtime-paths-v1"',
            "def _decode_probe_payload(",
            "def _runtime_paths(",
            "get_backend(",
        ) and _clean_source(
            preflight,
            "spec_from_file_location",
            "module_from_spec",
            "sys.modules",
            "_legacy.",
        ),
        "source-owned-runtime-contract": _all(
            runtime_contract,
            'POLICY = "clean-runtime-contract-v2"',
            "def sampled_sha256_file(",
            "def build_fingerprints(",
            "BACKEND_SELECTION_POLICY",
            "backend.discover_model",
        ) and _clean_source(
            runtime_contract,
            "_clean_runtime_contract_base",
            "install_runtime_fingerprint",
            "exec(compile(",
            "spec_from_file_location",
        ) and not (voxcpm / "_clean_runtime_contract_base.py").exists(),
        "russian-only-source-bed": _all(
            source_bed,
            f'POLICY = "{MASTER_MIX_POLICY}"',
            f'SOURCE_BED_POLICY = "{SOURCE_BED_POLICY}"',
            "CENTER_FLOOR_RATIO = 0.0",
            "SIDE_BED_RATIO = 0.0",
            '"source_bed_applied": False',
        ) and _all(
            master,
            "def build_dialogue_suppressed_mix(",
            "source is audit input, never a mix stem",
            "source_bed_applied=False",
        ),
        "post-aac-final-qa": _all(
            final_qa,
            "def estimate_spatial_bed(",
            "estimated_center_level",
            "estimated_side_level",
        ),
        "typical-continuous-reference": _all(
            reference,
            f'POLICY = "{REFERENCE_POLICY}"',
            f'SELECTION_POLICY = "{REFERENCE_SELECTION_POLICY}"',
            "def _candidate_windows(",
            "robust_median = float(np.median(median_values))",
        ) and _clean_source(reference, "spec_from_file_location", "module_from_spec", "_legacy."),
        "model-independent-backend": _all(
            backend_base,
            f'BACKEND_COMMAND_POLICY = "{BACKEND_COMMAND_POLICY}"',
            f'BACKEND_ENVIRONMENT_POLICY = "{BACKEND_ENVIRONMENT_POLICY}"',
            f'PRODUCTION_CAPABILITY_POLICY = "{PRODUCTION_CAPABILITY_POLICY}"',
            "class BackendSynthesisSession(Protocol):",
            "def build_renderer_command(",
            "def build_master_command(",
            "def process_environment(",
        ) and _all(
            backend_vox,
            "class VoxCPM2Session:",
            "def build_renderer_command(",
            "def build_master_command(",
            "def process_environment(",
            "def open_session(",
        ),
        "request-backend-boundary": _all(
            project_runtime,
            "DEFAULT_BACKEND_ID",
            "get_backend(",
            "speech_backend",
        ) and _all(
            wizard,
            '"speech_backend": choice.backend_id',
            "def _request_payload(",
        ),
        "semantic-block-direct": _all(
            semantic_blocks,
            f'POLICY = "{SEMANTIC_BLOCK_POLICY}"',
            "MIN_BLOCK_SECONDS = 7.0",
            "TARGET_BLOCK_SECONDS = 10.5",
            "MAX_BLOCK_SECONDS = 15.0",
            "def group_ready_srt(",
        ) and _all(
            direct_cli,
            "backend.open_session(",
            "session.generate(",
            "set_continuation_context",
        ),
        "source-prosody-diagnostic-only": _all(
            source_policy,
            f'POLICY = "{SOURCE_PROSODY_ROLE_POLICY}"',
            "def ranking_view(",
            "def mark_diagnostic_only(",
        ) and _all(
            direct_cli,
            "source_prosody_policy.ranking_view(display_segment)",
            "source_prosody_policy.mark_diagnostic_only(item)",
        ),
        "tail-bracketing": _all(
            tail,
            f'BRACKETING_POLICY = "{TAIL_BRACKETING_POLICY}"',
            "FRAME_OVERLAP_TOLERANCE = 2",
            "def _bracketing_voice_runs(",
        ),
        "canonical-title-policy": _all(
            title_policy,
            "def canonical_media_title(",
        ),
        "no-known-shadow-packages": all(_no_shadow(root, stem) for stem in shadow_stems),
    }

    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        return False, "Dub canonical-source health не прошёл: " + ", ".join(failed)
    return True, (
        f"Canonical Dub owners healthy: worker={WORKER_RUNTIME}; "
        f"semantic blocks={SEMANTIC_BLOCK_POLICY}; backend={BACKEND_COMMAND_POLICY}; "
        f"environment={BACKEND_ENVIRONMENT_POLICY}; capabilities={PRODUCTION_CAPABILITY_POLICY}; "
        f"source bed={SOURCE_BED_POLICY}; source prosody={SOURCE_PROSODY_ROLE_POLICY}; "
        f"tail={TAIL_BRACKETING_POLICY}; health={POLICY}"
    )


__all__ = ["POLICY", "WORKER_RUNTIME", "_v68_quality_contract"]
'''


def main() -> int:
    current = TARGET.read_text(encoding="utf-8")
    # Compatibility aliases were historical-only. Prove no production Python file
    # outside this module references them before removing them.
    aliases = (
        "_v67_quality_contract",
        "_v66_quality_contract",
        "_v65_quality_contract",
        "_russian_only_master_contract",
    )
    blockers: list[str] = []
    for path in ROOT.rglob("*.py"):
        if path == TARGET or "tests" in path.parts or ".git" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in aliases:
            if name in text:
                blockers.append(f"{path.relative_to(ROOT).as_posix()}:{name}")
    if blockers:
        raise RuntimeError("legacy health aliases still have production refs: " + ", ".join(blockers))
    TARGET.write_text(SOURCE, encoding="utf-8")
    print("rewrote Dub release health to canonical-source contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
