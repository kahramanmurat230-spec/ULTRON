const { app, BrowserWindow, dialog } = require('electron');
const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');
const path = require('path');
const url = require('url');

const BACKEND_PORT = Number(process.env.ULTRON_PORT || 8000);
let backend = null;
let staticServer = null;

function resourcePath(...parts) {
  return path.join(process.resourcesPath, ...parts);
}

function devPath(...parts) {
  return path.join(__dirname, '..', ...parts);
}

function getFrontendRoot() {
  const packaged = resourcePath('frontend-dist');
  if (fs.existsSync(packaged)) return packaged;
  return devPath('frontend', 'dist');
}

function getBackendCommand() {
  const packagedExe = resourcePath('backend', 'ULTRON_BACKEND.exe');
  if (fs.existsSync(packagedExe)) return { command: packagedExe, args: [] };
  return { command: process.env.PYTHON || 'python', args: [devPath('backend', 'server.py')] };
}

function startBackend() {
  const target = getBackendCommand();
  backend = spawn(target.command, target.args, {
    cwd: path.dirname(target.command),
    windowsHide: true,
    stdio: 'ignore',
    env: {
      ...process.env,
      ULTRON_BIND_HOST: '127.0.0.1',
      ULTRON_PORT: String(BACKEND_PORT),
      ULTRON_AUTH: process.env.ULTRON_AUTH || '0'
    }
  });
  backend.on('error', (err) => {
    dialog.showErrorBox('ULTRON backend başlatılamadı', err.message);
  });
}

function waitForBackend(timeoutMs = 30000) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const retry = () => {
      if (Date.now() - started > timeoutMs) return reject(new Error('ULTRON backend zaman aşımına uğradı.'));
      setTimeout(probe, 250);
    };
    const probe = () => {
      const req = http.get(`http://127.0.0.1:${BACKEND_PORT}/api/system`, (res) => {
        res.resume();
        if (res.statusCode >= 200 && res.statusCode < 500) return resolve();
        retry();
      });
      req.on('error', retry);
      req.setTimeout(1200, () => { req.destroy(); retry(); });
    };
    probe();
  });
}

function startStaticServer(root) {
  const rootAbs = path.resolve(root);
  return new Promise((resolve, reject) => {
    staticServer = http.createServer((req, res) => {
      const parsed = url.parse(req.url || '/');
      let pathname = decodeURIComponent(parsed.pathname || '/');
      if (pathname === '/') pathname = '/index.html';
      const relative = pathname.replace(/^[/\\]+/, '');
      const file = path.resolve(rootAbs, relative);
      if (file !== rootAbs && !file.startsWith(rootAbs + path.sep)) {
        res.writeHead(403);
        return res.end();
      }

      let target = file;
      if (!fs.existsSync(target) || fs.statSync(target).isDirectory()) target = path.join(rootAbs, 'index.html');
      fs.readFile(target, (err, data) => {
        if (err) return void (res.writeHead(404), res.end('Not found'));
        const ext = path.extname(target).toLowerCase();
        const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp', '.woff2': 'font/woff2' };
        res.writeHead(200, { 'Content-Type': types[ext] || 'application/octet-stream', 'Cache-Control': 'no-cache' });
        res.end(data);
      });
    });
    staticServer.on('error', reject);
    staticServer.listen(0, '127.0.0.1', () => {
      const port = staticServer.address().port;
      resolve(`http://127.0.0.1:${port}`);
    });
  });
}

async function createWindow() {
  const root = getFrontendRoot();
  const index = path.join(root, 'index.html');
  if (!fs.existsSync(index)) throw new Error('Frontend build bulunamadı. Önce frontend build alınmalıdır.');

  startBackend();
  await waitForBackend();
  const origin = await startStaticServer(root);

  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    title: 'ULTRON',
    backgroundColor: '#05050a',
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
    show: false
  });

  win.once('ready-to-show', () => win.show());
  await win.loadURL(origin);
}

app.whenReady().then(async () => {
  try {
    await createWindow();
  } catch (err) {
    dialog.showErrorBox('ULTRON başlatılamadı', String(err?.message || err));
    app.quit();
  }
});

app.on('window-all-closed', () => app.quit());
app.on('before-quit', () => {
  if (staticServer) staticServer.close();
  if (backend && !backend.killed) backend.kill();
});
