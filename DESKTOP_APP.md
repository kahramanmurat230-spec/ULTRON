# ULTRON Windows Desktop App

ULTRON artık geliştirme sırasında Vite + Python ile çalışmaya devam eder; dağıtımda ise Electron masaüstü kabuğu içinde açılır.

## Mimari

- Electron: masaüstü pencere ve uygulama yaşam döngüsü.
- React/Vite: mevcut ULTRON arayüzü.
- Three.js: mevcut 3D avatar.
- Python/aiohttp: mevcut ULTRON motoru.
- PyInstaller: Python backend'i bağımsız Windows executable haline getirir.
- Electron Forge + Squirrel: Windows kurulum paketi üretir.

Electron, JavaScript/HTML/CSS tabanlı masaüstü uygulamaları için kullanılır. PyInstaller ise Python uygulamasını Python kurulumu gerektirmeden çalışabilecek bir pakete dönüştürür.

## Geliştirme

```powershell
cd frontend
npm install
npm run build

cd ..\desktop
npm install
npm start
```

Electron, backend için geliştirme ortamında `python backend/server.py` çalıştırır. Frontend build'i localhost üzerinde küçük bir yerel HTTP sunucusundan açıldığı için mevcut `/api` ve `/ws` yolları değişmeden çalışır.

## Windows installer

PowerShell:

```powershell
.\scripts\build_desktop.ps1
```

Çıktı `desktop/out` altında oluşur. Son kullanıcı için hedeflenen çıktı `ULTRON-Setup.exe` kurulum paketidir.

## Notlar

- Backend yalnızca `127.0.0.1` üzerinde dinlenir.
- Electron renderer'da `nodeIntegration` kapalı, `contextIsolation` ve sandbox açık tutulur.
- Üretim paketinde frontend build ve PyInstaller backend Electron kaynaklarına eklenir.
- İlk paketleme sonrasında Ollama/model varlıkları ayrıca paketlenmesi veya kullanıcı makinesindeki Ollama'ya bağlanması gereken bir dağıtım kararıdır.
