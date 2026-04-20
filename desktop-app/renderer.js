const state = {
  mode: 'idle',
  server: 'stopped',
  voice: 'stopped',
  voiceLab: 'closed',
  pythonPath: '',
  drawerOpen: false,
  drawerAutoOpened: false,
  drawerUserClosed: false,
};

const orbTimers = {
  speaking: null,
  listening: null,
};

const elements = {
  orbButton: document.getElementById('orbButton'),
  chromeOrb: document.getElementById('chromeOrb'),
  drawer: document.getElementById('drawer'),
  drawerClose: document.getElementById('drawerClose'),
  modeBadge: document.getElementById('modeBadge'),
  serverBadge: document.getElementById('serverBadge'),
  voiceBadge: document.getElementById('voiceBadge'),
  voiceLabBadge: document.getElementById('voiceLabBadge'),
  runtimeSummary: document.getElementById('runtimeSummary'),
  voiceLabStatus: document.getElementById('voiceLabStatus'),
  chromeStatus: document.getElementById('chromeStatus'),
  clockTime: document.getElementById('clockTime'),
  clockDate: document.getElementById('clockDate'),
  logStream: document.getElementById('logStream'),
  commandInput: document.getElementById('commandInput'),
  inputForm: document.getElementById('inputForm'),
  startConsole: document.getElementById('startConsole'),
  restartVoice: document.getElementById('restartVoice'),
  stopAll: document.getElementById('stopAll'),
  pickBrowser: document.getElementById('pickBrowser'),
  clearConversation: document.getElementById('clearConversation'),
  extractConversation: document.getElementById('extractConversation'),
  pythonPath: document.getElementById('pythonPath'),
  minimizeWindow: document.getElementById('minimizeWindow'),
  maximizeWindow: document.getElementById('maximizeWindow'),
  closeWindow: document.getElementById('closeWindow'),
};

function setBadge(element, label, tone) {
  if (!element) {
    return;
  }
  element.textContent = label;
  element.dataset.tone = tone;
}

function shortRuntimeLabel(pythonPath) {
  if (!pythonPath) {
    return '';
  }
  const tail = pythonPath.split(/[\\/]/).pop() || 'python.exe';
  return `${tail} - x64`;
}

function updateClock() {
  const now = new Date();
  if (elements.clockTime) {
    elements.clockTime.textContent = now.toLocaleTimeString([], {
      hour: 'numeric',
      minute: '2-digit',
    });
  }
  if (elements.clockDate) {
    elements.clockDate.textContent = now.toLocaleDateString([], {
      weekday: 'long',
      month: 'long',
      day: 'numeric',
      year: 'numeric',
    });
  }
}

function setDrawerOpen(open, options = {}) {
  state.drawerOpen = Boolean(open);
  if (options.manual) {
    state.drawerUserClosed = !state.drawerOpen;
    if (state.drawerOpen) {
      state.drawerAutoOpened = true;
    }
  }
  document.body.classList.toggle('drawer-open', state.drawerOpen);

  if (elements.drawer) {
    elements.drawer.classList.toggle('is-open', state.drawerOpen);
    elements.drawer.setAttribute('aria-hidden', String(!state.drawerOpen));
    if ('inert' in elements.drawer) {
      elements.drawer.inert = !state.drawerOpen;
    }
  }
}

function toggleDrawer() {
  setDrawerOpen(!state.drawerOpen, { manual: true });
}

function setVoiceLabStatus(text) {
  if (elements.voiceLabStatus) {
    elements.voiceLabStatus.textContent = text;
  }
}

async function restartVoiceStack() {
  appendLog({ source: 'launcher', message: 'Restarting the voice stack...', timestamp: new Date().toISOString() });
  await window.fridayDeck.startPlayground();
}

