@echo off
setlocal EnableExtensions
title ULTRON Final Installer
echo ================================================
echo   ULTRON V17+ — Windows Tek Tikla Kurulum
echo ================================================

where python >nul 2>&1 || ( echo [FAIL] Python bulunamadi. python.org'dan 3.11+ kurun. & pause & exit /b 1 )

cd /d "%~dp0.."
if not exist ".venv" (
  echo [1/4] Sanal ortam olusturuluyor...
  python -m venv .venv
) else ( echo [1/4] Sanal ortam mevcut. )

echo [2/5] Python bagimliliklari kuruluyor...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
python -m pip install -r backend\requirements.v16.txt


echo [3/5] Frontend bagimliliklari kuruluyor...
if exist "frontend\package-lock.json" ( cd frontend & call npm ci & cd .. ) else ( echo [WARN] frontend package-lock.json bulunamadi. & goto frontend_mobile )
:frontend_mobile
if exist "frontend-mobile\package-lock.json" ( cd frontend-mobile & call npm ci & cd .. ) else ( echo [WARN] frontend-mobile package-lock.json bulunamadi. )

echo [4/5] Ollama kontrolu...
where ollama >nul 2>&1 || (
  echo [WARN] Ollama kurulu degil. https://ollama.com adresinden kurun.
  echo        Kurulumdan sonra: ollama pull qwen2.5-coder:7b ^& ollama pull llava:7b
  goto rules
)
ollama list | findstr /i "qwen" >nul || ( echo        -> qwen2.5-coder:7b cekiliyor... & ollama pull qwen2.5-coder:7b )
ollama list | findstr /i "llava" >nul || ( echo        -> llava:7b cekiliyor... & ollama pull llava:7b )

:rules
echo [5/5] master_rules muhurleniyor + ilk saglik taramasi...
cd backend
python -B -c "from app.personal.user_dna import MasterRules; MasterRules(); print('   master_rules.json muhurlendi (KUTSAL).')"
python -B -c "from app.core.doctor import run_doctor; r=run_doctor({'db_paths':[], 'rules_path':'config/security/master_rules.json'}); print('   DOCTOR:', r['overall']); print('  ', r['summary'])"
cd ..

echo [VERIFY] Bagimlilik ve temel ortam dogrulamasi...
python scripts\check_deps.py
if errorlevel 1 (
  echo [WARN] Zorunlu bazi ogeler eksik — yukaridaki rapora bakin.
) else (
  echo [OK] Bagimlilik dogrulamasi gecti.
)
echo.
echo Kurulum tamam. Baslatmak icin: scripts\start_ultron.bat
pause
