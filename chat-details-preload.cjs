;(() => {
  if (!process.isMainFrame || location.protocol !== 'app:' || location.hostname !== '-') return;
  const {contextBridge, ipcRenderer} = require('electron');
  contextBridge.exposeInMainWorld('localChatDetails', {
    delivery: ids => ipcRenderer.invoke('local-chat-sync:delivery', ids),
    colours: (mode, entries, previous) => ipcRenderer.invoke('local-sidebar-colours:preferences', mode, entries, previous),
    forkParents: ids => ipcRenderer.invoke('local-chat-details:fork-parents', ids),
    readTurns: (threadId, turnIds) => ipcRenderer.invoke('local-chat-details:read-turns', threadId, turnIds),
  });
})();
