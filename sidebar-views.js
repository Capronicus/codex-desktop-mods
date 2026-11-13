// With marks sync on in Mod Controls, saved views and text animations follow this account
// to other desktops. The main process decides whether anything leaves this window.
function installMarksSync(api) {
  const viewPrefix = 'codex.mods.saved-view.v1/', wordPrefix = 'codex.mods.animated-words.v1/';
  let account = null, applied = null, busy = false, timer = 0;
  function snapshot() {
    const scope = viewPrefix + account + '/', views = {}, words = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key.startsWith(scope)) {
        try { views[key.slice(scope.length)] = JSON.parse(localStorage.getItem(key)); } catch { /* Not a saved view. */ }
      } else if (key.startsWith(wordPrefix)) words.push(key.slice(wordPrefix.length));
    }
    return {account, views, words};
  }
  function schedule(delay) { if (!timer) timer = setTimeout(exchange, delay); }
  async function exchange() {
    timer = 0;
    if (busy || document.hidden) return;
    busy = true;
    try {
      const reply = await api.marks({...(account ? snapshot() : {}), applied});
      if (!reply?.enabled) return;
      const changed = account !== reply.account;
      account = reply.account;
      const received = reply.apply;
      if (received && received.id !== applied) {
        const target = key => key.startsWith('view/') ? viewPrefix + account + '/' + key.slice(5)
          : key.startsWith('word/') ? wordPrefix + key.slice(5) : null;
        for (const [key,value] of Object.entries(received.set || {}))
          if (target(key)) localStorage.setItem(target(key), key.startsWith('view/') ? JSON.stringify(value) : '1');
        for (const key of received.remove || []) if (target(key)) localStorage.removeItem(target(key));
        applied = received.id;
        // Storage events only reach other windows. Tell this window's list and highlights too.
        window.dispatchEvent(new StorageEvent('storage', {key:null}));
      }
      if (changed || received) schedule(1000);
    } catch { /* Marks stay on this desktop until the next attempt. */ }
    finally { busy = false; }
  }
  window.setInterval(() => schedule(0), 20000);
  window.addEventListener('focus', () => schedule(0));
  window.addEventListener('storage', event => {
    if (!event.key || event.key.startsWith(viewPrefix) || event.key.startsWith(wordPrefix)) schedule(1500);
  });
  schedule(0);
}

