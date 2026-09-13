@echo off
setlocal EnableExtensions
title ULTRON Final Installer
echo ================================================
echo   ULTRON V17+ — Windows Tek Tikla Kurulum
echo ================================================

where python >nul 2>&1 || ( echo [FAIL] Python bulunamadi. python.org'dan 3.11+ kurun ve PATH'e ekleyin. & pause & exit /b 1 )
python -c "import sys; v=sys.version_info; print('Python %d.%d.%d' % v[:3]); sys.exit(0 if v >= (3,11) else 1)"
if errorlevel 1 (
  echo [FAIL] Python 3.11+ gerekli. Microsoft Store python alias'i varsa:
  echo        Ayarlar ^> Uygulamalar ^> Geli^smis uygulama ayarlari ^> Uygulama calistirma takma adlari ^> python.exe/python3.exe KAPALI.
  pause & exit /b 1
)
where node >nul 2>&1 || ( echo [FAIL] Node.js bulunamadi. nodejs.org adresinden LTS surumunu kurun. & pause & exit /b 1 )
where npm >nul 2>&1 || ( echo [FAIL] npm bulunamadi. Node.js kurulumunu onarin; PATH'te npm.cmd gorunmeli. & pause & exit /b 1 )

cd /d "%~dp0.."
if not exist ".venv" (
  echo [1/6] Sanal ortam olusturuluyor...
  python -m venv .venv
  if errorlevel 1 ( echo [FAIL] venv olusturulamadi. & pause & exit /b 1 )
) else ( echo [1/6] Sanal ortam mevcut. )

echo [2/6] Python bagimliliklari kuruluyor...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
if errorlevel 1 ( echo [FAIL] backend\requirements.txt kurulamadi — ag/pip hatasina bakin. & pause & exit /b 1 )
python -m pip install -r backend\requirements.v16.txt
if errorlevel 1 ( echo [WARN] requirements.v16.txt tam kurulamadi — opsiyonel yetenekler sinirli kalir. )

echo [3/6] Frontend bagimliliklari + production build...
if not exist "frontend\package-lock.json" ( echo [FAIL] frontend\package-lock.json bulunamadi. & pause & exit /b 1 )
pushd frontend
call npm ci
if errorlevel 1 ( popd & echo [FAIL] frontend npm ci basarisiz — node_modules silinip tekrar denenmeli. & pause & exit /b 1 )
call npm run build
if errorlevel 1 ( popd & echo [FAIL] frontend build basarisiz. & pause & exit /b 1 )
popd

echo [4/6] Mobil frontend bagimliliklari + build...
if exist "frontend-mobile\package-lock.json" (
  pushd frontend-mobile
  call npm ci
  if errorlevel 1 ( popd & echo [FAIL] frontend-mobile npm ci basarisiz. & pause & exit /b 1 )
  call npm run build
  if errorlevel 1 ( popd & echo [WARN] frontend-mobile build basarisiz — mobil arayuz dev modda calisabilir. )
  popd
) else ( echo [WARN] frontend-mobile package-lock.json bulunamadi. )

echo [5/6] Desktop kabugu bagimliliklari (Electron)...
if exist "desktop\package.json" (
  pushd desktop
  call npm install
  if errorlevel 1 ( popd & echo [WARN] desktop npm install basarisiz — desktop kabugu/installer build calismaz. ) else ( popd )
) else ( echo [WARN] desktop\package.json bulunamadi. )

echo [6/6] Ollama kontrolu...
where ollama >nul 2>&1 || (
  echo [WARN] Ollama kurulu degil. https://ollama.com adresinden kurun.
  echo        Kurulumdan sonra: ollama pull qwen2.5-coder:7b ^& ollama pull llava:7b
  goto rules
)
ollama list | findstr /i "qwen2.5-coder" >nul || ( echo        -^> qwen2.5-coder:7b cekiliyor... & ollama pull qwen2.5-coder:7b )
ollama list | findstr /i "llava" >nul || ( echo        -^> llava:7b cekiliyor... & ollama pull llava:7b )

:rules
echo master_rules muhurleniyor + ilk saglik taramasi...
cd backend
python -B -c "from app.personal.user_dna import MasterRules; MasterRules(); print('   master_rules.json muhurlendi (KUTSAL).')"
python -B -c "from app.core.doctor import run_doctor; r=run_doctor({'db_paths':[], 'rules_path':'config/security/master_rules.json'}); print('   DOCTOR:', r['overall']); print('  ', r['summary'])"
cd ..

echo [VERIFY] Bagimlilik ve temel ortam dogrulamasi...
.venv\Scripts\python.exe scripts\check_deps.py
if errorlevel 1 (
  echo [WARN] Zorunlu bazi ogeler eksik — yukaridaki rapora bakin.
) else (
  echo [OK] Bagimlilik dogrulamasi gecti.
)
echo.
echo Kurulum tamam. Baslatmak icin: scripts\start_ultron.bat
pause
