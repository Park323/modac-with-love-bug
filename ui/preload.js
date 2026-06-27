const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("LovebugBridge", {
  selectFolder: () => ipcRenderer.invoke("select-folder"),
  analyzeVideos: (payload) => ipcRenderer.invoke("analyze-videos", payload)
});
