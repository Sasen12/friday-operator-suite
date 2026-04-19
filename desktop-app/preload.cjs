const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('fridayDeck', {
  startConsole: () => ipcRenderer.invoke('friday:start-console'),
  startPlayground: () => ipcRenderer.invoke('friday:start-playground'),
  stopAll: () => ipcRenderer.invoke('friday:stop-all'),
  sendInput: (text) => ipcRenderer.invoke('friday:send-input', text),
  openExternal: (url) => ipcRenderer.invoke('friday:open-external', url),
  pickBrowser: () => ipcRenderer.invoke('friday:pick-browser'),
  minimizeWindow: () => ipcRenderer.invoke('friday:window-minimize'),
  maximizeWindow: () => ipcRenderer.invoke('friday:window-maximize'),
  closeWindow: () => ipcRenderer.invoke('friday:window-close'),
  onLog: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on('friday:log', listener);
    return () => ipcRenderer.removeListener('friday:log', listener);
  },
  onState: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on('friday:state', listener);
    return () => ipcRenderer.removeListener('friday:state', listener);
  },
  onVoiceActivity: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on('friday:voice-activity', listener);
    return () => ipcRenderer.removeListener('friday:voice-activity', listener);
  },
});
