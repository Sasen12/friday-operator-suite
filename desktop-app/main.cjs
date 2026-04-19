const { app, BrowserWindow, ipcMain, shell } = require('electron');
const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const net = require('net');
const readline = require('readline');

const repoRoot = path.resolve(__dirname, '..');
const runScript = path.join(repoRoot, 'run-friday.ps1');
const serverPort = 8000;
// Launch the Python modules directly so Windows does not contend with uv-generated console-script shims.
const serverLauncherArgs = ['run', 'python', 'server.py'];
const voiceConsoleLauncherArgs = ['run', 'python', 'local_friday.py', 'console'];
const voicePlaygroundLauncherArgs = ['run', 'python', 'local_friday.py'];

function readEnvFile(filePath) {
  if (!fs.existsSync(filePath)) {
    return {};
  }

  const values = {};
  const content = fs.readFileSync(filePath, 'utf8');

  for (const line of content.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) {
      continue;
    }

    const separator = trimmed.indexOf('=');
    if (separator === -1) {
      continue;
    }

    const key = trimmed.slice(0, separator).trim();
    if (!key) {
      continue;
    }

    let value = trimmed.slice(separator + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    values[key] = value;
  }

  return values;
}

const repoEnv = {
  ...readEnvFile(path.join(repoRoot, '.env')),
  ...readEnvFile(path.join(repoRoot, '.env.local')),
};

let mainWindow = null;
let serverProcess = null;
let voiceProcess = null;
let autoStartQueued = false;
let state = {
  mode: 'idle',
  server: 'stopped',
  voice: 'stopped',
  voiceLab: 'closed',
  runtime: {
    speech: 'local',
    sttModel: process.env.FRIDAY_LOCAL_STT_MODEL || repoEnv.FRIDAY_LOCAL_STT_MODEL || 'base.en',
    ttsModel: process.env.FRIDAY_LOCAL_TTS_MODEL || repoEnv.FRIDAY_LOCAL_TTS_MODEL || 'en_US-lessac-medium',
    llmProvider: process.env.FRIDAY_LLM_PROVIDER || repoEnv.FRIDAY_LLM_PROVIDER || 'ollama',
    llmModel: process.env.OLLAMA_LLM_MODEL || repoEnv.OLLAMA_LLM_MODEL || 'gemma4',
  },
  pythonPath: '',
  syncing: false,
};

function sendState() {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return;
  }
  mainWindow.webContents.send('friday:state', state);
}

function sendLog(source, message, level = 'info') {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return;
  }
  mainWindow.webContents.send('friday:log', {
    source,
    level,
    message: String(message).replace(/\r?\n$/, ''),
    timestamp: new Date().toISOString(),
  });
}

function sendVoiceActivity(kind, message) {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return;
  }
  mainWindow.webContents.send('friday:voice-activity', {
    kind,
    message: String(message || ''),
    timestamp: new Date().toISOString(),
  });
}

function classifyVoiceActivity(line) {
  const text = String(line || '').trim();
  if (!text) {
    return null;
  }

  const lower = text.toLowerCase();
  const ignorePatterns = [
    /^(\d{2}:\d{2}:\d{2}(?:\.\d{3})?)?\s*(debug|info|warn|warning|error)\b/i,
    /\blivekit\.agents\b/i,
    /\bfriday-agent\b/i,
    /\basyncio\b/i,
    /\bstarting worker\b/i,
    /\bhttp server listening\b/i,
    /\bjob runner\b/i,
    /\busing proactor\b/i,
    /\bdeprecated\b/i,
    /\bmcp server url\b/i,
    /\busing audio io\b/i,
    /\busing transcript io\b/i,
    /\bttf\b/i,
    /\btts\b/i,
    /\bstt\b/i,
  ];

  if (ignorePatterns.some((pattern) => pattern.test(text))) {
    return null;
  }

  if (/^\s*(you|user)\b/i.test(text)) {
    return { kind: 'listening', message: text };
  }

  if (/^\s*(agent|assistant|friday)\b/i.test(text)) {
    return { kind: 'speaking', message: text };
  }

  return { kind: 'speaking', message: text };
}

function setState(patch) {
  state = { ...state, ...patch };
  sendState();
}

function isWindowsAmd64Python(pythonPath) {
  if (!pythonPath || !fs.existsSync(pythonPath)) {
    return false;
  }
  try {
    const result = spawnSync(pythonPath, ['-c', 'import sysconfig; print(sysconfig.get_platform())'], {
      cwd: repoRoot,
      encoding: 'utf8',
      timeout: 10_000,
      windowsHide: true,
    });
    return result.status === 0 && String(result.stdout || '').trim().toLowerCase() === 'win-amd64';
  } catch {
    return false;
  }
}

