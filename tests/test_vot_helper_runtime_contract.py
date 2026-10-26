"""Contract tests for the Node half of LiveDub (`vot_helper/`).

Python CI cannot import an ES module, which is how `@vot.js/node` 3.x would
have shipped: `client.translateVideoCache` and friends moved to
`client.provider.*`, so the helper breaks at the first real translation while
every Python test stays green.

Two layers are checked here:

1. **Static policy** (always runs): the lockfile format `npm ci` can install,
   and a resolved `undici` that satisfies the security override in
   `package.json`. No network, no Node.
2. **Runtime smoke** (runs when the Node tree is installed): `smoke.mjs`
   derives the required API surface from `vot_live.mjs` and asserts the
   installed `@vot.js/node` provides it. CI runs this inside the dependency
   job, right after `npm ci`, so the skip below never hides it there.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "vot_helper"
PACKAGE = HELPER / "package.json"
LOCK = HELPER / "package-lock.json"
SMOKE = HELPER / "smoke.mjs"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _version_tuple(value: str) -> tuple[int, ...]:
    core = re.split(r"[-+]", value.strip(), maxsplit=1)[0]
    return tuple(int(part) for part in core.split("."))


def _floor_from_override(spec: str) -> tuple[int, ...]:
    match = re.fullmatch(r"\^?(\d+(?:\.\d+)*)", spec.strip())
    assert match, f"cannot read a floor out of override {spec!r}"
    return tuple(int(part) for part in match.group(1).split("."))


def test_lockfile_is_installable_with_npm_ci() -> None:
    lock = _json(LOCK)
    assert lock.get("lockfileVersion") == 3, "npm ci needs lockfileVersion 3"
    assert lock["packages"][""]["dependencies"], "root package must declare deps"


def test_undici_override_is_declared_and_resolved() -> None:
    """Transitive `undici` carries the vulnerabilities; the floor must hold.

    `@vot.js/node` imports `undici` in `client.js` -> `utils/fetchAgent.js`, so
    it sits in the hot path of every LiveDub request, next to the OAuth token.
    """
    package = _json(PACKAGE)
    overrides = package.get("overrides") or {}
    assert "undici" in overrides, (
        "package.json must keep an undici override: the transitive 7.x range "
        "resolves to versions with known high-severity advisories"
    )

    floor = _floor_from_override(overrides["undici"])
    resolved = [
        meta["version"]
        for path, meta in _json(LOCK)["packages"].items()
        if path == "node_modules/undici" or path.endswith("/node_modules/undici")
    ]
    assert resolved, "the lock must contain a resolved undici"
    for version in resolved:
        assert _version_tuple(version) >= floor, (
            f"resolved undici {version} is below the declared floor {overrides['undici']}"
        )


def test_helper_dependencies_stay_on_the_reviewed_major() -> None:
    """A `@vot.js/node` major is a code change, not an install detail.

    The helper is written against the 2.x client API. CI is *supposed* to go red
    on a 3.x PR — that is the signal to port `vot_live.mjs` first — and
    ``smoke.mjs`` is what turns it red for the right reason.
    """
    dependencies = _json(PACKAGE)["dependencies"]
    spec = dependencies["@vot.js/node"]
    major = _floor_from_override(spec.split(",")[0].replace("^", ""))[0]
    assert major == 2, (
        "@vot.js/node moved provider methods to client.provider in 3.x; "
        "port vot_live.mjs and smoke.mjs before raising this major"
    )


def test_smoke_script_exists_and_is_wired_into_ci() -> None:
    assert SMOKE.is_file()
    source = SMOKE.read_text(encoding="utf-8")
    # The surface must be derived from the helper, not maintained by hand.
    assert "vot_live.mjs" in source
    assert "requiredClientMethods" in source

    audit_workflow = (ROOT / ".github" / "workflows" / "dependency-audit.yml").read_text(encoding="utf-8")
    assert "node smoke.mjs" in audit_workflow, "CI must run the smoke script"
    assert "npm audit --audit-level=high" in audit_workflow
    assert "npm ci --no-audit --no-fund" in audit_workflow

    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "uses: ./.github/workflows/dependency-audit.yml" in ci, (
        "the PR pipeline must call the same reusable audit workflow as the schedule"
    )


@pytest.mark.skipif(
    shutil.which("node") is None or not (HELPER / "node_modules").is_dir(),
    reason="needs node and an installed vot_helper/node_modules (npm ci)",
)
def test_installed_tree_satisfies_the_helper_api() -> None:
    completed = subprocess.run(
        ["node", "smoke.mjs"],
        cwd=HELPER,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, f"smoke.mjs failed:\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    assert "runtime contract is satisfied" in completed.stdout


def _helper_dir(tmp_path: Path, *, with_lock: bool) -> Path:
    helper = tmp_path / "vot_helper"
    helper.mkdir()
    (helper / "vot_live.mjs").write_text("// stub\n", encoding="utf-8")
    (helper / "package.json").write_text('{"dependencies": {}}', encoding="utf-8")
    if with_lock:
        (helper / "package-lock.json").write_text('{"lockfileVersion": 3}', encoding="utf-8")
    return helper


def _install_dependency(helper: Path) -> None:
    module = helper / "node_modules" / "@vot.js" / "node"
    module.mkdir(parents=True)
    (module / "package.json").write_text('{"version": "2.4.12"}', encoding="utf-8")


@pytest.mark.parametrize(
    "failure",
    [FileNotFoundError("missing"), subprocess.TimeoutExpired("vot", 3)],
)
def test_run_subprocess_chains_process_failures(monkeypatch, failure) -> None:
    from services import yandex_live_dub

    def fake_run(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(yandex_live_dub.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError) as caught:
        yandex_live_dub._run_subprocess(["vot"], timeout=3)
    assert caught.value.__cause__ is failure


@pytest.mark.asyncio
async def test_live_dub_info_chains_unrecoverable_json_error(monkeypatch) -> None:
    from services import yandex_live_dub

    monkeypatch.setattr(yandex_live_dub, "_check_vot_cli", lambda: "vot")
    monkeypatch.setattr(yandex_live_dub, "_run_subprocess", lambda *_args, **_kwargs: ("not json", "", 0))
    with pytest.raises(RuntimeError, match="Не удалось распарсить JSON") as caught:
        await yandex_live_dub.get_live_dub_info("https://example.invalid/video")
    assert isinstance(caught.value.__cause__, json.JSONDecodeError)


def test_production_install_uses_npm_ci_and_needs_the_lock(monkeypatch, tmp_path) -> None:
    """`npm install` re-resolves versions; production must replay the lock."""
    from services import yandex_live_dub

    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(list(command))
        _install_dependency(helper)
        return 0, "", ""

    monkeypatch.setattr(yandex_live_dub, "_find_node", lambda: "/usr/bin/node")
    monkeypatch.setattr(yandex_live_dub.shutil, "which", lambda name: "/usr/bin/npm")
    monkeypatch.setattr(yandex_live_dub, "_run_subprocess", fake_run)

    helper = _helper_dir(tmp_path, with_lock=True)
    monkeypatch.setattr(yandex_live_dub, "VOT_HELPER_DIR", helper)

    command = yandex_live_dub._ensure_vot_helper()

    assert command == ["/usr/bin/node", str(helper / "vot_live.mjs")]
    assert calls and calls[0][1] == "ci", f"expected npm ci, got {calls}"
    assert "install" not in calls[0]


def test_missing_lock_disables_the_new_protocol_instead_of_resolving_fresh(monkeypatch, tmp_path) -> None:
    from services import yandex_live_dub

    calls: list[list[str]] = []
    monkeypatch.setattr(yandex_live_dub, "_find_node", lambda: "/usr/bin/node")
    monkeypatch.setattr(yandex_live_dub.shutil, "which", lambda name: "/usr/bin/npm")
    monkeypatch.setattr(
        yandex_live_dub,
        "_run_subprocess",
        lambda command, **kwargs: calls.append(list(command)) or (0, "", ""),
    )

    helper = _helper_dir(tmp_path, with_lock=False)
    monkeypatch.setattr(yandex_live_dub, "VOT_HELPER_DIR", helper)

    assert yandex_live_dub._ensure_vot_helper() is None
    assert calls == [], "npm must not run without a lock to pin it"
