from __future__ import annotations

from tools.voxcpm2 import direct_universal_runtime as runtime


def test_model_tqdm_detector_accepts_plain_and_ansi_lines() -> None:
    assert runtime._MODEL_TQDM_RE.match(
        "79%|████| 68/86 [00:10<00:02, 1.0s/it]"
    )
    assert runtime._MODEL_TQDM_RE.match(
        "\x1b[32m79%|████| 68/86 [00:10<00:02, 1.0s/it]"
    )
    assert not runtime._MODEL_TQDM_RE.match(
        'DUB_PROGRESS {"progress": 44}'
    )


def test_progress_is_monotonic_by_segment_and_attempt() -> None:
    values = [
        runtime._progress_value(position=1, total=5, attempt=1, max_attempts=3),
        runtime._progress_value(position=1, total=5, attempt=2, max_attempts=3),
        runtime._progress_value(position=2, total=5, attempt=1, max_attempts=3),
    ]
    assert values == sorted(values)
