@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PY="

rem ---- 1) Try python on PATH ----
for /f "delims=" %%P in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%P"

rem ---- 2) DoubaoWork bundled sandbox python (base folder name is a hash, use wildcard) ----
if not defined PY (
    for /d %%D in ("%LOCALAPPDATA%\DoubaoWork\User Data\sandbox_runtime\bases\*") do (
        if exist "%%D\python\python.exe" set "PY=%%D\python\python.exe"
    )
)

rem ---- 3) Common standalone install locations ----
if not defined PY (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PY=%%D\python.exe"
)
if not defined PY (
    for /d %%D in ("C:\Python3*") do if exist "%%D\python.exe" set "PY=%%D\python.exe"
)

if not defined PY (
    echo [ERROR] Python not found. Please install Python 3.8 or newer from:
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Using Python: !PY!

rem ---- Derive pythonw.exe (windowless launcher) in the same folder ----
set "PYW=!PY:python.exe=pythonw.exe!"
if not exist "!PYW!" set "PYW=!PY!"

rem ---- Ensure PySide6 is available, install if missing ----
"!PY!" -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo First run: installing PySide6, please wait...
    "!PY!" -m pip install PySide6-Essentials
    if errorlevel 1 (
        echo [ERROR] Failed to install PySide6. Please check your network and retry.
        pause
        exit /b 1
    )
)

rem ---- Launch the GUI (no console window) ----
start "" "!PYW!" "%~dp0caj_gui.py"
endlocal
