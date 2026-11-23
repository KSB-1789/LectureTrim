@echo off
setlocal
cd /d "%~dp0"

if not exist "dist\LectureTrim.exe" (
  echo Build the app first:
  echo   .venv\Scripts\python.exe -m PyInstaller --clean --noconfirm LectureTrim.spec
  exit /b 1
)

set "DEST=%LOCALAPPDATA%\Programs\LectureTrim"
if not exist "%DEST%" mkdir "%DEST%"
copy /Y "dist\LectureTrim.exe" "%DEST%\LectureTrim.exe" >nul

powershell -NoProfile -Command ^
  "$s=New-Object -ComObject WScript.Shell;" ^
  "$target=Join-Path '%DEST%' 'LectureTrim.exe';" ^
  "$dirs=@([Environment]::GetFolderPath('Desktop'), (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'));" ^
  "foreach($dir in $dirs){" ^
  "  $l=$s.CreateShortcut((Join-Path $dir 'LectureTrim.lnk'));" ^
  "  $l.TargetPath=$target;" ^
  "  $l.WorkingDirectory='%DEST%';" ^
  "  $l.Description='Trim lecture recordings locally';" ^
  "  $l.Save()" ^
  "}"

echo.
echo Installed to %DEST%
echo LectureTrim is now on the desktop and in the Start Menu, so it shows up in Windows search.
pause
