from pathlib import Path


def test_start_bot_recreates_unsupported_virtualenv_before_launch():
    source = Path("Start Bot.bat").read_text(encoding="utf-8")

    version_check = "(3, 11) <= sys.version_info[:2] < (3, 14)"
    assert version_check in source
    assert "rmdir /s /q \"%VENV_DIR%\"" in source
    assert source.index(version_check) < source.index(
        "if not exist \"%VENV_PYTHON%\" ("
    )


def test_start_bot_reinstalls_when_locked_dependencies_change():
    source = Path("Start Bot.bat").read_text(encoding="utf-8")

    assert "set \"REQUIREMENTS_FILE=requirements-lock.txt\"" in source
    assert "hashlib.sha256" in source
    assert "CURRENT_REQ_HASH" in source
    assert "SAVED_REQ_HASH" in source
    assert (
        "if /I not \"!CURRENT_REQ_HASH!\"==\"!SAVED_REQ_HASH!\" ("
        in source
    )
    assert ">\"%SETUP_MARKER%\" echo !CURRENT_REQ_HASH!" in source


def test_start_bot_hash_uses_temp_file_not_nested_for_f_quoting():
    source = Path("Start Bot.bat").read_text(encoding="utf-8")

    assert "set \"REQ_HASH_FILE=%VENV_DIR%\\.requirements-hash.tmp\"" in source
    assert ">\"%REQ_HASH_FILE%\"" in source
    assert "set /p CURRENT_REQ_HASH=<\"%REQ_HASH_FILE%\"" in source
    assert "del /q \"%REQ_HASH_FILE%\"" in source
    assert "for /f \"delims=\" %%H" not in source


def test_start_bot_verifies_lock_after_install():
    source = Path("Start Bot.bat").read_text(encoding="utf-8")

    assert "tools\\check_requirements_lock.py" in source
    assert (
        "ERROR: Failed to install or verify dependencies "
        "from %REQUIREMENTS_FILE%."
    ) in source


def test_start_bot_migrates_off_browser_wpc_and_bootstraps_bgutil():
    source = Path("Start Bot.bat").read_text(encoding="utf-8")

    assert "pip show yt-dlp-getpot-wpc" in source
    assert "pip uninstall -y yt-dlp-getpot-wpc nodriver" in source
    assert "tools\\bootstrap_bgutil_provider.py" in source
    assert source.index("pip uninstall -y yt-dlp-getpot-wpc nodriver") < source.index(
        "tools\\bootstrap_bgutil_provider.py"
    )
    assert source.index("tools\\bootstrap_bgutil_provider.py") < source.index(
        'echo [START] Starting MP3 Telegram Bot...'
    )
