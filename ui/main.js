const { app, BrowserWindow, ipcMain, dialog, shell } = require("electron");
const { spawn } = require("child_process");
const path = require("path");

const PYTHON_SCRIPT = path.join(__dirname, "..", "analyze.py");
const isMock = process.argv.includes("--mock");

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

ipcMain.handle("select-raw-data-folder", async () => {
  const result = await dialog.showOpenDialog({ properties: ["openDirectory"] });
  return result.canceled ? null : result.filePaths[0];
});

function analyzeVideosMock(event) {
  return new Promise((resolve) => {
    setTimeout(() => {
      const result = { resultDir: path.join(__dirname, "mock", "results") };
      event.sender.send("analysis-complete", result);
      resolve({ ok: true });
    }, 1500);
  });
}

function analyzeVideosReal(event, payload) {
  return new Promise((resolve, reject) => {
    const proc = spawn("python3", [PYTHON_SCRIPT, payload.videoDirectory], {
      stdio: ["inherit", "pipe", "inherit"]
    });

    let output = "";
    proc.stdout.on("data", (chunk) => { output += chunk; });

    proc.on("close", (code) => {
      if (code === 0) {
        let result = { ok: true };
        try { result = JSON.parse(output.trim()); } catch (_) {}
        event.sender.send("analysis-complete", result);
        resolve(result);
      } else {
        reject(new Error(`Python exited with code ${code}`));
      }
    });
    proc.on("error", reject);
  });
}

ipcMain.handle("analyze-videos", (event, payload) =>
  isMock ? analyzeVideosMock(event) : analyzeVideosReal(event, payload)
);

ipcMain.handle("open-analysis-result-folder", (_, folderPath) => shell.openPath(folderPath));

app.whenReady().then(createWindow);

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
