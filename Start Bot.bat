@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

set "VENV_PYTHON=.venv\Scripts\python.exe"
set "SETUP_MARKER=.venv\.setup-complete"

if not exist "bot_new.py" (
    echo ERROR: bot_new.py not found in:
    echo %CD%
    echo.
    echo Put this BAT file in the project root folder.
    pause
    exit /b 1
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

    !PYTHON_COMMAND! -m venv ".venv"
    if errorlevel 1 (
        echo.
        echo ERROR: Failed to create .venv.
        pause
        exit /b 1
    )
)

if not exist "%SETUP_MARKER%" (
    if not exist "requirements.txt" (
        echo ERROR: requirements.txt not found.
        pause
        exit /b 1
    )

    echo [SETUP] Installing Python dependencies...
    "%VENV_PYTHON%" -m pip install --upgrade pip
    if errorlevel 1 goto :pip_error

    "%VENV_PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 goto :pip_error

    >"%SETUP_MARKER%" echo Setup completed successfully.
    echo [SETUP] Dependencies installed successfully.
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
echo ERROR: Failed to install dependencies from requirements.txt.
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

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) and sys.version_info < (3, 14) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_COMMAND=python"
    exit /b 0
)

exit /b 1
