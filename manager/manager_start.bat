@echo off
rem manager: source launcher. ASCII, UTF-8 without BOM, LF only.
rem Args are passed unchanged. This script never creates shortcuts.
setlocal
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
set "PYW=.venv\Scripts\pythonw.exe"
set "NOTICE=%~dp0setup\python_missing.hta"

if exist "%PY%" goto check_version
py -3 -m venv .venv 2>nul
if not exist "%PY%" python -m venv .venv 2>nul
rem Right after an install the PATH of the already-running explorer may be stale,
rem so also look where the official installer puts python per user / per machine.
if not exist "%PY%" for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if not exist "%PY%" if exist "%%D\python.exe" "%%D\python.exe" -m venv .venv 2>nul
if not exist "%PY%" for /d %%D in ("%ProgramFiles%\Python3*") do if not exist "%PY%" if exist "%%D\python.exe" "%%D\python.exe" -m venv .venv 2>nul
if not exist "%PY%" goto need_python

:check_version
"%PY%" -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if errorlevel 1 goto need_upgrade

"%PY%" -c "import importlib.util as u,sys; sys.exit(0 if all(u.find_spec(m) for m in ('webview','pynput','pystray','PIL',)) else 1)" >nul 2>nul
if errorlevel 1 (
    "%PY%" -m pip install -r requirements.txt --disable-pip-version-check
    if errorlevel 1 goto need_deps
)
if not exist "%PYW%" goto need_python

rem Console commands must finish and return the app's exit code.
rem One forward-only test: a backward goto is unreliable in an LF-only batch file.
set "CONSOLE=0"
echo %* | findstr /i /c:"--selftest" /c:"--quit" /c:"--reload" /c:"--where" /c:"--dump" /c:"--build-only" /c:"--bench=" >nul && set "CONSOLE=1"
if "%CONSOLE%"=="1" goto console_run
start "" "%PYW%" "%~dp0app.py" %*
exit /b 0

:console_run
"%PY%" "%~dp0app.py" %*
exit /b %errorlevel%

rem Every environment problem ends in one native dialog instead of a console
rem that flashes by. Chinese lives in the .hta because this file stays ASCII.
:need_python
set "MODE=missing"
goto show_notice

:need_upgrade
set "MODE=old"
goto show_notice

:need_deps
set "MODE=deps"
goto show_notice

:show_notice
if not exist "%SystemRoot%\System32\mshta.exe" goto notice_console
start "" mshta "%NOTICE%" %MODE%
exit /b 1

:notice_console
echo Python 3.10 or newer is required: https://www.python.org/downloads/windows/
echo Install it, then run this launcher again.
pause
exit /b 1
