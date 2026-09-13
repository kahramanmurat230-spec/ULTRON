@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
title ULTRON Windows Build

where node >nul 2>&1 || ( echo [HATA] Node.js bulunamadi. nodejs.org adresinden LTS kurun. & pause & exit /b 1 )
where npm >nul 2>&1 || ( echo [HATA] npm bulunamadi. Node.js kurulumunu onarin. & pause & exit /b 1 )

echo [1/4] Desktop frontend build ediliyor...
cd frontend
call npm install
if errorlevel 1 goto :error
call npm run build
if errorlevel 1 goto :error
cd ..

echo [2/4] Desktop paketleme bagimliliklari kuruluyor...
cd desktop
call npm install
if errorlevel 1 goto :error

echo [3/4] ULTRON Windows installer uretiliyor...
call npm run dist
if errorlevel 1 goto :error
cd ..

echo [4/4] TAMAMLANDI.
echo Installer: desktop\dist\ULTRON-Setup-^<version^>.exe (surum: desktop\package.json)
echo.
echo Not: ULTRON local Python + Ollama ortamini kullanir; herhangi bir bulut API anahtari gerekmez.
pause
exit /b 0

:error
cd /d "%~dp0.."
echo.
echo [HATA] Build tamamlanamadi. Yukaridaki hatayi kontrol edin.
pause
exit /b 1
