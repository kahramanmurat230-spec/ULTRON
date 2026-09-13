@echo off
setlocal EnableExtensions
title ULTRON Command Center
cd /d "%~dp0.."

if not exist ".venv\Scripts\activate.bat" (
  echo [WARN] .venv bulunamadi — once scripts\install_windows.bat calistirin.
  echo        Sistem Python ile devam ediliyor; bagimliliklar eksikse backend penceresinde hata gorunur.
) else ( call .venv\Scripts\activate.bat )

where python >nul 2>&1 || ( echo [FAIL] Python bulunamadi. & pause & exit /b 1 )

set NPM_OK=1
where npm >nul 2>&1 || (
  set NPM_OK=0
  echo [WARN] npm bulunamadi — frontend/mobil baslatilamaz; sadece backend acilir.
  echo        Node.js LTS kurun: https://nodejs.org
)

echo [DOCTOR] Acilis on-taramasi...
cd backend
python -B -c "from app.core.doctor import run_doctor; r=run_doctor({'db_paths':[],'rules_path':'config/security/master_rules.json'}); print(r['summary']); print('overall:', r['overall'])"
cd ..

echo [START] Backend (8000)...
cd backend
start "ULTRON-BACKEND" cmd /k "title ULTRON-BACKEND && python -B server.py"
cd ..
if "%NPM_OK%"=="1" (
  echo [START] Desktop UI (5173) + Mobile UI (5174)...
  pushd frontend
  start "ULTRON-DESKTOP" cmd /k "title ULTRON-DESKTOP && npm run dev"
  popd
  pushd frontend-mobile
  start "ULTRON-MOBILE" cmd /k "title ULTRON-MOBILE && npm run dev"
  popd
)

echo.
echo   Desktop: http://localhost:5173
echo   Mobile : http://localhost:5174
echo   Brain  : http://localhost:8000/api/hud/overview
echo.
echo   ULTRON ayakta. Boss, sahne senin.
echo   Not: Pencereler cmd /k ile acilir — hata olursa pencere acik kalir, tani kolaylasir.
pause