function findX64PythonPath() {
  const envPython = process.env.UV_PYTHON;
  if (envPython && isWindowsAmd64Python(envPython)) {
    return envPython;
  }

  const candidates = [
    path.join(repoRoot, '.venv', 'Scripts', 'python.exe'),
    path.join(process.env.LOCALAPPDATA || '', 'Python', 'pythoncore-3.14-64', 'python.exe'),
    path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Python', 'Python314', 'python.exe'),
    path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Python', 'Python312', 'python.exe'),
  ];

  for (const candidate of candidates) {
    if (isWindowsAmd64Python(candidate)) {
      return candidate;
    }
  }

  const py = spawnSync('py', ['-0p'], {
    cwd: repoRoot,
    encoding: 'utf8',
    timeout: 10_000,
    windowsHide: true,
  });
  if (py.status === 0) {
    for (const line of String(py.stdout || '').split(/\r?\n/)) {
      const match = line.match(/([A-Za-z]:.*python\.exe)\s*$/);
      if (!match) {
        continue;
      }
      if (isWindowsAmd64Python(match[1])) {
        return match[1];
      }
    }
  }

  return '';
}

function waitForPort(port, timeoutMs = 60_000) {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + timeoutMs;

    const attempt = () => {
      const socket = net.createConnection({ host: '127.0.0.1', port }, () => {
        socket.end();
        resolve();
      });

      socket.on('error', () => {
        socket.destroy();
        if (Date.now() >= deadline) {
          reject(new Error(`Timed out waiting for port ${port}`));
          return;
        }
        setTimeout(attempt, 500);
      });
    };

    attempt();
  });
}

function spawnUv(args, label, extraEnv = {}) {
  const pythonPath = state.pythonPath || findX64PythonPath();
  if (!pythonPath) {
    throw new Error('Could not find a 64-bit Python interpreter for UV.');
  }

  const child = spawn('uv', args, {
    cwd: repoRoot,
    env: {
      ...process.env,
      UV_PYTHON: pythonPath,
      PYTHONUTF8: '1',
      PYTHONIOENCODING: 'utf-8',
      ...extraEnv,
    },
    shell: false,
    windowsHide: true,
    stdio: ['pipe', 'pipe', 'pipe'],
  });

  const forward = (stream, level) => {
    const rl = readline.createInterface({ input: stream });
    rl.on('line', (line) => {
      sendLog(label, line, level);
      if (label === 'voice') {
        const activity = classifyVoiceActivity(line);
        if (activity) {
          sendVoiceActivity(activity.kind, activity.message);
        }
      }
    });
  };

  forward(child.stdout, 'info');
  forward(child.stderr, 'warn');

  child.on('exit', (code, signal) => {
    sendLog(label, `exited with code ${code ?? 'null'} signal ${signal ?? 'none'}`, code === 0 ? 'info' : 'error');
    if (label === 'server') {
      serverProcess = null;
      setState({ server: 'stopped' });
    }
    if (label === 'voice') {
      voiceProcess = null;
      setState({ voice: 'stopped', voiceLab: 'closed' });
    }
  });

  child.on('error', (error) => {
    sendLog(label, error.message, 'error');
  });

  return child;
}

async function startConsoleDeck() {
  await stopAll({ quiet: true });
  setState({ mode: 'console', syncing: false, voiceLab: 'closed' });

  const pythonPath = findX64PythonPath();
  if (!pythonPath) {
    throw new Error('No 64-bit Python interpreter found. Run the PowerShell launcher once first if needed.');
  }
  state.pythonPath = pythonPath;
  sendState();

  try {
    serverProcess = spawnUv(serverLauncherArgs, 'server', { FRIDAY_WAKE_WORD_MODE: '0' });
    setState({ server: 'starting' });
    sendLog('launcher', `Using Python: ${pythonPath}`);
    sendLog('launcher', 'Starting FRIDAY server...');
    await waitForPort(serverPort, 60_000);
    setState({ server: 'online' });
    sendLog('launcher', 'Starting FRIDAY voice console...');

    voiceProcess = spawnUv(voiceConsoleLauncherArgs, 'voice', { FRIDAY_WAKE_WORD_MODE: '0' });
    setState({ voice: 'starting' });
  } catch (error) {
    sendLog('launcher', error.message, 'error');
    await stopAll({ quiet: true });
    throw error;
  }
}

async function startPlayground() {
  await stopAll({ quiet: true });
  setState({ mode: 'local', syncing: false, voiceLab: 'opening' });

  try {
    const pythonPath = findX64PythonPath();
    if (!pythonPath) {
      throw new Error('No 64-bit Python interpreter found. Run the PowerShell launcher once first if needed.');
    }
    state.pythonPath = pythonPath;
    sendState();
    sendLog('launcher', `Using Python: ${pythonPath}`);

    serverProcess = spawnUv(serverLauncherArgs, 'server', { FRIDAY_WAKE_WORD_MODE: '0' });
    setState({ server: 'starting' });
    sendLog('launcher', 'Starting FRIDAY server for local voice...');
    await waitForPort(serverPort, 60_000);
    setState({ server: 'online' });
    sendLog('launcher', 'Starting FRIDAY local voice runtime...');

    voiceProcess = spawnUv(voicePlaygroundLauncherArgs, 'voice', { FRIDAY_WAKE_WORD_MODE: '1' });
    setState({ voice: 'starting', voiceLab: 'open' });
    sendLog('voice', 'Local voice runtime is ready inside the desktop app.', 'info');
  } catch (error) {
    sendLog('launcher', error.message, 'error');
    await stopAll({ quiet: true });
    throw error;
  }
}

