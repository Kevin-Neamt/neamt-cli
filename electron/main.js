// electron/main.js — Electron entry point for the Neamt desktop app.
//
// Boot sequence:
//   1. show a splash window
//   2. start the Python backend (uvicorn dashboard.server:app)
//   3. wait (≤30s) for http://127.0.0.1:8000 to answer
//   4. swap the splash for the main, frameless window pointed at the dashboard
//   5. on quit, kill the Python child so nothing is left running

const { app, BrowserWindow, Menu, ipcMain, dialog, shell, nativeImage } = require('electron');
const path = require('path');
const py = require('./python');

let mainWindow = null;
let splashWindow = null;

const BG = '#0c0c0f'; // matches the dashboard --bg-primary (dark)

// ── Windows ────────────────────────────────────────────────────────────────

function createSplash() {
  splashWindow = new BrowserWindow({
    width: 420,
    height: 300,
    frame: false,
    resizable: false,
    movable: false,
    center: true,
    backgroundColor: BG,
    show: true,
    webPreferences: { contextIsolation: true },
  });
  splashWindow.loadFile(path.join(__dirname, 'splash.html'));
}

function destroySplash() {
  if (splashWindow && !splashWindow.isDestroyed()) splashWindow.destroy();
  splashWindow = null;
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 1024,
    minHeight: 680,
    frame: false,
    titleBarStyle: 'hiddenInset', // inset traffic lights on macOS
    backgroundColor: BG,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.loadURL(`http://${py.HOST}:${py.PORT}`);

  mainWindow.once('ready-to-show', () => {
    destroySplash();
    mainWindow.show();
  });

  // Open external links in the default browser, not inside the app shell.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http')) shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// ── Native menu (basic, macOS-friendly) ──────────────────────────────────────

function buildMenu() {
  const isMac = process.platform === 'darwin';
  const template = [
    ...(isMac
      ? [
          {
            label: 'Neamt',
            submenu: [
              { role: 'about' },
              { type: 'separator' },
              { role: 'hide' },
              { role: 'hideOthers' },
              { role: 'unhide' },
              { type: 'separator' },
              { role: 'quit' },
            ],
          },
        ]
      : []),
    {
      label: 'File',
      submenu: [isMac ? { role: 'close' } : { role: 'quit' }],
    },
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'selectAll' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    {
      label: 'Window',
      submenu: [
        { role: 'minimize' },
        { role: 'zoom' },
        ...(isMac ? [{ type: 'separator' }, { role: 'front' }] : [{ role: 'close' }]),
      ],
    },
    {
      role: 'help',
      submenu: [
        {
          label: 'Neamt Website',
          click: () => shell.openExternal('https://neamt.ai'),
        },
        {
          label: 'Documentation',
          click: () => shell.openExternal('https://neamt.ai/docs'),
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ── IPC: window controls + app info ──────────────────────────────────────────

ipcMain.on('window:minimize', () => mainWindow && mainWindow.minimize());
ipcMain.on('window:maximize', () => {
  if (!mainWindow) return;
  if (mainWindow.isMaximized()) mainWindow.unmaximize();
  else mainWindow.maximize();
});
ipcMain.on('window:close', () => mainWindow && mainWindow.close());
ipcMain.handle('app:info', () => ({
  version: app.getVersion(),
  platform: process.platform,
}));

// ── Lifecycle ────────────────────────────────────────────────────────────────

app.whenReady().then(async () => {
  if (process.platform === 'darwin' && app.dock) {
    const icon = nativeImage.createFromPath(path.join(__dirname, '..', 'assets', 'icon.png'));
    if (!icon.isEmpty()) app.dock.setIcon(icon);
  }

  buildMenu();
  createSplash();

  if (!py.startPython()) return; // dialog shown, app quitting

  const ready = await py.waitForServer();
  if (!ready) {
    destroySplash();
    py.stopPython();
    dialog.showErrorBox(
      'Neamt could not start',
      'The Neamt engine did not respond within 30 seconds. Please try again, ' +
        'and make sure no other program is using port 8000.'
    );
    app.quit();
    return;
  }

  createMainWindow();
});

app.on('activate', () => {
  // macOS: re-create a window when the dock icon is clicked and none are open.
  if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  py.stopPython();
});
