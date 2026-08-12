@echo off
REM Double-click this. Sets itself up the first time, then opens the panel.
cd /d "%~dp0"

set PY=
for %%C in (python py) do (
  if not defined PY (
    %%C -c "import sys; sys.exit(sys.version_info < (3,11))" >nul 2>&1 && set PY=%%C
  )
)
if not defined PY (
  echo.
  echo   Python 3.11 or newer is required.
  echo   Get it from https://www.python.org/downloads/
  echo   Tick "Add Python to PATH" during install, then run this again.
  echo.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   First run - setting up. A minute or two, only once.
  %PY% -m venv .venv || goto fail
)

".venv\Scripts\python.exe" -c "import fastapi, playwright, psutil, qrcode" >nul 2>&1
if errorlevel 1 (
  echo   Installing components...
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
  ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || goto fail
)

echo.
echo   Starting the panel - leave this window open.
".venv\Scripts\python.exe" web.py
pause
exit /b 0

:fail
echo.
echo   Setup failed. Check your internet connection and try again.
pause
exit /b 1
