@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Run the setup steps in README.md first.
  pause
  exit /b 1
)
if exist ".venv\Scripts\pythonw.exe" (
  start "" "%~dp0.venv\Scripts\pythonw.exe" -m app.main
  exit /b 0
)
REM Fallback for environments without pythonw; the console remains visible.
".venv\Scripts\python.exe" -m app.main
if errorlevel 1 pause