// A view reads the local catalogue on demand. Opening a result uses the native chat route.
function installSidebarViews() {
  const api = window.localChatDetails;
  if (!api?.savedViewData || window.cscSidebarViews) return;
  window.cscSidebarViews = true;
  if (api.marks) installMarksSync(api);
  const panel = document.createElement('details');
  panel.id = 'csc-saved-views';
  const summary = document.createElement('summary');
  summary.textContent = 'Saved views';
  const body = document.createElement('div');
  panel.append(summary, body);
  const style = document.createElement('style');
  style.textContent = `
    #csc-saved-views {padding:3px 10px;color:inherit;font:inherit}
    #csc-saved-views summary {cursor:pointer;font-weight:600}
    #csc-saved-views button,#csc-saved-views select,#csc-saved-views input {font:inherit;color:inherit}
    #csc-saved-views select,#csc-saved-views input {background:Canvas;color:CanvasText;border:1px solid GrayText;border-radius:4px;padding:2px 4px;max-width:100%}
    #csc-saved-views label {display:grid;gap:2px;margin:5px 0}
    #csc-saved-views button {cursor:pointer;text-align:start;padding:2px 5px;border-radius:4px}
    #csc-saved-views button:hover,#csc-saved-views button:focus-visible {background:color-mix(in srgb,currentColor 14%,transparent)}
    #csc-saved-views ol {list-style:none;margin:4px 0;padding:0;max-height:38vh;overflow:auto}
    #csc-saved-views li button {display:block;width:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    #csc-saved-views input[type=search] {display:block;width:100%;box-sizing:border-box;margin:4px 0}
  `;
  document.head.append(style);
  let data, active = '', request = 0, editing = false, host, queued = false;
  const prefix = 'codex.mods.saved-view.v1/';
  // The title filter narrows the chosen view, or every local chat when no view is chosen.
  const filter = document.createElement('input');
  filter.type = 'search';
  filter.placeholder = 'Filter chats by title';
  filter.setAttribute('aria-label','Filter chats by title');
  function records() {
    const scope = prefix + data.account + '/';
    return Object.keys(localStorage).filter(k=>k.startsWith(scope)).flatMap(key=>{
      try { return [{...JSON.parse(localStorage.getItem(key)),key}]; } catch { return []; }
    }).sort((a,b)=>a.name.localeCompare(b.name));
  }
  function control(title, fn, parent = body) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = title;
    button.onclick = fn;
    parent.append(button);
    return button;
  }
  function field(title, input) {
    const label = document.createElement('label');
    input.setAttribute('aria-label',title);
    label.append(title,input);
    body.append(label);
    return input;
  }
  function options(values, first, initial) {
    const input = document.createElement('select');
    input.add(new Option(first,''));
    for (const [id,name] of Object.entries(values).sort((a,b)=>a[1].localeCompare(b[1])))
      input.add(new Option(name,id));
    if (initial && !Object.hasOwn(values,initial)) input.add(new Option('Unavailable on this device',initial));
    input.value = initial || '';
    return input;
  }
  function editor(view = {}) {
    editing = true;
    body.replaceChildren();
    const name = field('View name',document.createElement('input'));
    name.maxLength = 80;
    name.value = view.name || '';
    const project = field('Project',options(data.projects,'Any project',view.project));
    const origin = field('Started on',options(data.devices,'Any device',view.origin));
    const age = field('Last updated',options({1:'Past day',7:'Past week',30:'Past month'},'Any time',view.age));
    const receivers = {...data.devices};
    delete receivers[data.device];
    const waiting = field('Awaiting receipt from',options(receivers,'Any receipt status',view.waiting));
    control('Save',()=>{
      if (!name.value.trim()) {name.setCustomValidity('Give your view a name.'); name.reportValidity(); return;}
      active = view.key || prefix + data.account + '/' + crypto.randomUUID();
      localStorage.setItem(active,JSON.stringify({name:name.value.trim(),project:project.value,origin:origin.value,
        age:age.value,waiting:waiting.value}));
      render();
    });
    name.oninput = ()=>name.setCustomValidity('');
    control('Cancel',render);
    name.focus();
  }
  function render() {
    editing = false;
    const typing = document.activeElement === filter;
    body.replaceChildren();
    const views = records();
    const view = views.find(row=>row.key===active);
    const chooser = options(Object.fromEntries(views.map(v=>[v.key,v.name])),'Choose a view',view?.key);
    chooser.setAttribute('aria-label','Saved sidebar view');
    chooser.onchange = ()=>{active=chooser.value;render();};
    body.append(chooser);
    control('New view',()=>editor());
    if (view) {
      control('Edit',()=>editor(view));
      control('Remove',()=>{
        if (window.confirm('Remove “'+view.name+'” from saved views?')) {
          localStorage.removeItem(view.key);
          active = '';
          render();
        }
      });
    }
    control('Refresh',refresh);
    const count = document.createElement('div');
    count.setAttribute('role','status');
    const list = document.createElement('ol');
    body.append(filter,count,list);
    if (typing) filter.focus();
    let more;
    function show() {
      more?.remove();
      list.replaceChildren();
      const needle = filter.value.trim().toLocaleLowerCase();
      if (!view && !needle) {count.textContent = ''; return;}
      const cutoff = Date.now() - Number(view?.age)*86400000;
      const rows = data.chats.filter(row=>
        (!needle || row.title.toLocaleLowerCase().includes(needle)) && (!view || (
        (!view.project || row.project===view.project) &&
        (!view.origin || row.origin===view.origin) &&
        (!view.age || row.updated>=cutoff) &&
        (!view.waiting || (view.waiting!==data.device && Object.hasOwn(data.devices,view.waiting) &&
          (row.busy.includes(view.waiting) || (row.shared && !row.received.includes(view.waiting))))))));
      rows.sort((a,b)=>b.updated-a.updated || a.title.localeCompare(b.title));
      count.textContent = rows.length+' matching '+(rows.length===1?'chat':'chats');
      let shown = 0;
      (function page() {
        for (const row of rows.slice(shown,shown+50)) {
          const item = document.createElement('li');
          const link = control(row.title,()=>api.openChat(row.id),item);
          link.title = row.title;
          list.append(item);
        }
        shown += 50;
        more = shown < rows.length ? control('Show more',()=>{more.remove();page();}) : null;
      })();
    }
    filter.oninput = show;
    show();
  }
  async function refresh() {
    const generation = ++request;
    try {
      const next = await api.savedViewData();
      if (generation!==request || !panel.open) return;
      if (active && !active.startsWith(prefix+next.account+'/')) active = '';
      data = next;
      render();
    } catch {
      if (generation!==request) return;
      body.textContent = 'Could not read local chats. ';
      control('Retry',refresh);
    }
  }
  panel.ontoggle = ()=>{
    if (panel.open) refresh();
    else {++request; data=null;editing=false;body.replaceChildren();}
  };
  window.addEventListener('focus',()=>{if (panel.open && !editing) refresh();});
  window.addEventListener('storage',event=>{
    if (panel.open && !editing && data && (!event.key || event.key.startsWith(prefix+data.account+'/'))) render();
  });
  function mount() {
    queued = false;
    host = document.querySelector('[data-app-action-sidebar-scroll]');
    if (host && panel.parentNode!==host) host.prepend(panel);
  }
  const observer = new MutationObserver(()=>{
    if ((!host?.isConnected || !panel.isConnected) && !queued) {
      queued = true;
      requestAnimationFrame(mount);
    }
  });
  observer.observe(document.documentElement,{childList:true,subtree:true});
  mount();
}
