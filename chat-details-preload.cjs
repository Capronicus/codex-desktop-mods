;(() => {
  if (!process.isMainFrame || location.protocol !== 'app:' || location.hostname !== '-') return;
  const {contextBridge, ipcRenderer} = require('electron');
  const groups = ipcRenderer.sendSync('local-sidebar:groups');
  contextBridge.exposeInMainWorld('localChatDetails', {
    visuals: ipcRenderer.sendSync('local-mod-controls:visuals'),
    subsectionParents: groups.parents,
    sidebarProjectKeys: groups.projectKeys,
    openModControls: async context => {
      if (context) await ipcRenderer.invoke('local-chat-actions:context', context);
      return ipcRenderer.invoke('codex_desktop:message-from-view', {
      type: 'open-in-browser',
      url: 'codex://mcp-app/chat-sync@capronicus-desktop-mods/open_mod_controls',
      });
    },
    openChat: id => /^[a-f0-9-]{36}$/.test(id) && ipcRenderer.invoke('codex_desktop:message-from-view',
      {type:'open-in-browser',url:'codex://threads/'+id}),
    listenActions: listener => {
      ipcRenderer.on('local-chat-actions:command', (_, command) => listener(command));
      const attach = () => ipcRenderer.send('local-chat-actions:listen');
      window.addEventListener('load', attach, {once:true});
      window.addEventListener('focus', attach);
      attach();
    },
    savePerformance: report => ipcRenderer.invoke('local-chat-actions:performance', report),
    savedViewData: () => ipcRenderer.invoke('local-sidebar:saved-view-data'),
    delivery: ids => ipcRenderer.invoke('local-chat-sync:delivery', ids),
    colourMembers: (kind,id) => ipcRenderer.invoke('local-sidebar-colours:members',kind,id),
    colours: (mode, entries, previous) => ipcRenderer.invoke('local-sidebar-colours:preferences', mode, entries, previous),
    forkParents: ids => ipcRenderer.invoke('local-chat-details:fork-parents', ids),
    readTurns: (threadId, turnIds) => ipcRenderer.invoke('local-chat-details:read-turns', threadId, turnIds),
  });
})();
