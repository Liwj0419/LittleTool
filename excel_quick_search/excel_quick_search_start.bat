@echo off
rem excel_quick_search: source launcher. ASCII, UTF-8 without BOM, LF only.
rem Args are passed unchanged. This script never creates shortcuts.
setlocal
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
set "PYW=.venv\Scripts\pythonw.exe"
if not exist "%PY%" (
    py -3 -m venv .venv 2>nul
    if not exist "%PY%" python -m venv .venv 2>nul
    if not exist "%PY%" (
        echo Python 3.10 or newer is required.
        pause
        exit /b 1
    )
    "%PY%" -m pip install -r requirements.txt --disable-pip-version-check
    if errorlevel 1 exit /b 1
)
"%PY%" -c "import importlib.util as u,sys; sys.exit(0 if all(u.find_spec(m) for m in ('webview','pynput',)) else 1)" >nul 2>nul
if errorlevel 1 (
    "%PY%" -m pip install -r requirements.txt --disable-pip-version-check
    if errorlevel 1 exit /b 1
)
if not exist "%PYW%" (
    echo The local virtual environment is incomplete. Missing pythonw.exe.
    exit /b 1
)
rem Console commands must finish and return the app's exit code.
rem Let Python read the same argv as app.py, with no backward jump in an LF-only file.
"%PY%" -c "import sys; flags = {'--selftest', '--quit', '--reload', '--where', '--dump', '--build-only'}; args = (arg.lower() for arg in sys.argv[1:]); sys.exit(0 if any(arg in flags or arg.startswith('--bench=') for arg in args) else 1)" %*
if not errorlevel 1 goto console_run
start "" "%PYW%" "%~dp0app.py" %*
exit /b 0
:console_run
"%PY%" "%~dp0app.py" %*
exit /b %errorlevel%
