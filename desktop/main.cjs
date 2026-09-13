const { app, BrowserWindow, dialog } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

let backend = null;

function projectRoot() { return path.resolve(__dirname, '..'); }
function runtimeRoot() { return app.isPackaged ? path.join(process.resourcesPath, 'runtime') : projectRoot(); }
function backendDir() { return path.join(runtimeRoot(), 'backend'); }

function backendCommand() {
  // PyInstaller --onedir creates: backend/dist/ultron-backend/ultron-backend.exe
  const packagedBundleExe = path.join(backendDir(), 'dist', 'ultron-backend', 'ultron-backend.exe');
  const packagedFlatExe = path.join(backendDir(), 'dist', 'ultron-backend.exe');
  if (app.isPackaged && fs.existsSync(packagedBundleExe)) return { command: packagedBundleExe, args: [] };
  if (app.isPackaged && fs.existsSync(packagedFlatExe)) return { command: packagedFlatExe, args: [] };

  const venvPython = path.join(backendDir(), '.venv', 'Scripts', 'python.exe');
  if (fs.existsSync(venvPython)) return { command: venvPython, args: ['-B', 'server.py'] };
  return { command: 'python', args: ['-B', 'server.py'] };
}

function startBackend() {
  const dir = backendDir();
  if (!fs.existsSync(path.join(dir, 'server.py'))) throw new Error(`Backend bulunamadı: ${dir}`);
  const launcher = backendCommand();
  backend = spawn(launcher.command, launcher.args, {
    cwd: dir,
    windowsHide: true,
    stdio: 'ignore',
    env: {
      ...process.env,
      ULTRON_WORKSPACE: app.isPackaged
        ? path.join(app.getPath('userData'), 'workspace')
        : (process.env.ULTRON_WORKSPACE || projectRoot())
    }
  });
  backend.on('error', (err) => dialog.showErrorBox('ULTRON Backend', `Backend başlatılamadı.\n\n${err.message}`));
}

async function waitForBackend(timeoutMs = 20000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      const response = await fetch('http://127.0.0.1:8000/api/system');
      if (response.ok) return true;
    } catch (_) {}
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  return false;
}

async function createWindow() {
  const win = new BrowserWindow({
    width: 1440, height: 900, minWidth: 1100, minHeight: 700,
    backgroundColor: '#05070b', title: 'ULTRON', autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false }
  });
  if (!await waitForBackend()) {
    dialog.showErrorBox('ULTRON', 'ULTRON backend 20 saniye içinde hazır olmadı. Python veya paketlenmiş backend ortamını kontrol edin.');
    return win;
  }
  const indexFile = path.join(runtimeRoot(), 'frontend', 'dist', 'index.html');
  if (!fs.existsSync(indexFile)) {
    dialog.showErrorBox('ULTRON', 'Desktop arayüzü paket içinde bulunamadı. Frontend build adımını tekrar çalıştırın.');
    return win;
  }
  await win.loadFile(indexFile);
  return win;
}

app.whenReady().then(async () => {
  try {
    fs.mkdirSync(path.join(app.getPath('userData'), 'workspace'), { recursive: true });
    startBackend();
    await createWindow();
  } catch (err) {
    dialog.showErrorBox('ULTRON', `ULTRON başlatılamadı.\n\n${err.message}`);
    app.quit();
    return;
  }
  app.on('activate', async () => {
    if (BrowserWindow.getAllWindows().length === 0) await createWindow();
  });
});

app.on('window-all-closed', () => {
  if (backend && !backend.killed) backend.kill();
  if (process.platform !== 'darwin') app.quit();
});
app.on('before-quit', () => { if (backend && !backend.killed) backend.kill(); });
