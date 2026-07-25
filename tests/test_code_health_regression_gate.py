from __future__ import annotations

import json
from pathlib import Path

from tools import check_code_health as cli


def _write_baseline(path: Path, *, regex: int, post: int) -> None:
    path.write_text(
        json.dumps(
            {
                "files_scanned": 1,
                "total_regex_markers": regex,
                "total_postprocess_markers": post,
            }
        ),
        encoding="utf-8",
    )


def test_regression_gate_accepts_equal_snapshot(tmp_path, monkeypatch):
    core = tmp_path / "core"
    core.mkdir()
    (core / "sample.py").write_text("import re\nre.search('x', 'x')\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, regex=1, post=0)
    monkeypatch.delenv("CODE_HEALTH_MAX_REGEX_DELTA", raising=False)
    monkeypatch.delenv("CODE_HEALTH_MAX_POSTPROCESS_DELTA", raising=False)

    assert cli._regression_exit_code(tmp_path, baseline) == 0


def test_regression_gate_rejects_positive_marker_growth(tmp_path, monkeypatch):
    core = tmp_path / "core"
    core.mkdir()
    (core / "sample.py").write_text(
        "import re\nre.search('x', 'x')\nre.sub('x', 'y', 'x')\n",
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, regex=1, post=0)
    monkeypatch.delenv("CODE_HEALTH_MAX_REGEX_DELTA", raising=False)

    assert cli._regression_exit_code(tmp_path, baseline) == 1


def test_regression_gate_supports_reviewed_temporary_allowance(tmp_path, monkeypatch):
    core = tmp_path / "core"
    core.mkdir()
    (core / "sample.py").write_text(
        "import re\nre.search('x', 'x')\nre.sub('x', 'y', 'x')\n",
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline.json"
    _write_baseline(baseline, regex=1, post=0)
    monkeypatch.setenv("CODE_HEALTH_MAX_REGEX_DELTA", "1")

    assert cli._regression_exit_code(tmp_path, baseline) == 0


def test_regression_gate_fails_closed_when_baseline_missing(tmp_path):
    assert cli._regression_exit_code(tmp_path, tmp_path / "missing.json") == 2
