@echo off
REM ASCII-only CMD wrapper; implementation is shared with POSIX in backup.py.
setlocal
if defined PYTHON goto :run_python
set "PY="
for %%P in (python.exe python3.exe py.exe python python3 py) do (
    if not defined PY (
        call %%P -c "import sys" >nul 2>&1
        if not errorlevel 1 set "PY=%%P"
    )
)
if not defined PY goto :missing_python
set "PYTHON=%PY%"

:run_python
call "%PYTHON%" "%~dp0backup.py" %*
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%

:missing_python
echo [ERROR] Python 3 was not found. Add it to PATH or set PYTHON.
endlocal & exit /b 1
