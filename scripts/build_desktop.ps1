$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host '=== ULTRON Desktop Build ===' -ForegroundColor Cyan

Write-Host '[1/4] Building React frontend...' -ForegroundColor Yellow
Push-Location frontend
npm ci
npm run build
Pop-Location

Write-Host '[2/4] Building Python backend...' -ForegroundColor Yellow
python -m pip install --upgrade pyinstaller
python -m PyInstaller --noconfirm --clean backend/ULTRON_BACKEND.spec

if (-not (Test-Path 'backend/dist/ULTRON_BACKEND.exe')) {
    throw 'PyInstaller backend output bulunamadı.'
}

Write-Host '[3/4] Installing Electron dependencies...' -ForegroundColor Yellow
Push-Location desktop
npm install
Pop-Location

Write-Host '[4/4] Creating Windows installer...' -ForegroundColor Yellow
Push-Location desktop
npm run make
Pop-Location

Write-Host ''
Write-Host '=== ULTRON Desktop build tamamlandı ===' -ForegroundColor Green
Write-Host 'Kurulum çıktısı: desktop/out' -ForegroundColor Green
