// electron/python.js — manages the Python (uvicorn) backend process.
//
// Responsibilities:
//   • locate a usable Python interpreter (bundled in production, system in dev)
//   • spawn `uvicorn dashboard.server:app` on 127.0.0.1:8000
//   • wait until the server answers before the UI is shown
//   • surface a friendly dialog (with a python.org link) if Python is missing
//   • kill the child cleanly on quit so no orphan uvicorn is left behind

const { spawn, spawnSync } = require('child_process');
const { app, dialog, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');

const HOST = process.env.NEAMT_HOST || '127.0.0.1';
const PORT = Number(process.env.NEAMT_PORT) || 8000;
const STARTUP_TIMEOUT_MS = 30_000;

let pyProc = null;

// The directory that contains the `dashboard` and `neamt` Python packages.
// In a packaged build these are copied into Resources/app via extraResources;
// in development they live one level up from electron/.
function appRoot() {
  if (app.isPackaged) return path.join(process.resourcesPath, 'app');
  return path.join(__dirname, '..');
}

// Pick a Python interpreter. Preference order:
//   1. a Python bundled inside the .app/.exe (production)
//   2. the project virtualenv (development convenience)
//   3. the system interpreter on PATH (python3 / python)
function findPython() {
  const root = appRoot();
  const win = process.platform === 'win32';
  const candidates = win
    ? [
        path.join(root, 'python', 'python.exe'),
        path.join(root, '.venv', 'Scripts', 'python.exe'),
      ]
    : [
        path.join(root, 'python', 'bin', 'python3'),
        path.join(root, '.venv', 'bin', 'python3'),
      ];

  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return win ? 'python' : 'python3';
}

// True if the interpreter exists and runs.
function pythonWorks(py) {
  try {
    const r = spawnSync(py, ['--version'], { stdio: 'ignore' });
    return r.status === 0;
  } catch {
    return false;
  }
}

function showPythonMissingDialog() {
  return dialog
    .showMessageBox({
      type: 'error',
      title: 'Python is required',
      message: 'Neamt needs Python 3.11 or newer to run its engine.',
      detail:
        'We could not find a Python interpreter on your system. Install Python ' +
        'from python.org, then reopen Neamt.',
      buttons: ['Download Python', 'Quit'],
      defaultId: 0,
      cancelId: 1,
    })
    .then((res) => {
      if (res.response === 0) shell.openExternal('https://www.python.org/downloads/');
      app.quit();
    });
}

// Start uvicorn. Returns false (and shows a dialog) if Python is unavailable.
function startPython() {
  const py = findPython();
  if (!pythonWorks(py)) {
    showPythonMissingDialog();
    return false;
  }

  const root = appRoot();
  pyProc = spawn(
    py,
    ['-m', 'uvicorn', 'dashboard.server:app', '--host', HOST, '--port', String(PORT)],
    {
      cwd: root,
      env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONPATH: root },
      stdio: ['ignore', 'pipe', 'pipe'],
    }
  );

  pyProc.stdout.on('data', (d) => process.stdout.write(`[neamt-py] ${d}`));
  pyProc.stderr.on('data', (d) => process.stderr.write(`[neamt-py] ${d}`));
  pyProc.on('exit', (code, signal) => {
    console.log(`[neamt-py] backend exited (code=${code} signal=${signal})`);
    pyProc = null;
  });

  return true;
}

// Single health probe against the dashboard.
function ping() {
  return new Promise((resolve) => {
    const req = http.get(
      { host: HOST, port: PORT, path: '/api/system', timeout: 1500 },
      (res) => {
        res.resume();
        resolve(res.statusCode > 0 && res.statusCode < 500);
      }
    );
    req.on('error', () => resolve(false));
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
  });
}

// Poll until the server answers or the timeout elapses.
async function waitForServer(timeoutMs = STARTUP_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (pyProc === null) return false; // backend crashed during boot
    if (await ping()) return true;
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

// Terminate the backend. SIGTERM first, SIGKILL if it lingers.
function stopPython() {
  if (!pyProc) return;
  const proc = pyProc;
  try {
    proc.kill('SIGTERM');
  } catch {
    /* already gone */
  }
  setTimeout(() => {
    try {
      proc.kill('SIGKILL');
    } catch {
      /* already gone */
    }
  }, 2500);
  pyProc = null;
}

module.exports = { startPython, stopPython, waitForServer, HOST, PORT };
