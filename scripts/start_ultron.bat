@echo off
setlocal EnableExtensions
title ULTRON Command Center
cd /d "%~dp0.."

echo [DOCTOR] Acilis on-taramasi...
if exist ".venv\Scripts\activate.bat" ( call .venv\Scripts\activate.bat )
cd backend
python -B -c "from app.core.doctor import run_doctor; r=run_doctor({'db_paths':[],'rules_path':'config/security/master_rules.json'}); print(r['summary']); print('overall:', r['overall'])"
cd ..

echo [START] Backend (8000) + Desktop (5173) + Mobile (5174)...
cd backend
start "ULTRON-BACKEND" cmd /c "title ULTRON-BACKEND && python -B server.py"
cd ..\frontend
start "ULTRON-DESKTOP" cmd /c "title ULTRON-DESKTOP && npm run dev"
cd ..\frontend-mobile
start "ULTRON-MOBILE" cmd /c "title ULTRON-MOBILE && npm run dev"
cd ..

echo.
echo   Desktop: http://localhost:5173
echo   Mobile : http://localhost:5174
echo   Brain  : http://localhost:8000/api/hud/overview
echo.
echo   ULTRON ayakta. Boss, sahne senin.
pause