async function stopAll(options = {}) {
  const quiet = Boolean(options.quiet);
  const processes = [
    ['voice', voiceProcess],
    ['server', serverProcess],
  ];

  for (const [label, child] of processes) {
    if (!child) {
      continue;
    }
    if (!quiet) {
      sendLog('launcher', `Stopping ${label}...`);
    }
    try {
      child.stdin?.end();
    } catch {}
    try {
      child.kill();
    } catch {}
    try {
      spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], {
        cwd: repoRoot,
        windowsHide: true,
        stdio: 'ignore',
      });
    } catch {}
  }

  serverProcess = null;
  voiceProcess = null;
  setState({ mode: 'idle', server: 'stopped', voice: 'stopped', voiceLab: 'closed' });
}

function sendConsoleInput(text) {
  const value = String(text || '').trimEnd();
  if (!value) {
    return false;
  }
  if (!voiceProcess || voiceProcess.killed) {
    throw new Error('Voice console is not running.');
  }
  voiceProcess.stdin.write(`${value}\n`);
  sendLog('you', value, 'info');
  return true;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1600,
    height: 1024,
    minWidth: 1280,
    minHeight: 860,
    backgroundColor: '#050816',
    frame: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, 'preload.cjs'),
    },
  });

  mainWindow.removeMenu();
  mainWindow.webContents.on('did-finish-load', () => {
    sendState();
  });
  mainWindow.loadFile(path.join(__dirname, 'renderer.html'));
  mainWindow.once('ready-to-show', () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.maximize();
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
    autoStartQueued = false;
  });
}

function queueAutoStart() {
  if (autoStartQueued) {
    return;
  }
  autoStartQueued = true;

  const launch = async () => {
    if (!mainWindow || mainWindow.isDestroyed()) {
      autoStartQueued = false;
      return;
    }
    if (serverProcess || voiceProcess || state.mode !== 'idle') {
      return;
    }

    try {
      sendLog('launcher', 'Auto-starting FRIDAY server and voice...', 'info');
      await startPlayground();
    } catch (error) {
      sendLog('launcher', `Autostart failed: ${error.message}`, 'error');
      autoStartQueued = false;
    }
  };

  if (mainWindow && !mainWindow.isDestroyed()) {
    const runLaunch = () => {
      setTimeout(() => {
        launch().catch((error) => {
          sendLog('launcher', `Autostart failed: ${error.message}`, 'error');
          autoStartQueued = false;
        });
      }, 250);
    };

    if (mainWindow.webContents.isLoadingMainFrame()) {
      mainWindow.webContents.once('did-finish-load', runLaunch);
    } else {
      runLaunch();
    }
  } else {
    setTimeout(() => {
      launch().catch((error) => {
        sendLog('launcher', `Autostart failed: ${error.message}`, 'error');
        autoStartQueued = false;
      });
    }, 250);
  }
}

app.whenReady().then(() => {
  createWindow();
  queueAutoStart();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
      queueAutoStart();
    }
  });
});

app.on('window-all-closed', async () => {
  await stopAll({ quiet: true });
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

ipcMain.handle('friday:start-console', async () => {
  await startConsoleDeck();
  return state;
});

ipcMain.handle('friday:start-playground', async () => {
  startPlayground();
  return state;
});

ipcMain.handle('friday:stop-all', async () => {
  await stopAll();
  return state;
});

ipcMain.handle('friday:send-input', async (_event, text) => {
  return sendConsoleInput(text);
});

ipcMain.handle('friday:open-external', async (_event, url) => {
  await shell.openExternal(url);
  return true;
});

ipcMain.handle('friday:pick-browser', async () => {
  const firefox = path.join('C:\\Program Files\\Mozilla Firefox\\firefox.exe');
  if (fs.existsSync(firefox)) {
    const child = spawn(firefox, [], {
      cwd: repoRoot,
      shell: false,
      windowsHide: true,
      detached: true,
      stdio: 'ignore',
    });
    child.unref();
    return firefox;
  }
  return '';
});

ipcMain.handle('friday:window-minimize', async () => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.minimize();
  }
  return true;
});

ipcMain.handle('friday:window-maximize', async () => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    if (mainWindow.isMaximized()) {
      mainWindow.unmaximize();
    } else {
      mainWindow.maximize();
    }
  }
  return true;
});

ipcMain.handle('friday:window-close', async () => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.close();
  }
  return true;
});
