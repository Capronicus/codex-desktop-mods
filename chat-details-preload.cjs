;(() => {
  if (!process.isMainFrame || location.protocol !== 'app:' || location.hostname !== '-') return;
  const {contextBridge, ipcRenderer} = require('electron');
  contextBridge.exposeInMainWorld('localChatDetails', {
    visuals: ipcRenderer.sendSync('local-mod-controls:visuals'),
    delivery: ids => ipcRenderer.invoke('local-chat-sync:delivery', ids),
    colourMembers: (kind,id) => ipcRenderer.invoke('local-sidebar-colours:members',kind,id),
    colours: (mode, entries, previous) => ipcRenderer.invoke('local-sidebar-colours:preferences', mode, entries, previous),
    forkParents: ids => ipcRenderer.invoke('local-chat-details:fork-parents', ids),
    readTurns: (threadId, turnIds) => ipcRenderer.invoke('local-chat-details:read-turns', threadId, turnIds),
  });
})();
