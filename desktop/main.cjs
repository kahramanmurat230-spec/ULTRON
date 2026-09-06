"use strict";

const { app, BrowserWindow, dialog, shell } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const net = require("net");
const path = require("path");

let backend = null;
let mainWindow = null;
let quitting = false;

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function configuredPort() {
  const value = Number.parseInt(process.env.ULTRON_PORT || "", 10);
  return Number.isInteger(value) && value > 0 && value < 65536 ? value : null;
}

function findFreePort() {
  const requested = configuredPort();
  if (requested) return Promise.resolve(requested);
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const address = probe.address();
      probe.close((error) => error ? reject(error) : resolve(address.port));
    });
  });
}

function getJson(url) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout: 2000 }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => { body += chunk; });
      res.on("end", () => {
        if (res.statusCode !== 200) return reject(new Error(`health returned HTTP ${res.statusCode}`));
        try { resolve(JSON.parse(body)); } catch (error) { reject(error); }
      });
    });
    req.on("timeout", () => req.destroy(new Error("health timeout")));
    req.on("error", reject);
  });
}

async function waitForBackend(port, timeoutMs = 30000) {
  const url = `http://127.0.0.1:${port}/api/system/health`;
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    if (backend && backend.exitCode !== null) {
      throw new Error(`Backend exited during startup (code ${backend.exitCode}).`);
    }
    try {
      const health = await getJson(url);
      return { health, url };
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  throw new Error(`Backend health check timed out: ${lastError?.message || "unknown error"}`);
}

function desktopPaths() {
  const userData = ensureDir(app.getPath("userData"));
  const resources = app.isPackaged ? process.resourcesPath : path.resolve(__dirname, "..");
  return {
    data: ensureDir(path.join(userData, "backend-data")),
    workspace: ensureDir(process.env.ULTRON_WORKSPACE || path.join(app.getPath("documents"), "ULTRON Workspace")),
    frontend: app.isPackaged ? path.join(resources, "frontend") : path.join(resources, "frontend", "dist"),
    backendDir: app.isPackaged ? path.join(resources, "backend") : path.join(resources, "backend"),
  };
}

function startBackend(port, paths) {
  const packagedExecutable = path.join(paths.backendDir, "ULTRON-Backend.exe");
  const command = app.isPackaged ? packagedExecutable : (process.env.ULTRON_PYTHON || "python");
  const args = app.isPackaged ? [] : [path.join(paths.backendDir, "server.py")];
  if (app.isPackaged && !fs.existsSync(packagedExecutable)) {
    throw new Error(`Packaged backend missing: ${packagedExecutable}`);
  }
  if (!fs.existsSync(path.join(paths.frontend, "index.html"))) {
    throw new Error(`Production Cockpit missing: ${paths.frontend}`);
  }

  const env = {
    ...process.env,
    PYTHONUTF8: "1",
    ULTRON_PORT: String(port),
    ULTRON_BIND_HOST: "127.0.0.1",
    ULTRON_DATA_DIR: paths.data,
    ULTRON_MEMORY_PATH: path.join(paths.data, "memory", "ultron.db"),
    ULTRON_WORKSPACE: paths.workspace,
    ULTRON_FRONTEND_DIST: paths.frontend,
  };
  backend = spawn(command, args, {
    cwd: paths.data,
    env,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  backend.stdout.on("data", (line) => console.log(`[backend] ${String(line).trimEnd()}`));
  backend.stderr.on("data", (line) => console.error(`[backend] ${String(line).trimEnd()}`));
  backend.once("error", (error) => console.error("Backend launch error:", error));
}

function stopBackend() {
  return new Promise((resolve) => {
    const child = backend;
    backend = null;
    if (!child || child.exitCode !== null) return resolve();
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      resolve();
    };
    child.once("exit", finish);
    // Python/aiohttp receives SIGTERM and runs its shutdown/cleanup hooks.
    child.kill("SIGTERM");
    // A forced tree termination is only the fallback for a hung child.
    setTimeout(() => {
      if (child.exitCode !== null) return finish();
      if (process.platform === "win32") {
        spawn("taskkill", ["/pid", String(child.pid), "/t", "/f"], { windowsHide: true })
          .once("close", finish);
      } else {
        child.kill("SIGKILL");
        finish();
      }
    }, 5000).unref();
  });
}

function writeE2EReport(report) {
  const reportPath = process.env.ULTRON_DESKTOP_TEST_READY_FILE;
  if (reportPath) fs.writeFileSync(reportPath, JSON.stringify(report, null, 2), "utf8");
}

async function createCockpit(port, health) {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1024,
    minHeight: 700,
    show: false,
    backgroundColor: "#050508",
    title: "ULTRON",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) void shell.openExternal(url);
    return { action: "deny" };
  });
  mainWindow.once("ready-to-show", () => mainWindow?.show());
  await mainWindow.loadURL(`http://127.0.0.1:${port}/cockpit`);
  writeE2EReport({ backendHealthy: true, frontendLoaded: true, health });
  if (process.env.ULTRON_DESKTOP_TEST_EXIT_AFTER_LOAD === "1") {
    setTimeout(() => app.quit(), 250).unref();
  }
}

function showStartupError(error) {
  writeE2EReport({ backendHealthy: false, frontendLoaded: false, error: error.message });
  mainWindow = new BrowserWindow({ width: 720, height: 360, backgroundColor: "#050508" });
  const body = String(error.message).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  void mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(
    `<main style="font-family:Segoe UI,sans-serif;background:#050508;color:#d7dde6;padding:36px"><h1 style="color:#ff2438">ULTRON başlatılamadı</h1><p>Backend health check başarısız oldu.</p><pre>${body}</pre></main>`
  )}`);
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
  app.whenReady().then(async () => {
    try {
      const port = await findFreePort();
      const paths = desktopPaths();
      startBackend(port, paths);
      const { health } = await waitForBackend(port);
      await createCockpit(port, health);
    } catch (error) {
      console.error(error);
      showStartupError(error instanceof Error ? error : new Error(String(error)));
    }
  });
}

app.on("window-all-closed", () => app.quit());
app.on("before-quit", (event) => {
  if (quitting || !backend) return;
  event.preventDefault();
  quitting = true;
  stopBackend().finally(() => app.quit());
});
app.on("will-quit", () => { void stopBackend(); });