function clearOrbActivity() {
  document.body.classList.remove('orb-speaking', 'orb-listening');
  document.body.removeAttribute('data-orb-activity');
  if (orbTimers.speaking) {
    clearTimeout(orbTimers.speaking);
    orbTimers.speaking = null;
  }
  if (orbTimers.listening) {
    clearTimeout(orbTimers.listening);
    orbTimers.listening = null;
  }
}

function setOrbActivity(kind, duration = 2200) {
  if (!kind) {
    clearOrbActivity();
    return;
  }

  if (kind === 'speaking') {
    document.body.classList.add('orb-speaking');
    document.body.classList.remove('orb-listening');
    document.body.dataset.orbActivity = 'speaking';
    if (orbTimers.listening) {
      clearTimeout(orbTimers.listening);
      orbTimers.listening = null;
    }
    if (orbTimers.speaking) {
      clearTimeout(orbTimers.speaking);
    }
    orbTimers.speaking = setTimeout(() => {
      document.body.classList.remove('orb-speaking');
      if (document.body.dataset.orbActivity === 'speaking') {
        document.body.removeAttribute('data-orb-activity');
      }
      orbTimers.speaking = null;
    }, duration);
    return;
  }

  if (kind === 'listening') {
    document.body.classList.add('orb-listening');
    document.body.classList.remove('orb-speaking');
    document.body.dataset.orbActivity = 'listening';
    if (orbTimers.speaking) {
      clearTimeout(orbTimers.speaking);
      orbTimers.speaking = null;
    }
    if (orbTimers.listening) {
      clearTimeout(orbTimers.listening);
    }
    orbTimers.listening = setTimeout(() => {
      document.body.classList.remove('orb-listening');
      if (document.body.dataset.orbActivity === 'listening') {
        document.body.removeAttribute('data-orb-activity');
      }
      orbTimers.listening = null;
    }, duration);
  }
}

