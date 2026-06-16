// electron/preload.js — the only bridge between the renderer (React) and the
// Electron main process. Runs with contextIsolation: nothing here leaks Node
// into the page; we expose a tiny, explicit surface on window.electron.

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electron', {
  // Platform string ('darwin' | 'win32' | 'linux') — lets the UI decide where
  // to put the window controls (left on macOS, right on Windows).
  platform: process.platform,

  // App metadata (version, platform) resolved from the main process.
  getInfo: () => ipcRenderer.invoke('app:info'),

  // Window controls for the custom (frameless) title bar.
  window: {
    minimize: () => ipcRenderer.send('window:minimize'),
    maximize: () => ipcRenderer.send('window:maximize'),
    close: () => ipcRenderer.send('window:close'),
  },
});
