@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

set "VENV_DIR=.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "SETUP_MARKER=%VENV_DIR%\.setup-complete"
set "WPC_MIGRATION_MARKER=%VENV_DIR%\.wpc-provider-removed"
set "BGUTIL_WHEEL_MIGRATION_MARKER=%VENV_DIR%\.bgutil-wheel-removed"
set "REQ_HASH_FILE=%VENV_DIR%\.requirements-hash.tmp"

if not exist "bot_new.py" (
    echo ERROR: bot_new.py not found in:
    echo %CD%
    echo.
    echo Put this BAT file in the project root folder.
    pause
    exit /b 1
)

if exist "%VENV_PYTHON%" (
    "%VENV_PYTHON%" -c "import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 14) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo [SETUP] Existing .venv uses an unsupported Python version.
        echo [SETUP] Recreating .venv with Python 3.11, 3.12 or 3.13...
        rmdir /s /q "%VENV_DIR%"
        if exist "%VENV_DIR%" (
            echo.
            echo ERROR: Failed to remove the incompatible .venv.
            echo Close Python processes using this folder and run the BAT again.
            pause
            exit /b 1
        )
    )
)

if not exist "%VENV_PYTHON%" (
    echo [SETUP] Virtual environment .venv was not found.
    echo [SETUP] Creating it automatically...

    call :find_python
    if errorlevel 1 (
        echo.
        echo ERROR: Python 3.11, 3.12 or 3.13 was not found.
        echo Install Python from python.org and enable the Python Launcher.
        pause
        exit /b 1
    )

    !PYTHON_COMMAND! -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo.
        echo ERROR: Failed to create .venv.
        pause
        exit /b 1
    )
)

set "REQUIREMENTS_FILE=requirements-lock.txt"
if not exist "%REQUIREMENTS_FILE%" set "REQUIREMENTS_FILE=requirements.txt"
if not exist "%REQUIREMENTS_FILE%" (
    echo ERROR: requirements-lock.txt and requirements.txt were not found.
    pause
    exit /b 1
)

set "CURRENT_REQ_HASH="
del /q "%REQ_HASH_FILE%" >nul 2>&1
"%VENV_PYTHON%" -c "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path(r'%REQUIREMENTS_FILE%').read_bytes()).hexdigest())" >"%REQ_HASH_FILE%"
if errorlevel 1 (
    del /q "%REQ_HASH_FILE%" >nul 2>&1
    echo ERROR: Failed to calculate the dependency file hash.
    pause
    exit /b 1
)
if exist "%REQ_HASH_FILE%" set /p CURRENT_REQ_HASH=<"%REQ_HASH_FILE%"
del /q "%REQ_HASH_FILE%" >nul 2>&1
if not defined CURRENT_REQ_HASH (
    echo ERROR: Dependency hash output was empty.
    pause
    exit /b 1
)

set "SAVED_REQ_HASH="
if exist "%SETUP_MARKER%" set /p SAVED_REQ_HASH=<"%SETUP_MARKER%"

if /I not "!CURRENT_REQ_HASH!"=="!SAVED_REQ_HASH!" (
    echo [SETUP] Dependency set changed or was never installed.
    echo [SETUP] Installing from %REQUIREMENTS_FILE%...

    "%VENV_PYTHON%" -m pip install --upgrade pip
    if errorlevel 1 goto :pip_error

    "%VENV_PYTHON%" -m pip install -r "%REQUIREMENTS_FILE%"
    if errorlevel 1 goto :pip_error

    if /I "%REQUIREMENTS_FILE%"=="requirements-lock.txt" (
        if exist "tools\check_requirements_lock.py" (
            "%VENV_PYTHON%" tools\check_requirements_lock.py
            if errorlevel 1 goto :pip_error
        )
    )

    >"%SETUP_MARKER%" echo !CURRENT_REQ_HASH!
    echo [SETUP] Dependencies installed and verified successfully.
) else (
    echo [SETUP] Dependencies are already current.
)

rem One-time migration from the old WPC/nodriver browser provider.
rem Keep it idempotent without paying a pip subprocess cost on every bot start.
if not exist "%WPC_MIGRATION_MARKER%" (
    "%VENV_PYTHON%" -m pip uninstall -y yt-dlp-getpot-wpc nodriver >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Failed to remove the obsolete browser-based YouTube PO Token runtime.
        pause
        exit /b 1
    )
    >"%WPC_MIGRATION_MARKER%" echo browser-provider-removed-v1
)

rem One-time migration from the released bgutil wheel to one exact source tree.
rem yt-dlp.conf already disables default/global plugin dirs, but remove the stale
rem wheel as well so the venv contains no redundant provider implementation.
if not exist "%BGUTIL_WHEEL_MIGRATION_MARKER%" (
    "%VENV_PYTHON%" -m pip uninstall -y bgutil-ytdlp-pot-provider >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Failed to remove the obsolete bgutil Python provider wheel.
        pause
        exit /b 1
    )
    >"%BGUTIL_WHEEL_MIGRATION_MARKER%" echo exact-source-provider-v1
)

if not exist "tools\ensure_bgutil_provider.py" (
    echo ERROR: tools\ensure_bgutil_provider.py not found.
    pause
    exit /b 1
)
"%VENV_PYTHON%" tools\ensure_bgutil_provider.py
if errorlevel 1 (
    echo.
    echo ERROR: Browserless YouTube PO Token runtime is not ready.
    echo Check git/npm/Node.js and the setup message above.
    pause
    exit /b 1
)

echo [START] Starting MP3 Telegram Bot...
"%VENV_PYTHON%" bot_new.py
set "BOT_EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%BOT_EXIT_CODE%"=="0" (
    echo Bot stopped with error code %BOT_EXIT_CODE%.
) else (
    echo Bot stopped normally.
)
pause
exit /b %BOT_EXIT_CODE%

:pip_error
echo.
echo ERROR: Failed to install or verify dependencies from %REQUIREMENTS_FILE%.
echo Check the messages above, your Internet connection and available disk space.
pause
exit /b 1

:find_python
for %%V in (3.13 3.12 3.11) do (
    py -%%V -c "import sys" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_COMMAND=py -%%V"
        exit /b 0
    )
)

python -c "import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 14) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_COMMAND=python"
    exit /b 0
)

exit /b 1
