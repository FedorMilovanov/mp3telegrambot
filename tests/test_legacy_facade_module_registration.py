from __future__ import annotations

import dataclasses
from pathlib import Path
import subprocess
import sys

from tools.voxcpm2 import dub_quality_v4


REPO = Path(__file__).resolve().parents[1]


def _run_fresh(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def test_dub_quality_legacy_module_is_registered_for_dataclasses() -> None:
    legacy = dub_quality_v4._legacy

    assert sys.modules.get(legacy.__name__) is legacy
    cue = legacy._CuePart(0.0, 1.0, "текст")
    assert dataclasses.is_dataclass(cue)
    assert sys.modules.get(type(cue).__module__) is legacy


def test_dub_quality_import_succeeds_in_fresh_python_process() -> None:
    script = (
        "import dataclasses, sys; "
        "from tools.voxcpm2 import dub_quality_v4 as q; "
        "m=q._legacy; c=m._CuePart(0.0,1.0,'x'); "
        "assert sys.modules[m.__name__] is m; "
        "assert dataclasses.is_dataclass(c); "
        "print(m.__name__)"
    )
    process = _run_fresh(script)

    assert process.returncode == 0, process.stderr or process.stdout
    assert "tools.voxcpm2._dub_quality_v4_legacy" in process.stdout


def test_original_failed_import_chains_succeed_in_fresh_python_process() -> None:
    script = (
        "from tools.voxcpm2 import continuous_reference_policy; "
        "from tools.voxcpm2 import expressive_continuity; "
        "from tools.voxcpm2 import dub_quality_v4; "
        "assert continuous_reference_policy.POLICY; "
        "assert expressive_continuity.POLICY; "
        "assert dub_quality_v4.POLICY; "
        "print('fresh-import-chain-ok')"
    )
    process = _run_fresh(script)

    assert process.returncode == 0, process.stderr or process.stdout
    assert "fresh-import-chain-ok" in process.stdout


def test_registration_is_transactional_in_source() -> None:
    source = (REPO / "tools" / "voxcpm2" / "dub_quality_v4" / "__init__.py").read_text(
        encoding="utf-8"
    )

    register_at = source.index("sys.modules[_SPEC.name] = _legacy")
    execute_at = source.index("_SPEC.loader.exec_module(_legacy)")
    restore_at = source.index("sys.modules[_SPEC.name] = _previous_legacy")
    assert register_at < execute_at < restore_at
    assert "sys.modules.pop(_SPEC.name, None)" in source


def test_every_package_over_dataclass_file_registers_before_execution() -> None:
    failures: list[str] = []
    for facade in REPO.rglob("__init__.py"):
        legacy_path = facade.parent.parent / f"{facade.parent.name}.py"
        if not legacy_path.is_file():
            continue
        legacy_source = legacy_path.read_text(encoding="utf-8")
        if "@dataclass" not in legacy_source:
            continue
        facade_source = facade.read_text(encoding="utf-8")
        if "module_from_spec(_SPEC)" not in facade_source:
            continue
        try:
            register_at = facade_source.index("sys.modules[_SPEC.name] = _legacy")
            execute_at = facade_source.index("_SPEC.loader.exec_module(_legacy)")
        except ValueError:
            failures.append(str(facade.relative_to(REPO)))
            continue
        if register_at >= execute_at:
            failures.append(str(facade.relative_to(REPO)))

    assert not failures, "Unsafe dataclass facades: " + ", ".join(sorted(failures))