function isLikelyVoiceSpeechLine(text) {
  const value = String(text || '').trim();
  if (!value) {
    return false;
  }

  const ignorePatterns = [
    /^(\d{2}:\d{2}:\d{2}(?:\.\d{3})?)?\s*(debug|info|warn|warning|error)\b/i,
    /\blivekit\.agents\b/i,
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

  if (ignorePatterns.some((pattern) => pattern.test(value))) {
    return false;
  }

  return true;
}

function handleVoiceActivity(payload) {
  const kind = payload?.kind;
  if (kind === 'speaking') {
    setOrbActivity('speaking', 2600);
  } else if (kind === 'listening') {
    setOrbActivity('listening', 1200);
  }
}

function renderState(next) {
  Object.assign(state, next || {});

  if (state.mode === 'idle') {
    state.drawerAutoOpened = false;
    state.drawerUserClosed = false;
    if (state.drawerOpen) {
      setDrawerOpen(false);
    }
  }

  document.body.dataset.mode = state.mode;
  document.body.dataset.server = state.server;
  document.body.dataset.voice = state.voice;
  document.body.dataset.voiceLab = state.voiceLab;

  setBadge(elements.modeBadge, `Mode: ${state.mode}`, state.mode === 'local' ? 'open' : state.mode);
  setBadge(elements.serverBadge, `Server: ${state.server}`, state.server);
  setBadge(elements.voiceBadge, `Voice: ${state.voice}`, state.voice);
  setBadge(elements.voiceLabBadge, `Session: ${state.voiceLab}`, state.voiceLab);
  if (elements.chromeStatus) {
    const chromeStatusLabel =
      state.mode === 'console'
        ? 'Console'
        : state.mode === 'local'
          ? 'Local'
          : state.server === 'online'
            ? 'Ready'
            : 'Standby';
    elements.chromeStatus.textContent = chromeStatusLabel;
    elements.chromeStatus.dataset.tone = chromeStatusLabel === 'Standby' ? 'idle' : 'online';
  }

  if (elements.pythonPath) {
    elements.pythonPath.textContent = state.pythonPath
      ? `Python: ${shortRuntimeLabel(state.pythonPath)}`
      : 'Python: waiting...';
  }

  if (elements.runtimeSummary) {
    const runtime = state.runtime || {};
    const speech = runtime.speech === 'local' ? `local Whisper (${runtime.sttModel || 'base.en'})` : 'local speech';
    const tts = runtime.speech === 'local' ? `local Piper (${runtime.ttsModel || 'en_US-lessac-medium'})` : 'local TTS';
    const reasoning =
      runtime.llmProvider === 'ollama'
        ? `Ollama ${runtime.llmModel || 'gemma4'}`
        : runtime.llmProvider
          ? `${runtime.llmProvider}`
          : 'local reasoning';
    elements.runtimeSummary.textContent = `Speech: ${speech} | TTS: ${tts} | Reasoning: ${reasoning}`;
  }

  if (elements.voiceLabStatus) {
    const localStatus =
      state.voiceLab === 'open'
        ? 'Local voice is ready and listening on this machine.'
        : state.voiceLab === 'opening'
          ? 'Local voice is starting up.'
          : 'Local voice is closed.';
    setVoiceLabStatus(localStatus);
  }

  const consoleActive =
    state.voice !== 'stopped' && (state.mode === 'console' || state.mode === 'local');
  if (elements.commandInput) {
    elements.commandInput.disabled = !consoleActive;
    elements.commandInput.placeholder = consoleActive
      ? 'Type a message for FRIDAY...'
      : 'Launch the console deck or local session to type here...';
  }

  const sendButton = elements.inputForm?.querySelector('button[type="submit"]');
  if (sendButton) {
    sendButton.disabled = !consoleActive;
  }

  document.querySelectorAll('[data-command]').forEach((button) => {
    button.disabled = !consoleActive;
  });

  if (state.mode !== 'idle' && state.voiceLab !== 'closed' && !state.drawerOpen && !state.drawerUserClosed && !state.drawerAutoOpened) {
    state.drawerAutoOpened = true;
    setDrawerOpen(true);
  }
}

function timestampLabel(iso) {
  try {
    return new Date(iso).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return '';
  }
}

function appendLog(entry) {
  if (!elements.logStream) {
    return;
  }

  const row = document.createElement('article');
  row.className = `log-entry log-entry--${entry.source || 'launcher'}`;

  const meta = document.createElement('div');
  meta.className = 'log-meta';
  meta.innerHTML = `<span class="log-source">${entry.source || 'launcher'}</span><span>${timestampLabel(entry.timestamp)}</span>`;

  const body = document.createElement('div');
  body.textContent = entry.message || '';

  row.append(meta, body);
  elements.logStream.appendChild(row);
  elements.logStream.scrollTop = elements.logStream.scrollHeight;

  if (entry.source === 'voice' && isLikelyVoiceSpeechLine(entry.message)) {
    setOrbActivity('speaking', 2400);
  }
}

function clearConversation() {
  if (!elements.logStream) {
    return;
  }
  elements.logStream.innerHTML = '';
}

async function extractConversation() {
  if (!elements.logStream) {
    return;
  }

  const transcript = Array.from(elements.logStream.querySelectorAll('.log-entry'))
    .map((entry) => String(entry.textContent || '').trim())
    .filter(Boolean)
    .join('\n\n');

  if (!transcript) {
    appendLog({
      source: 'launcher',
      message: 'Nothing to extract yet.',
      timestamp: new Date().toISOString(),
    });
    return;
  }

  try {
    await navigator.clipboard.writeText(transcript);
    appendLog({
      source: 'launcher',
      message: 'Conversation copied to clipboard.',
      timestamp: new Date().toISOString(),
    });
  } catch {
    appendLog({
      source: 'launcher',
      message: 'Clipboard access failed. I could not extract the conversation.',
      timestamp: new Date().toISOString(),
    });
  }
}

async function startConsole() {
  appendLog({ source: 'launcher', message: 'Booting console deck...', timestamp: new Date().toISOString() });
  await window.fridayDeck.startConsole();
}

async function stopAll() {
  appendLog({ source: 'launcher', message: 'Shutting everything down...', timestamp: new Date().toISOString() });
  await window.fridayDeck.stopAll();
}

async function sendInput(text) {
  const value = text.trim();
  if (!value) {
    return;
  }
  await window.fridayDeck.sendInput(value);
  if (elements.commandInput) {
    elements.commandInput.value = '';
  }
}

window.fridayDeck.onState(renderState);
window.fridayDeck.onLog(appendLog);
window.fridayDeck.onVoiceActivity(handleVoiceActivity);

elements.orbButton?.addEventListener('click', toggleDrawer);
elements.chromeOrb?.addEventListener('click', toggleDrawer);
elements.drawerClose?.addEventListener('click', () => setDrawerOpen(false));

document.querySelectorAll('[data-close-drawer]').forEach((node) => {
  node.addEventListener('click', () => setDrawerOpen(false));
});

elements.startConsole?.addEventListener('click', () => {
  startConsole().catch((error) =>
    appendLog({ source: 'launcher', message: error.message, timestamp: new Date().toISOString() })
  );
});

elements.restartVoice?.addEventListener('click', () => {
  restartVoiceStack().catch((error) =>
    appendLog({ source: 'launcher', message: error.message, timestamp: new Date().toISOString() })
  );
});

elements.stopAll?.addEventListener('click', () => {
  stopAll().catch((error) =>
    appendLog({ source: 'launcher', message: error.message, timestamp: new Date().toISOString() })
  );
});

elements.pickBrowser?.addEventListener('click', async () => {
  await window.fridayDeck.pickBrowser();
});

elements.clearConversation?.addEventListener('click', () => {
  clearConversation();
});

elements.extractConversation?.addEventListener('click', () => {
  extractConversation().catch((error) =>
    appendLog({ source: 'launcher', message: error.message, timestamp: new Date().toISOString() })
  );
});

elements.minimizeWindow?.addEventListener('click', () => {
  window.fridayDeck.minimizeWindow();
});

elements.maximizeWindow?.addEventListener('click', () => {
  window.fridayDeck.maximizeWindow();
});

elements.closeWindow?.addEventListener('click', () => {
  window.fridayDeck.closeWindow();
});

elements.inputForm?.addEventListener('submit', (event) => {
  event.preventDefault();
  sendInput(elements.commandInput.value).catch((error) =>
    appendLog({ source: 'launcher', message: error.message, timestamp: new Date().toISOString() })
  );
});

elements.commandInput?.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && event.shiftKey) {
    event.preventDefault();
    sendInput(elements.commandInput.value).catch((error) =>
      appendLog({ source: 'launcher', message: error.message, timestamp: new Date().toISOString() })
    );
  }
});

document.querySelectorAll('[data-command]').forEach((button) => {
  button.addEventListener('click', async () => {
    const command = button.getAttribute('data-command') || '';
    if (elements.commandInput) {
      elements.commandInput.value = command;
    }
    await sendInput(command);
  });
});

document.addEventListener('keydown', (event) => {
  const active = document.activeElement;
  const typing = active && ['INPUT', 'TEXTAREA'].includes(active.tagName);

  if (event.key === 'Escape' && state.drawerOpen) {
    event.preventDefault();
    setDrawerOpen(false);
    return;
  }

  if (event.code === 'Space' && !typing && !event.metaKey && !event.ctrlKey && !event.altKey) {
    event.preventDefault();
    toggleDrawer();
  }
});

renderState(state);
setDrawerOpen(false);
clearOrbActivity();
updateClock();
setInterval(updateClock, 1000);
appendLog({
  source: 'launcher',
  message: 'F.R.I.D.A.Y is booting automatically. The voice stack now runs locally on this machine.',
  timestamp: new Date().toISOString(),
});
