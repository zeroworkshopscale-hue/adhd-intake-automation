@echo off
REM ===== Dev/test launcher: runs the LATEST source (incl. the new fixes) =====
REM This is for testing only. Staff use the bundled ADHD_Intake.exe instead.
cd /d "%~dp0"
"C:\Users\bcice\Desktop\Projects\Automation\_testvenv\Scripts\python.exe" main.py
echo.
echo (App closed.) Press any key to close this window.
pause >nul
