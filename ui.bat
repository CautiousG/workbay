@echo off
REM Apiary - Workspace Manager: double-click, the page opens on http://127.0.0.1:8780/
REM Runs from this directory; the only thing it installs is west, once, with pip.
REM Close this window or press Ctrl+C to stop it.
setlocal
chcp 65001 >nul
cd /d "%~dp0"

REM python first so an activated venv wins; the py launcher when no python is on PATH
set "PY="
where python.exe >nul 2>nul && set "PY=python"
if not defined PY where py.exe >nul 2>nul && set "PY=py -3"
if not defined PY goto no_python

%PY% -c "import sys; sys.exit(sys.version_info < (3, 10))"
if errorlevel 1 goto old_python

%PY% -c "import west" 2>nul
if not errorlevel 1 goto run

echo [Apiary] First run: installing west ...
REM --user, but not in a venv: pip refuses it there
set "PIPUSER=--user"
%PY% -c "import sys; sys.exit(sys.prefix == sys.base_prefix)"
if not errorlevel 1 set "PIPUSER="
%PY% -m pip install %PIPUSER% "west>=1.2"
if errorlevel 1 goto no_west

:run
%PY% -m apiary ui %*
pause
exit /b 0

:no_python
echo [Apiary] Python not found. Install Python 3.10+ and tick "Add python.exe to PATH".
pause
exit /b 1

:old_python
echo [Apiary] Python 3.10+ is needed, this one is:
%PY% --version
pause
exit /b 1

:no_west
echo [Apiary] pip could not install west. Check the network / pip index, or run:
echo     %PY% -m pip install west
pause
exit /b 1
