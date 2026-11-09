;(() => {
  if (!process.isMainFrame || location.protocol !== 'app:' || location.hostname !== '-') return;
  const {contextBridge, ipcRenderer} = require('electron');
  contextBridge.exposeInMainWorld('localChatDetails', {
    forkParents: ids => ipcRenderer.invoke('local-chat-details:fork-parents', ids),
    readTurns: (threadId, turnIds) => ipcRenderer.invoke('local-chat-details:read-turns', threadId, turnIds),
  });
})();
