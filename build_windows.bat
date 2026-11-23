@echo off
setlocal
cd /d "%~dp0"

if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt

python -m PyInstaller --clean --noconfirm LectureTrim.spec
if errorlevel 1 exit /b 1

echo.
echo Build complete: dist\LectureTrim.exe
echo Run install_windows.bat to add it to the desktop and Windows search.
