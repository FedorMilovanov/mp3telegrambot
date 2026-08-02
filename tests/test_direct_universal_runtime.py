from __future__ import annotations

from tools.voxcpm2 import direct_universal_runtime as runtime


def test_worker_ignores_plain_and_ansi_model_tqdm() -> None:
    namespace = {
        "_progress_from_line_v44": lambda line, current: (79, "synthesis"),
    }
    runtime.install_worker_progress(namespace)
    parser = namespace["_progress_from_line_v44"]
    assert parser("79%|████| 68/86 [00:10<00:02, 1.0s/it]", 31) == (31, "")
    assert parser("\x1b[32m79%|████| 68/86 [00:10<00:02, 1.0s/it]", 31) == (31, "")
    assert parser("DUB_PROGRESS {\"progress\": 44}", 31) == (79, "synthesis")


def test_runtime_fingerprint_contains_universal_and_active_modules() -> None:
    namespace = {"_RENDER_MODULES": ("existing.py",)}
    runtime.install_runtime_fingerprint(namespace)
    modules = namespace["_RENDER_MODULES"]
    assert "tools/voxcpm2/direct_timing_guard.py" in modules
    assert "tools/voxcpm2/direct_max_quality_cli/__init__.py" in modules
    assert "services/speech_backends/voxcpm2.py" in modules
    assert len(modules) == len(set(modules))


def test_progress_is_monotonic_by_segment_and_attempt() -> None:
    values = [
        runtime._progress_value(position=1, total=5, attempt=1, max_attempts=3),
        runtime._progress_value(position=1, total=5, attempt=2, max_attempts=3),
        runtime._progress_value(position=2, total=5, attempt=1, max_attempts=3),
    ]
    assert values == sorted(values)
