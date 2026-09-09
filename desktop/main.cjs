const { app, BrowserWindow, dialog } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

let backend = null;
let ollama = null;
let backendOwned = false;

function projectRoot() {
  return path.resolve(__dirname, '..');
}

function runtimeRoot() {
  if (app.isPackaged) return path.join(process.resourcesPath, 'runtime');
  return projectRoot();
}

function backendDir() {
  return path.join(runtimeRoot(), 'backend');
}

function pythonCommand() {
  const venvPython = path.join(backendDir(), '.venv', 'Scripts', 'python.exe');
  if (fs.existsSync(venvPython)) return venvPython;
  return 'python';
}

function findOllama() {
  const candidates = [
    process.env.OLLAMA_EXE,
    process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, 'Programs', 'Ollama', 'ollama.exe'),
    process.env.ProgramFiles && path.join(process.env.ProgramFiles, 'Ollama', 'ollama.exe'),
    process.env['ProgramFiles(x86)'] && path.join(process.env['ProgramFiles(x86)'], 'Ollama', 'ollama.exe')
  ].filter(Boolean);
  return candidates.find((p) => fs.existsSync(p)) || 'ollama.exe';
}

async function ollamaReady() {
  try {
    const response = await fetch('http://127.0.0.1:11434/api/tags');
    if (!response.ok) return false;
    const data = await response.json();
    return Array.isArray(data.models);
  } catch (_) {
    return false;
  }
}

async function ensureOllama(timeoutMs = 20000) {
  if (await ollamaReady()) return true;

  const command = findOllama();
  try {
    ollama = spawn(command, ['serve'], {
      windowsHide: true,
      stdio: 'ignore',
      detached: false,
      env: { ...process.env }
    });
    ollama.on('error', () => {});
  } catch (_) {
    return false;
  }

  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await ollamaReady()) return true;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return false;
}

async function backendReady() {
  try {
    const response = await fetch('http://127.0.0.1:8000/api/system/health');
    if (!response.ok) return false;
    const data = await response.json();
    return data.runtime === 'online' && data.ollama === 'connected';
  } catch (_) {
    return false;
  }
}

function startBackend() {
  const dir = backendDir();
  if (!fs.existsSync(path.join(dir, 'server.py'))) {
    throw new Error(`Backend bulunamadı: ${dir}`);
  }

  backend = spawn(pythonCommand(), ['-B', 'server.py'], {
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
  backendOwned = true;

  backend.on('error', (err) => {
    dialog.showErrorBox('ULTRON Backend', `Backend başlatılamadı.\n\n${err.message}`);
  });

  backend.on('exit', () => {
    backend = null;
    backendOwned = false;
  });
}

async function ensureBackend(timeoutMs = 25000) {
  if (await backendReady()) return true;
  if (backend && !backend.killed) return false;

  startBackend();

  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await backendReady()) return true;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return false;
}

function stopOwnedBackend() {
  if (backendOwned && backend && !backend.killed) {
    backend.kill();
  }
  backend = null;
  backendOwned = false;
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

  if (!(await ensureOllama())) {
    dialog.showErrorBox('ULTRON', 'Ollama 20 saniye içinde hazır olmadı. Ollama kurulumunu veya OLLAMA_EXE yolunu kontrol edin.');
    return win;
  }

  if (!(await ensureBackend())) {
    dialog.showErrorBox('ULTRON', 'ULTRON backend 25 saniye içinde hazır olmadı. Python/paketlenmiş backend ortamını kontrol edin.');
    return win;
  }

  const indexFile = path.join(runtimeRoot(), 'frontend', 'dist', 'index.html');
  if (!fs.existsSync(indexFile)) {
    dialog.showErrorBox('ULTRON', 'Desktop arayüzü paket içinde bulunamadı. Installer build adımını tekrar çalıştırın.');
    return win;
  }

  await win.loadFile(indexFile);
  return win;
}

app.whenReady().then(async () => {
  if (!app.requestSingleInstanceLock()) {
    app.quit();
    return;
  }

  try {
    fs.mkdirSync(path.join(app.getPath('userData'), 'workspace'), { recursive: true });
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
  stopOwnedBackend();
  // Ollama is intentionally left running; it may be shared by other local AI apps.
  ollama = null;
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  stopOwnedBackend();
  // Never terminate Ollama here. ULTRON may have started it, but Ollama can be shared system-wide.
  ollama = null;
});
