@echo off
REM ====================================================================
REM  ADHD Intake Automation - launcher (no console window)
REM  Double-click this file to start the app. The CMD window closes
REM  immediately; the app runs windowless via pythonw.
REM ====================================================================
cd /d "%~dp0"

REM Prefer pythonw.exe (no console). Fall back to the py launcher / python.
set "PYW="
if exist "C:\Users\dhire\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe" (
    set "PYW=C:\Users\dhire\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe"
)
if not defined PYW (
    where pythonw >nul 2>nul && set "PYW=pythonw"
)
if not defined PYW (
    REM Last resort: regular python (shows a console). Better than nothing.
    where python >nul 2>nul && set "PYW=python"
)
if not defined PYW (
    echo Could not find Python. Please install Python 3.11+ and try again.
    pause
    exit /b 1
)

start "" "%PYW%" "%~dp0main.py"
exit
