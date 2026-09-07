import runpy
from pathlib import Path

import pytest

import bot
from services import runtime_dependency_lock as runtime_lock


def test_compat_launcher_executes_bot_new_as_main(monkeypatch):
    calls: list[tuple[str, str | None]] = []

    monkeypatch.setattr(bot, "_print_banner", lambda: None)
    monkeypatch.setattr(
        runpy,
        "run_module",
        lambda module_name, *, run_name=None: calls.append((module_name, run_name)),
    )

    bot.main()

    assert calls == [("bot_new", "__main__")]


def test_windows_launcher_requires_repository_lock_fail_closed():
    source = Path("Start Bot.bat").read_text(encoding="utf-8")
    checker_call = '"%VENV_PYTHON%" tools\\check_requirements_lock.py requirements.txt'
    marker_gate = 'if /I not "!CURRENT_REQ_HASH!"=="!SAVED_REQ_HASH!" ('
    locked_install = '"%VENV_PYTHON%" -m pip install -r "%REQUIREMENTS_FILE%"'

    assert 'set "REQUIREMENTS_FILE=requirements-lock.txt"' in source
    assert (
        'if not exist "%REQUIREMENTS_FILE%" set "REQUIREMENTS_FILE=requirements.txt"'
        not in source
    )
    assert "Refusing to install unlocked dependencies from requirements.txt." in source
    assert 'if not exist "tools\\check_requirements_lock.py" (' in source
    assert source.count(checker_call) == 2

    marker_index = source.index(marker_gate)
    install_index = source.index(locked_install)
    first_checker_index = source.index(checker_call)
    second_checker_index = source.rindex(checker_call)
    else_index = source.index(") else (", marker_index)

    assert source.index('if not exist "%REQUIREMENTS_FILE%" (') < marker_index
    assert source.index('if not exist "tools\\check_requirements_lock.py" (') < marker_index
    assert marker_index < install_index < first_checker_index < else_index
    assert else_index < second_checker_index
    assert "Dependencies are already current and verified." in source


def _write_runtime_contract(tmp_path: Path, direct: str, lock: str) -> None:
    (tmp_path / "requirements.txt").write_text(direct, encoding="utf-8")
    (tmp_path / "requirements-lock.txt").write_text(lock, encoding="utf-8")


def _mock_installed_metadata(monkeypatch, *, versions: dict[str, str], requires=None):
    requires = requires or {}

    def installed_version(name: str) -> str:
        if name not in versions:
            raise runtime_lock.metadata.PackageNotFoundError(name)
        return versions[name]

    monkeypatch.setattr(runtime_lock.metadata, "version", installed_version)
    monkeypatch.setattr(
        runtime_lock.metadata,
        "requires",
        lambda name: requires.get(name, ()),
    )


def test_direct_runtime_lock_accepts_exact_installed_pins(tmp_path, monkeypatch):
    _write_runtime_contract(
        tmp_path,
        "requests>=2,<3\npython-telegram-bot[socks]>=22,<23\n",
        "requests==2.34.2\npython-telegram-bot==22.8\n",
    )
    _mock_installed_metadata(
        monkeypatch,
        versions={
            "requests": "2.34.2",
            "python-telegram-bot": "22.8",
        },
    )

    result = runtime_lock.validate_runtime_dependency_lock(tmp_path)

    assert result["checked"] == 2
    assert result["direct"] == 2
    assert result["skipped"] == 0
    assert result["platform_skipped"] == 0
    assert result["runtime_closure"] == 2


def test_direct_runtime_lock_rejects_stale_lock_range(tmp_path, monkeypatch):
    _write_runtime_contract(
        tmp_path,
        "requests>=3\n",
        "requests==2.34.2\n",
    )
    _mock_installed_metadata(monkeypatch, versions={"requests": "2.34.2"})

    with pytest.raises(
        runtime_lock.RuntimeDependencyLockError,
        match="Repository dependency lock is invalid",
    ):
        runtime_lock.validate_runtime_dependency_lock(tmp_path)


def test_direct_runtime_lock_rejects_installed_version_drift(tmp_path, monkeypatch):
    _write_runtime_contract(
        tmp_path,
        "requests>=2,<3\n",
        "requests==2.34.2\n",
    )
    _mock_installed_metadata(monkeypatch, versions={"requests": "2.33.0"})

    with pytest.raises(
        runtime_lock.RuntimeDependencyLockError,
        match=r"requests=2\.33\.0 \(locked 2\.34\.2\)",
    ):
        runtime_lock.validate_runtime_dependency_lock(tmp_path)


