const { app, BrowserWindow } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const path = require("path");

const SERVER_URL = "http://127.0.0.1:8765";
const SERVER_ENTRY = ["-m", "manager.control"];
const PROJECT_ROOT = path.join(__dirname, "..");
const isMock = process.argv.includes("--mock");

let serverProcess = null;

function startServer() {
  serverProcess = spawn("python3", SERVER_ENTRY, {
    cwd: PROJECT_ROOT,
    env: {
      ...process.env,
      LOVEBUG_UI_MOCK: isMock ? "1" : ""
    },
    stdio: "inherit"
  });
  serverProcess.on("error", (err) => {
    console.error("FastAPI 서버 실행 실패:", err);
  });
}

function waitForServer(url, retries = 30, intervalMs = 300) {
  return new Promise((resolve, reject) => {
    let attempts = 0;
    const check = () => {
      http.get(`${url}/dashboard/health`, (res) => {
        if (res.statusCode === 200) resolve();
        else retry();
      }).on("error", retry);
    };
    const retry = () => {
      if (++attempts >= retries) return reject(new Error("서버 시작 시간 초과"));
      setTimeout(check, intervalMs);
    };
    check();
  });
}

function createWindow() {
  const win = new BrowserWindow({ width: 1280, height: 800 });
  win.loadURL(`${SERVER_URL}/dashboard/`);
}

app.whenReady().then(async () => {
  startServer();
  try {
    await waitForServer(SERVER_URL);
  } catch (err) {
    console.error(err.message);
  }
  createWindow();
});

app.on("window-all-closed", () => {
  if (serverProcess) serverProcess.kill();
  if (process.platform !== "darwin") app.quit();
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
