@echo off
rem AMD XSS Engine - console launcher (status / probe / set / telemetry)
setlocal
set "PYC="
where py >nul 2>nul && set "PYC=py -3"
if not defined PYC where python >nul 2>nul && set "PYC=python"
if not defined PYC (
  echo Python 3 not found on PATH. Install Python 3.10-3.12 or edit this file.
  exit /b 1
)
%PYC% "%~dp0XssEngine.py" %*
