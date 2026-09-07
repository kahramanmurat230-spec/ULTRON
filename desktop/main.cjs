const { app, BrowserWindow, dialog, shell } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

let backend = null;

function projectRoot() {
  return path.resolve(__dirname, '..');
}

function pythonCommand() {
  const root = projectRoot();
  const venvPython = path.join(root, 'backend', '.venv', 'Scripts', 'python.exe');
  if (fs.existsSync(venvPython)) return venvPython;
  return 'python';
}

function startBackend() {
  const root = projectRoot();
  const backendDir = path.join(root, 'backend');
  backend = spawn(pythonCommand(), ['-B', 'server.py'], {
    cwd: backendDir,
    windowsHide: true,
    stdio: 'ignore',
    env: { ...process.env }
  });
  backend.on('error', (err) => {
    dialog.showErrorBox('ULTRON Backend', `Backend başlatılamadı.\n\n${err.message}`);
  });
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
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    backgroundColor: '#05070b',
    title: 'ULTRON',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  const ready = await waitForBackend();
  if (!ready) {
    dialog.showErrorBox('ULTRON', 'ULTRON backend 20 saniye içinde hazır olmadı. Ollama ve Python ortamını kontrol edin.');
    return win;
  }

  const indexFile = path.join(projectRoot(), 'frontend', 'dist', 'index.html');
  if (!fs.existsSync(indexFile)) {
    dialog.showErrorBox('ULTRON', 'Desktop arayüzü henüz build edilmemiş. Önce frontend\'i build edin.');
    return win;
  }

  await win.loadFile(indexFile);
  return win;
}

app.whenReady().then(async () => {
  startBackend();
  await createWindow();
  app.on('activate', async () => {
    if (BrowserWindow.getAllWindows().length === 0) await createWindow();
  });
});

app.on('window-all-closed', () => {
  if (backend && !backend.killed) backend.kill();
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  if (backend && !backend.killed) backend.kill();
});
