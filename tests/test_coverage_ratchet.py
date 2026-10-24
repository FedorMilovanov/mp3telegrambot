#!/usr/bin/env python3
"""Ратчет покрытия: контракт инструмента и его место в CI.

Ратчет появился не «для галочки»: он закрывает дыру, которую оставляют два
других гейта. Общий `fail_under` — среднее по проекту, падение в одном файле
в нём не видно. `diff-cover` в разрезаемом файле считает проводку к уже
покрытым модулям, и построчная метрика там шумит. Поэтому
`pipelines/main_pipeline.py` исключён из `diff-cover`, а его покрытие держит
ратчет — и здесь проверяется, что обе половины этой замены на месте.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.check_coverage_ratchet import (
    BASELINE,
    TRACKED,
    TOLERANCE_PP,
    check,
    load_baseline,
    measure,
    render_baseline,
)

ROOT = Path(__file__).resolve().parents[1]


def _coverage_xml(tmp_path: Path, files: dict[str, tuple[int, int]], source: str = "pipelines") -> Path:
    """Минимальный Cobertura-отчёт: files = {имя: (выполнено, всего)}."""
    classes = []
    for name, (hit, total) in files.items():
        lines = "".join(f'<line number="{index}" hits="{1 if index < hit else 0}"/>' for index in range(total))
        classes.append(f'<class name="{name}" filename="{name}"><lines>{lines}</lines></class>')
    xml = (
        '<?xml version="1.0"?>'
        f"<coverage><sources><source>/{source}</source></sources>"
        f'<packages><package name="."><classes>{"".join(classes)}</classes></package></packages>'
        "</coverage>"
    )
    path = tmp_path / "coverage.xml"
    path.write_text(xml, encoding="utf-8")
    return path


def test_measure_resolves_paths_through_coverage_sources(tmp_path):
    coverage = _coverage_xml(tmp_path, {"main_pipeline.py": (3, 4)})
    measured = measure(coverage, root=ROOT)
    assert measured["pipelines/main_pipeline.py"] == 75.0


def test_measure_keeps_file_when_no_source_matches(tmp_path):
    coverage = _coverage_xml(tmp_path, {"nowhere.py": (1, 2)}, source="unknown_pkg")
    assert measure(coverage, root=ROOT) == {"nowhere.py": 50.0}


def test_check_flags_a_drop_beyond_tolerance():
    violations = check(
        {"pipelines/main_pipeline.py": 10.0},
        {"pipelines/main_pipeline.py": 18.0},
        tracked=("pipelines/main_pipeline.py",),
    )
    assert [v.path for v in violations] == ["pipelines/main_pipeline.py"]


def test_check_allows_noise_within_tolerance():
    baseline = {"pipelines/main_pipeline.py": 18.0}
    inside = 18.0 - TOLERANCE_PP
    assert check({"pipelines/main_pipeline.py": inside}, baseline, tracked=("pipelines/main_pipeline.py",)) == []


def test_check_blocks_growth_expectations_only_downwards():
    """Рост покрытия — всегда ок, и это не «нарушение базы»."""
    violations = check(
        {"pipelines/main_pipeline.py": 40.0},
        {"pipelines/main_pipeline.py": 18.0},
        tracked=("pipelines/main_pipeline.py",),
    )
    assert violations == []


def test_missing_file_in_report_is_a_violation():
    """Файл нельзя спрятать от ратчета, сломав сбор покрытия."""
    violations = check(
        {},
        {"pipelines/main_pipeline.py": 18.0},
        tracked=("pipelines/main_pipeline.py",),
    )
    assert [v.path for v in violations] == ["pipelines/main_pipeline.py"]


def test_render_baseline_is_stable_json_with_tracked_files_only():
    rendered = render_baseline({"pipelines/main_pipeline.py": 18.71, "core/utils.py": 90.0})
    payload = json.loads(rendered)
    assert payload["files"] == {"pipelines/main_pipeline.py": 18.71}
    assert payload["tolerance_pp"] == TOLERANCE_PP
    assert rendered == render_baseline({"pipelines/main_pipeline.py": 18.71, "core/utils.py": 90.0})


def test_committed_baseline_tracks_every_declared_file():
    baseline = load_baseline(ROOT / BASELINE)
    missing = [path for path in TRACKED if path not in baseline]
    assert not missing, f"нет базовой линии для {missing}: обновите {BASELINE}"


def test_ci_excludes_exactly_the_ratcheted_file_from_diff_cover():
    """Замена половин должна совпадать: исключение в diff-cover == список ратчета."""
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "check_coverage_ratchet.py" in ci, "ратчет обязан запускаться в CI"
    for path in TRACKED:
        assert Path(path).name in ci, f"{path} не упомянут в комментарии diff-cover"
    assert '--exclude "*/main_pipeline.py"' in ci
