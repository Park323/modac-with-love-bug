const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("LovebugBridge", {
  selectRawDataFolder: () => ipcRenderer.invoke("select-raw-data-folder"),
  analyzeVideos: (payload) => ipcRenderer.invoke("analyze-videos", payload),
  onAnalysisComplete: (cb) => ipcRenderer.on("analysis-complete", (_, data) => cb(data)),
  openAnalysisResultFolder: (folderPath) => ipcRenderer.invoke("open-analysis-result-folder", folderPath),
  openAnalysisArtifact: (resultDir, artifactPath) => ipcRenderer.invoke("open-analysis-artifact", resultDir, artifactPath),
  readFinalReport: (resultDir) => ipcRenderer.invoke("read-final-report", resultDir)
});
