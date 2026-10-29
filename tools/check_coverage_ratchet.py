#!/usr/bin/env python3
"""Ратчет покрытия по файлам: покрытие может расти, но не падать.

Зачем второй гейт поверх общего порога и `diff-cover`:

* общий порог (`fail_under = 52`) — среднее по проекту, поэтому падение
  покрытия в одном файле легко маскируется ростом в другом;
* `diff-cover` проверяет только изменённые строки. В файле, который режется на
  модули (`pipelines/main_pipeline.py`, P0-3), изменения — это в основном
  проводка к уже покрытым модулям, и построчная метрика там шумит.

Ратчет отвечает на другой вопрос: «покрытие этого файла не хуже, чем было?».
База хранится в репозитории (`docs/coverage_baseline.json`) и обновляется тем
же PR, который поднял покрытие. Понижать базу вручную нельзя — это видно в
диффе и требует объяснения.

    python tools/check_coverage_ratchet.py                 # проверка (CI)
    python tools/check_coverage_ratchet.py --update        # пересчитать базу
    python tools/check_coverage_ratchet.py --coverage coverage.xml
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path("docs/coverage_baseline.json")
COVERAGE_XML = Path("coverage.xml")

#: Файлы под ратчетом. Остальные держит общий порог и `diff-cover`: тотальный
#: per-file ратчет на легаси-коде дал бы сотню «нельзя трогать» строк вместо
#: одной честной цели. Список расширяется по мере разбора крупных файлов.
TRACKED: tuple[str, ...] = ("pipelines/main_pipeline.py",)

#: Допуск в процентных пунктах: покрытие, измеренное на разных наборах тестов
#: (локально / в CI), может отличаться на доли процента из-за пропущенных
#: тестов, требующих ffmpeg или node.
TOLERANCE_PP = 0.5


@dataclass(frozen=True)
class Violation:
    path: str
    measured: float
    required: float

    def __str__(self) -> str:
        return f"{self.path}: покрытие {self.measured:.2f}% < базы {self.required:.2f}%"


def _resolve(filename: str, source_roots: list[str], root: Path) -> str:
    """Собрать путь от корня репозитория.

    Cobertura пишет имя файла относительно одного из ``<source>``, а сами
    ``source`` в CI абсолютные (``/home/runner/work/.../core``). Поэтому
    кандидат — «хвост source + имя файла», и берётся тот, что реально
    существует в репозитории. Это устойчивее, чем разбор ``<package name>``,
    который coverage оставляет пустым (``.``) для плоской раскладки.
    """
    for source in source_roots:
        candidate = f"{source}/{filename}"
        if (root / candidate).is_file():
            return candidate
    return filename


def measure(coverage_xml: Path, root: Path = ROOT) -> dict[str, float]:
    """Покрытие по файлам из Cobertura-отчёта, ключ — путь от корня репозитория."""
    document = ET.parse(coverage_xml).getroot()
    source_roots = [
        Path((source.text or "").strip()).name for source in document.iter("source") if (source.text or "").strip()
    ]
    result: dict[str, float] = {}
    for cls in document.iter("class"):
        filename = cls.get("filename") or ""
        if not filename:
            continue
        lines = cls.find("lines")
        if lines is None:
            continue
        total = len(lines)
        if not total:
            continue
        hit = sum(1 for line in lines if int(line.get("hits") or 0) > 0)
        result[_resolve(filename, source_roots, root)] = round(100.0 * hit / total, 2)
    return result


def load_baseline(path: Path = BASELINE) -> dict[str, float]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    files = data.get("files") or {}
    return {str(key): float(value) for key, value in files.items()}


def check(
    measured: dict[str, float],
    baseline: dict[str, float],
    tracked: tuple[str, ...] = TRACKED,
    tolerance: float = TOLERANCE_PP,
) -> list[Violation]:
    """Расхождения с базой. Отсутствие файла в отчёте — тоже нарушение.

    Иначе файл можно «спрятать» от ратчета, убрав его из измерения
    (например, случайно сломав сбор покрытия).
    """
    violations: list[Violation] = []
    for path in tracked:
        required = baseline.get(path)
        if required is None:
            continue
        if path not in measured:
            violations.append(Violation(path=path, measured=0.0, required=required))
            continue
        if measured[path] + tolerance < required:
            violations.append(Violation(path=path, measured=measured[path], required=required))
    return violations


def render_baseline(measured: dict[str, float], tracked: tuple[str, ...] = TRACKED) -> str:
    files = {path: measured.get(path, 0.0) for path in tracked if path in measured}
    payload = {
        "_comment": (
            "Ратчет покрытия: значения могут только расти. "
            "Обновляется командой python tools/check_coverage_ratchet.py --update."
        ),
        "tolerance_pp": TOLERANCE_PP,
        "files": files,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--coverage", type=Path, default=COVERAGE_XML, help="путь к coverage.xml")
    parser.add_argument("--baseline", type=Path, default=BASELINE, help="путь к базе ратчета")
    parser.add_argument("--update", action="store_true", help="пересчитать базу по текущему отчёту")
    parser.add_argument("--root", type=Path, default=ROOT, help="корень репозитория")
    args = parser.parse_args(argv)

    coverage_path = args.root / args.coverage if not args.coverage.is_absolute() else args.coverage
    baseline_path = args.root / args.baseline if not args.baseline.is_absolute() else args.baseline

    if not coverage_path.exists():
        print(
            f"COVERAGE_RATCHET_FAILED: нет {coverage_path}. Сначала запустите pytest с --cov --cov-report=xml",
            file=sys.stderr,
        )
        return 2

    measured = measure(coverage_path)

    if args.update:
        baseline_path.write_text(render_baseline(measured), encoding="utf-8")
        print(f"COVERAGE_RATCHET_UPDATED: {baseline_path} — {len(TRACKED)} файл(ов)")
        return 0

    if not baseline_path.exists():
        print(f"COVERAGE_RATCHET_FAILED: нет базы {baseline_path}", file=sys.stderr)
        return 2

    violations = check(measured, load_baseline(baseline_path))
    if violations:
        for violation in violations:
            print(f"[FAIL] {violation}", file=sys.stderr)
        print(
            "Покрытие этих файлов не может падать. Либо добавьте тест на новую ветку, "
            "либо осознанно понизьте базу в том же PR и объясните причину.",
            file=sys.stderr,
        )
        return 1

    summary = ", ".join(f"{path}={measured[path]:.2f}%" for path in TRACKED if path in measured)
    print(f"COVERAGE_RATCHET_OK: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
