@echo off
rem AMD XSS Engine - start the daemon hidden (no console window)
setlocal
set "PYW="
where pythonw >nul 2>nul && set "PYW=pythonw"
if not defined PYW where python >nul 2>nul && set "PYW=python"
if not defined PYW (
  echo Python 3 not found on PATH. Install Python 3.10+ or edit this file.
  exit /b 1
)
start "" %PYW% "%~dp0XssEngine.py" daemon
