from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


BASE_STUB = r'''
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
POLICY = "base"
SEED_EPOCH_STRIDE = 1000000000000
MAX_SEGMENT_ID = 1000000000
MAX_RETRY_EPOCH = 100000

def _now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def _strict_segment_id(v):
    v=int(v)
    if v < 1: raise RuntimeError("bad id")
    return v
def retry_epoch_path(work_dir, segment_id):
    return Path(work_dir).resolve()/"retry_epochs"/f"segment_{int(segment_id):02d}.json"
def _read_payload(path):
    if not path.is_file(): return {}
    value=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value,dict): raise RuntimeError("bad payload")
    return value
def load_retry_epoch(work_dir, segment_id):
    return int(_read_payload(retry_epoch_path(work_dir, segment_id)).get("epoch",0))
def seed_for_attempt(base_seed, segment_id, attempt, epoch):
    return int(base_seed)+int(segment_id)*100+int(attempt)+int(epoch)*SEED_EPOCH_STRIDE
def _atomic_write(path,payload):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(payload),encoding="utf-8")
def advance_retry_epoch(work_dir,segment_id,*,reason,evidence=None):
    raise AssertionError("wrapper must replace base advance")
def invalidate_segment_for_retry(work_dir,segment,*,reason,fitted_path=None,evidence=None):
    return advance_retry_epoch(work_dir,segment["id"],reason=reason,evidence=evidence)
__all__=[]
'''


def load_wrapper(tmp_path: Path):
    package = tmp_path / "tools" / "voxcpm2"
    package.mkdir(parents=True)
    (tmp_path / "tools" / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    source = Path(__file__).resolve().parents[1] / "tools" / "voxcpm2" / "direct_retry_epoch.py"
    (package / "direct_retry_epoch.py").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (package / "_direct_retry_epoch_base.py").write_text(BASE_STUB, encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    name = "tools.voxcpm2.direct_retry_epoch"
    previous = sys.modules.get(name)
    try:
        spec = importlib.util.spec_from_file_location(
            name,
            package / "direct_retry_epoch.py",
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(tmp_path))
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def test_new_text_scope_starts_from_zero(tmp_path: Path) -> None:
    module = load_wrapper(tmp_path)
    work = tmp_path / "work"
    first = "a" * 64
    second = "b" * 64
    module.advance_retry_epoch(
        work, 1, reason="raw_candidate_hard_failure",
        evidence={"failure_scope_fingerprint": first},
    )
    assert module.load_retry_epoch(work, 1, scope_fingerprint=first) == 1
    assert module.load_retry_epoch(work, 1, scope_fingerprint=second) == 0
    assert module.load_retry_epoch(work, 1) == 1


def test_exact_scope_is_capped_after_three_failed_epochs(tmp_path: Path) -> None:
    module = load_wrapper(tmp_path)
    work = tmp_path / "work"
    scope = "c" * 64
    for expected in (1, 2, 3):
        module.advance_retry_epoch(
            work, 1, reason="raw_candidate_hard_failure",
            evidence={"failure_scope_fingerprint": scope},
        )
        assert module.load_retry_epoch(work, 1, scope_fingerprint=scope) == expected
    with pytest.raises(RuntimeError, match="исчерпан безопасный retry-бюджет"):
        module.advance_retry_epoch(
            work, 1, reason="raw_candidate_hard_failure",
            evidence={"failure_scope_fingerprint": scope},
        )
