@echo off
setlocal
cd /d "%~dp0"
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
.venv\Scripts\python.exe -c "import webview, torch, silero_vad" >nul 2>&1 || .venv\Scripts\python.exe -m pip install -r requirements.txt
if exist .venv\Scripts\pythonw.exe (
  start "" .venv\Scripts\pythonw.exe native_main.py
) else (
  python native_main.py
)
