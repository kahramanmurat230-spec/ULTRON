@echo off
setlocal EnableExtensions
title ULTRON Autostart
cd /d "%~dp0.."
set TASK=UltronCommandCenter
set STARTBAT=%~dp0start_ultron.bat

if /i "%~1"=="remove" (
  schtasks /Delete /TN "%TASK%" /F >nul 2>&1
  echo Ultron otomatik baslatma KALDIRILDI.
  goto end
)

schtasks /Create /TN "%TASK%" /TR "\"%STARTBAT%\"" /SC ONLOGON /RL HIGHEST /F
if %errorlevel%==0 (
  echo Ultron, Windows acilisina eklendi. Her giriste Boss'un karsisinda olacak.
) else (
  echo [WARN] Gorev Zamanlayici basarisiz; Startup klasoru deneniyor...
  copy /Y "%STARTBAT%" "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\start_ultron.bat" >nul
  echo Startup klasorune kopyalandi.
)
echo Kaldirma icin: autostart_setup.bat remove
:end
pause
