@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Run the setup steps in README.md first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m app.main
if errorlevel 1 pause
