const { app, BrowserWindow, ipcMain, dialog } = require("electron");
const { spawn } = require("child_process");
const path = require("path");

// 파이썬 스크립트 경로 — 프로그램 확정 후 교체
const PYTHON_SCRIPT = path.join(__dirname, "..", "analyze.py");

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  win.loadFile(path.join(__dirname, "dashboard", "index.html"));
}

ipcMain.handle("select-folder", async () => {
  const result = await dialog.showOpenDialog({ properties: ["openDirectory"] });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle("analyze-videos", (_, payload) => {
  return new Promise((resolve, reject) => {
    const proc = spawn("python3", [PYTHON_SCRIPT, payload.videoDirectory], {
      stdio: "inherit"
    });
    proc.on("close", (code) => {
      if (code === 0) resolve({ ok: true });
      else reject(new Error(`Python exited with code ${code}`));
    });
    proc.on("error", reject);
  });
});

app.whenReady().then(createWindow);

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