def test_direct_runtime_lock_allows_optional_gemini_skip(tmp_path, monkeypatch):
    _write_runtime_contract(
        tmp_path,
        "requests>=2,<3\ngoogle-genai>=2,<3\n",
        "requests==2.34.2\ngoogle-genai==2.16.0\n",
    )
    _mock_installed_metadata(monkeypatch, versions={"requests": "2.34.2"})

    result = runtime_lock.validate_runtime_dependency_lock(
        tmp_path,
        skip_names=("google_genai",),
    )

    assert result["checked"] == 1
    assert result["skipped"] == 1
    assert result["runtime_closure"] == 1


def test_runtime_lock_honors_inactive_direct_platform_marker(tmp_path, monkeypatch):
    _write_runtime_contract(
        tmp_path,
        'never>=1; python_version < "0"\n',
        "never==1.0\n",
    )
    _mock_installed_metadata(monkeypatch, versions={})

    result = runtime_lock.validate_runtime_dependency_lock(tmp_path)

    assert result["checked"] == 0
    assert result["platform_skipped"] == 1
    assert result["runtime_closure"] == 0


def test_runtime_lock_follows_activated_extra_dependency(tmp_path, monkeypatch):
    _write_runtime_contract(
        tmp_path,
        "parent[feature]>=1\n",
        "parent==1.0\nchild==2.0\n",
    )
    _mock_installed_metadata(
        monkeypatch,
        versions={"parent": "1.0", "child": "2.0"},
        requires={
            "parent": ['child>=2; extra == "feature"'],
            "child": [],
        },
    )

    result = runtime_lock.validate_runtime_dependency_lock(tmp_path)

    assert result["checked"] == 2
    assert result["runtime_closure"] == 2


def test_runtime_lock_rejects_missing_activated_extra_dependency(tmp_path, monkeypatch):
    _write_runtime_contract(
        tmp_path,
        "parent[feature]>=1\n",
        "parent==1.0\nchild==2.0\n",
    )
    _mock_installed_metadata(
        monkeypatch,
        versions={"parent": "1.0"},
        requires={"parent": ['child>=2; extra == "feature"']},
    )

    with pytest.raises(
        runtime_lock.RuntimeDependencyLockError,
        match=r"child=missing \(locked 2\.0\)",
    ):
        runtime_lock.validate_runtime_dependency_lock(tmp_path)


def test_runtime_lock_ignores_inactive_extra_dependency(tmp_path, monkeypatch):
    _write_runtime_contract(
        tmp_path,
        "parent>=1\n",
        "parent==1.0\n",
    )
    _mock_installed_metadata(
        monkeypatch,
        versions={"parent": "1.0"},
        requires={"parent": ['child>=2; extra == "feature"']},
    )

    result = runtime_lock.validate_runtime_dependency_lock(tmp_path)

    assert result["checked"] == 1
    assert result["runtime_closure"] == 1


def test_runtime_lock_rejects_transitive_lock_constraint_mismatch(tmp_path, monkeypatch):
    _write_runtime_contract(
        tmp_path,
        "parent>=1\n",
        "parent==1.0\nchild==2.0\n",
    )
    _mock_installed_metadata(
        monkeypatch,
        versions={"parent": "1.0", "child": "2.0"},
        requires={"parent": ["child>=3"]},
    )

    with pytest.raises(
        runtime_lock.RuntimeDependencyLockError,
        match="child=locked-2.0-violates-parent-requirement->=3",
    ):
        runtime_lock.validate_runtime_dependency_lock(tmp_path)


def test_runtime_verifier_dependency_is_declared_directly():
    direct = Path("requirements.txt").read_text(encoding="utf-8")
    lock = Path("requirements-lock.txt").read_text(encoding="utf-8")

    assert "packaging>=26.2,<27.0" in direct
    assert "packaging==26.2" in lock


def test_direct_entrypoint_checks_lock_before_runtime_ownership():
    source = Path("bot_new.py").read_text(encoding="utf-8")
    core_source = Path("core/globals.py").read_text(encoding="utf-8")
    dependency_check = "validate_runtime_dependency_lock("
    singleton_import = "from services.process_singleton import acquire_early_singleton"
    bgutil_import = "from tools.ensure_bgutil_provider import ProvisionError"
    key_names = (
        "GEMINI_API_KEY",
        "GEMINI_API_KEY_2",
        "GEMINI_API_KEY_3",
        "GEMINI_API_KEY_4",
    )

    assert "if not ((3, 11) <= sys.version_info[:2] < (3, 14)):" in source
    assert "except ImportError as exc:" in source
    assert "if not _gemini_keys_configured:" in source
    assert "_gemini_key =" not in source
    assert "python -m pip install -r requirements-lock.txt" in source
    assert dependency_check in source
    assert "requirements-lock.txt" in source
    for name in key_names:
        assert f'"{name}"' in source
        assert name in core_source
    assert "GEMINI_API_KEY_5" not in source
    assert "GEMINI_API_KEY_5" not in core_source
    assert source.index(dependency_check) < source.index(singleton_import)
    assert source.index(dependency_check) < source.index(bgutil_import)
