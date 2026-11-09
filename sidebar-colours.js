// Loaded alongside the desktop sidebar renderer. Each key is one local preference,
// so edits to different items in separate windows cannot overwrite each other.
function createSidebarColours({preferencesReady, getPreference, setPreference, preferenceKeys, watchPreferencePrefix, readProjectPage}) {
  installForkLines();
  installDeliveryStatus();
  const prefix = 'codex.sidebar-colours.v1/';
  const projectAttr = 'data-app-action-sidebar-project-id';
  const projectListAttr = 'data-app-action-sidebar-project-list-id';
  const threadAttr = 'data-csc-chat-key';
  const rawThreadAttr = 'data-app-action-sidebar-thread-id';
  const sectionAttr = 'data-sidebar-project-container-id';
  const sectionSelector = '[' + sectionAttr + '^="custom:"]';
  const sectionToggle = '[data-app-action-sidebar-section-toggle]';
  const sectionStore = 'sidebar-custom-sections-v3';
  const ownerAttr = 'data-csc-owner';
  const hex = /^#[0-9a-f]{6}$/i;
  let rules, dialog;
  const bridge = window.localChatDetails?.colours;
  let stored = new Map(), inheritedLinks = [], version = null, queue = Promise.resolve();
  const portableKey = /^(project|section|chat|override-project|override-section|swatch)\/[A-Za-z0-9:_@.-]{1,256}$/;
  function exchange(mode, entries = []) {
    const next = queue.then(async () => {
      const snapshot = await bridge(mode, entries, version);
      if (snapshot) {
        version = snapshot.version;
        stored = new Map(snapshot.entries);
        inheritedLinks = snapshot.inherited;
        refresh();
      }
    });
    queue = next.catch(() => {});
    return next;
  }
  async function seed() {
    const entries = [];
    for (const full of preferenceKeys(prefix)) {
      const id = full.slice(prefix.length), value = getPreference(full, null);
      if (portableKey.test(id) && (hex.test(value) || (id.startsWith('override-') && value === 'on')))
        entries.push([id,value]);
    }
    for (let i = 0; i < entries.length; i += 512) await exchange('seed', entries.slice(i,i+512));
    if (!entries.length) await exchange('read');
  }
  const nativeReady = preferencesReady();
  const ready = bridge ? nativeReady.then(seed).catch(() => {}) : nativeReady;
  async function write(entries, mode = 'write') {
    if (bridge) {
      // Retry migration after a transient database lock before accepting new edits.
      if (version === null) await seed();
      const supported = entries.filter(([id]) => portableKey.test(id));
      if (supported.length) await exchange(mode, supported);
    }
    for (const [id,value] of entries) if(!bridge || mode!=='finish' || !id.startsWith('chat/')) setPreference(prefix + id, value ?? undefined);
    refresh();
  }
  function poll() {
    if (bridge && !document.hidden) ready.then(() => version === null ? seed() : exchange('read')).catch(() => {});
  }
  if (bridge) {
    window.setInterval(poll, 5000);
    window.addEventListener('focus', poll);
  }

  const displayRules = document.createElement('style');
  displayRules.textContent = `
    .csc-picker { color-scheme: inherit; font: inherit; font-size: 14px; padding: 20px;
      color: var(--color-text-primary, CanvasText); background: var(--color-background-surface, Canvas);
      border: 1px solid color-mix(in srgb, currentColor 25%, transparent); border-radius: 16px;
      box-shadow: 0 12px 48px #0004; width: min(320px, calc(100vw - 24px)); max-height: calc(100dvh - 24px); overflow: auto; margin: auto; }
    .csc-picker::backdrop { background: #0005; }
    .csc-picker h2 { margin: 0; font: inherit; font-weight: 650; font-size: 17px; }
    .csc-picker p { margin: 4px 0 15px; opacity: .75; overflow-wrap: anywhere; }
    .csc-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 8px; margin: 12px 0; }
    .csc-picker button, .csc-picker input { cursor: pointer; }
    .csc-grid button { border-radius: 50%; aspect-ratio: 1; width: 100%; border: 2px solid #8886; }
    .csc-grid button[aria-pressed=true] { outline: 2px solid currentColor; outline-offset: 2px; }
    .csc-picker :focus-visible { outline: 2px solid Highlight; outline-offset: 2px; }
    .csc-custom, .csc-footer { display: flex; align-items: center; gap: 8px; margin-top: 16px; }
    .csc-custom input { width: 40px; height: 30px; padding: 0; }
    .csc-wheel { position:relative; width:min(158px, 100%); aspect-ratio:1; margin:12px auto;
      border-radius:100%; touch-action:none; cursor:crosshair;
      background:radial-gradient(closest-side, white, transparent),conic-gradient(from 90deg,red,#ff0,#0f0,#0ff,#00f,#f0f,red); }
    .csc-wheel::before { content:""; position:absolute; inset:0; border-radius:inherit; background:black; opacity:var(--csc-shade); }
    .csc-wheel-mark { position:absolute; width:12px; height:12px; border-radius:50%; border:2px solid white;
      box-shadow:0 0 0 1px #111, inset 0 0 0 1px #111; transform:translate(-50%,-50%); pointer-events:none; }
    .csc-brightness { display:grid; grid-template-columns:1fr auto; align-items:center; gap:5px; }
    .csc-brightness output { font-variant-numeric:tabular-nums; }
    .csc-brightness input { grid-column:1/-1; width:100%; height:18px; margin:0; appearance:none; background:transparent; }
    .csc-brightness input::-webkit-slider-runnable-track { height:8px; border-radius:8px;
      background:linear-gradient(to right,black,var(--csc-full)); box-shadow:inset 0 0 0 1px #8888; }
    .csc-brightness input::-webkit-slider-thumb { appearance:none; width:16px; height:16px; margin-top:-4px;
      border-radius:50%; background:var(--csc-selected); border:2px solid white; box-shadow:0 0 0 1px #222; }
    .csc-footer, .csc-saved-heading { display:flex; align-items:center; justify-content:space-between; }
    .csc-override { display:flex; gap:10px; align-items:center; min-height:38px; cursor:pointer; font-weight:600; }
    .csc-override input[type="checkbox"] { appearance:none; box-sizing:border-box; display:grid; place-content:center; flex:none;
      width:22px; height:22px; margin:0; border:2px solid currentColor; border-radius:5px; background:Canvas; color:CanvasText; cursor:pointer; }
    .csc-override input:checked { background:Highlight; border-color:Highlight; color:HighlightText; }
    .csc-override input:checked::before { content:""; width:7px; height:12px; border:solid currentColor; border-width:0 2.5px 2.5px 0; transform:translateY(-1px) rotate(45deg); }
    .csc-override input:focus-visible { outline:2px solid currentColor; outline-offset:3px; }
    .csc-override input:disabled { opacity:.65; }
    .csc-custom { flex-wrap:wrap; }
    .csc-action { background: transparent; border-radius: 7px; padding: 5px 8px; color: inherit; font: inherit; }
    .csc-action:hover { background: color-mix(in srgb, currentColor 12%, transparent); }
    [data-csc-chat-key][data-csc-activity-day] {
      --csc-age-days: calc(var(--csc-clock-day) - attr(data-csc-activity-day type(<number>)));
      --csc-brightness: clamp(75%, calc(100% - (var(--csc-age-days) - 1) * 25% / 13), 100%);
      --csc-chat-tone: color-mix(in srgb, var(--csc-row-base) var(--csc-brightness), black);
    }
    [data-csc-chat-key]:is([data-app-action-sidebar-thread-pinned="true"], [data-csc-cloud-pinned="true"])::after {
      content: ""; position: absolute; inset: 0; pointer-events: none;
      border: 1px solid color-mix(in srgb, var(--csc-row-base, currentColor) 55%, transparent);
      border-radius: inherit;
    }
    @media (forced-colors: active) {
      [data-csc-chat-key]:is([data-app-action-sidebar-thread-pinned="true"], [data-csc-cloud-pinned="true"])::after {
        border-color: CanvasText;
      }
    }
  `;
  document.head.append(displayRules);

  const dayMs = 86400000;
  function activityDay(value) {
    if (value == null || value === '') return undefined;
    const numeric = Number(value);
    const ms = Number.isFinite(numeric) ? (numeric < 1e11 ? numeric * 1000 : numeric) : Date.parse(value);
    return Number.isFinite(ms) && ms > 0 ? ms / dayMs : undefined;
  }
  function updateClock() {
    document.documentElement.style.setProperty('--csc-clock-day', String(Date.now() / dayMs));
  }
  updateClock();
  window.setInterval(() => { if (!document.hidden) updateClock(); }, 15 * 60 * 1000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) updateClock(); });

  function attr(name, id) { return '[' + name + '="' + CSS.escape(id) + '"]'; }
  function read(kind, id) {
    if (!id) return null;
    const name = kind + '/' + id;
    return stored.has(name) ? stored.get(name) : getPreference(prefix + name, null);
  }
  function colourKeys() {
    return new Set([...preferenceKeys(prefix)].filter(k => k.startsWith(prefix)).map(k => k.slice(prefix.length)).concat([...stored.keys()]));
  }

  function chatRules(selectors, value) {
    return selectors.join(',') + '{--csc-row-base:' + value + '}' +
      selectors.map(s => s + ' [data-csc-chat-label]').join(',') + '{color:var(--csc-chat-tone,var(--csc-row-base)) !important}';
  }

  function sections() {
    const settings = getPreference(sectionStore, {}) || {};
    return Object.values(settings).flatMap(account => Array.isArray(account?.sections) ? account.sections : []);
  }
  function sectionChatKey(key) {
    return typeof key === 'string' ? key.replace(/^codex:thread:/, '') : null;
  }
  function memberSection(row) {
    const group = row?.closest(sectionSelector);
    if (group) return group.getAttribute(sectionAttr).slice(7);
    const keys = [row?.getAttribute(rawThreadAttr),row?.getAttribute(threadAttr)].filter(Boolean);
    return sections().find(section => section.itemKeys?.some(k => keys.includes(sectionChatKey(k))))?.id;
  }
  function refresh() {
    const sectionRules = [], projects = [], chats = [], forcedProjects = [], forcedSections = [];
    const records = sections();
    const linked = {project:new Map(), section:new Map()};
    for (const {chat,project,section} of inheritedLinks) {
      for (const [kind,id] of [['project',project],['section',section]]) {
        if (!id) continue;
        if (!linked[kind].has(id)) linked[kind].set(id, []);
        linked[kind].get(id).push(attr(threadAttr,chat));
      }
    }
    for (const parts of colourKeys()) {
      const split = parts.indexOf('/');
      const kind = parts.slice(0, split), id = parts.slice(split + 1);
      const value = read(kind,id);
      if (!id || !hex.test(value)) continue;
      if (kind === 'section') {
        const group = attr(sectionAttr, 'custom:' + id);
        sectionRules.push(group + ' ' + sectionToggle + '{color:' + value + ' !important}');
        const members = [group + ' :where([' + threadAttr + '])', ...(linked.section.get(id) || [])];
        for (const raw of records.find(section => section.id === id)?.itemKeys || []) {
          const chat = sectionChatKey(raw);
          if (chat) members.push(attr(rawThreadAttr,chat), attr(threadAttr,chat));
        }
        (read('override-section',id) === 'on' ? forcedSections : sectionRules).push(chatRules(members,value));
      } else if (kind === 'project') {
        projects.push(attr(projectAttr, id) + ' [data-csc-project-label]{color:' + value + ' !important}');
        const descendant = ' :where([' + threadAttr + '])';
        const members = [attr(projectListAttr,id) + descendant, attr('data-csc-project-group',id) + descendant,
          attr(ownerAttr,id), ...(linked.project.get(id) || [])];
        (read('override-project',id) === 'on' ? forcedProjects : projects).push(chatRules(members,value));
      } else if (kind === 'chat') {
        chats.push(chatRules([attr(threadAttr, id)], value));
      }
    }
    if (!rules) {
      rules = document.createElement('style');
      rules.id = 'codex-sidebar-colour-rules';
      document.head.append(rules);
    }
    rules.textContent = sectionRules.concat(projects,chats,forcedProjects,forcedSections).join('\n');
  }

  const palette = [
    ['Coral', '#e26c65'], ['Amber', '#ce8b24'], ['Leaf', '#6d9a40'],
    ['Teal', '#299e93'], ['Blue', '#5b8fe2'], ['Violet', '#9a77d3'],
    ['Rose', '#cf739d'], ['Copper', '#bb7751'], ['Olive', '#929039'],
    ['Aqua', '#3798b5'], ['Indigo', '#8082d7'], ['Slate', '#8894a3'],
  ];
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }
  function action(label, run) {
    const button = el('button', 'csc-action', label);
    button.type = 'button';
    button.addEventListener('click', run);
    return button;
  }
  function customPicker(initial, save, saveSwatch) {
    const section = el('div');
    const wheel = el('div', 'csc-wheel');
    const mark = el('span', 'csc-wheel-mark');
    wheel.append(mark);
    wheel.tabIndex = 0;
    wheel.setAttribute('role', 'slider');
    wheel.setAttribute('aria-label', 'Colour wheel');
    wheel.setAttribute('aria-valuemin', '0');
    wheel.setAttribute('aria-valuemax', '360');
    wheel.setAttribute('aria-description', 'Left and right change hue. Up and down change saturation.');
    const brightness = el('label', 'csc-brightness', 'Brightness');
    const percent = el('output');
    const bar = el('input');
    bar.type = 'range';
    bar.min = '0'; bar.max = '100'; bar.step = '1';
    bar.setAttribute('aria-label', 'Brightness');
    brightness.append(percent, bar);
    const row = el('div', 'csc-custom');
    const swatch = el('input');
    swatch.type = 'color';
    swatch.setAttribute('aria-label', 'Custom colour');
    let hue = 0, saturation = 0, value = 0;

    function colour(v) {
      const channel = offset => {
        const k = (offset + hue / 60) % 6;
        return Math.round(255 * v * (1 - saturation * Math.max(0, Math.min(k, 4-k, 1))))
          .toString(16).padStart(2, '0');
      };
      return '#' + channel(5) + channel(3) + channel(1);
    }
    function render() {
      const radians = hue * Math.PI / 180;
      mark.style.left = (50 + 50 * saturation * Math.cos(radians)) + '%';
      mark.style.top = (50 + 50 * saturation * Math.sin(radians)) + '%';
      wheel.style.setProperty('--csc-shade', 1 - value);
      wheel.setAttribute('aria-valuenow', String(Math.round(hue)));
      wheel.setAttribute('aria-valuetext', Math.round(hue) + ' degrees, ' + Math.round(100 * saturation) + '% saturation');
      swatch.value = colour(value);
      bar.value = String(Math.round(100 * value));
      percent.textContent = bar.value + '%';
      brightness.style.setProperty('--csc-full', colour(1));
      brightness.style.setProperty('--csc-selected', swatch.value);
    }
    function fromHex(code) {
      const [r, g, b] = code.slice(1).match(/../g).map(c => parseInt(c, 16) / 255);
      value = Math.max(r, g, b);
      const delta = value - Math.min(r, g, b);
      saturation = value ? delta / value : 0;
      if (delta) {
        const sector = value === r ? (g-b)/delta : value === g ? 2+(b-r)/delta : 4+(r-g)/delta;
        hue = (60 * sector + 360) % 360;
      }
      render();
    }
    function point(event) {
      const rect = wheel.getBoundingClientRect();
      const dx = 2 * (event.clientX - rect.left) / rect.width - 1;
      const dy = 2 * (event.clientY - rect.top) / rect.height - 1;
      saturation = Math.min(1, Math.hypot(dx, dy));
      if (saturation) hue = (Math.atan2(dy, dx) * 180 / Math.PI + 360) % 360;
      render();
    }
    wheel.onpointerdown = event => {
      if (!event.isPrimary || event.button !== 0) return;
      wheel.setPointerCapture(event.pointerId); point(event);
    };
    wheel.onpointermove = event => { if (wheel.hasPointerCapture(event.pointerId)) point(event); };
    wheel.onkeydown = event => {
      const step = event.shiftKey ? 10 : 1;
      switch (event.key) {
        case 'ArrowLeft': hue = (hue - step + 360) % 360; break;
        case 'ArrowRight': hue = (hue + step) % 360; break;
        case 'ArrowUp': saturation = Math.min(1, saturation + step/100); break;
        case 'ArrowDown': saturation = Math.max(0, saturation - step/100); break;
        default: return;
      }
      event.preventDefault(); render();
    };
    bar.oninput = () => { value = Number(bar.value) / 100; render(); };
    swatch.oninput = () => fromHex(swatch.value);
    fromHex(initial);
    row.append(swatch, action('Apply custom colour', () => save(swatch.value)),
      action('Save swatch', () => saveSwatch(swatch.value)));
    section.append(wheel, brightness, row);
    return section;
  }
  async function groupChats(kind,id){
    const keys=new Set(), projects=new Set();
    function add(raw){
      if (typeof raw!=='string')return;
      if (/^(?:codex|chatgpt):project:/.test(raw)) projects.add(raw.replace(/^[^:]+:project:/,''));
      else keys.add(sectionChatKey(raw));
    }
    if(kind==='section'){
      for(const key of sections().find(s=>s.id===id)?.itemKeys || [])add(key);
    } else projects.add(id);
    const native=await window.localChatDetails?.colourMembers?.(kind,id);
    for(const chat of native?.chats || [])add(chat);
    for(const pid of native?.cloudProjects || [])projects.add(pid);
    for(const hint of inheritedLinks)if(hint[kind]===id)add(hint.chat);
    for(const row of document.querySelectorAll('['+threadAttr+']')){
      const project=row.getAttribute(ownerAttr)||row.closest('['+projectListAttr+']')?.getAttribute(projectListAttr)||row.closest('[data-csc-project-group]')?.getAttribute('data-csc-project-group');
      if((kind==='section' && memberSection(row)===id) || projects.has(project))add(row.getAttribute(threadAttr));
    }
    for(const projectId of projects){
      if(!projectId.startsWith('g-p-'))continue;
      if(!readProjectPage)throw Error('This build cannot list the full cloud project.');
      let cursor=null;const seen=new Set();
      do {
        if(seen.has(cursor))throw Error('Repeated project page');
        seen.add(cursor);
        const page=await readProjectPage({projectId,cursor,ownedOnly:false,limit:50,signal:AbortSignal.timeout(20000)});
        if(!Array.isArray(page?.items))throw Error('Incomplete project membership');
        for(const c of page.items)if(c.id)add('chatgpt:conversation:'+c.id);
        cursor=page.cursor;
      }while(cursor);
    }
    return [...keys].filter(k=>typeof k==='string' && /^[A-Za-z0-9:_@.-]{1,256}$/.test(k));
  }
  async function open(kind, id) {
    if (!id) return;
    await ready;
    if (dialog && dialog.open) dialog.close();
    const project = kind === 'project', section = kind === 'section';
    const selector = section ? attr(sectionAttr,'custom:'+id) : project ? attr(projectAttr, id) : [attr(threadAttr, id), attr(rawThreadAttr, id)].join(',');
    const row = document.querySelector(selector);
    const savedId = () => section || project ? id : row?.getAttribute(threadAttr) || id;
    const title = section ? row?.querySelector('[data-app-action-sidebar-section-heading]')?.getAttribute('data-app-action-sidebar-section-heading') : row?.getAttribute('data-app-action-sidebar-' + (project ? 'project-label' : 'thread-title'))
      ?? row?.querySelector('[data-csc-chat-label]')?.textContent;
    const hint = kind === 'chat' && inheritedLinks.find(link => link.chat === savedId());
    const owner = row?.closest('[data-csc-project-group]')?.getAttribute('data-csc-project-group')
      || row?.getAttribute(ownerAttr) || row?.closest('[' + projectListAttr + ']')?.getAttribute(projectListAttr) || hint?.project;
    const current = read(kind, savedId());
    const sectionId = kind === 'chat' ? memberSection(row) || hint?.section : null;
    const forcedSection = read('override-section',sectionId) && read('section',sectionId);
    const forcedProject = read('override-project',owner) && read('project',owner);
    const inherited = kind === 'chat' ? forcedSection || read('project',owner) || read('section',sectionId) : null;
    const box = el('dialog', 'csc-picker');
    dialog = box;
    const heading = el('h2', null, section ? 'Section colour' : project ? 'Project colour' : 'Chat colour');
    heading.id = 'csc-picker-heading';
    box.setAttribute('aria-labelledby', heading.id);
    box.append(heading);
    if (title) box.append(el('p', null, title));
    if (kind === 'chat' && (forcedSection || forcedProject))
      box.append(el('p',null,'The ' + (forcedSection ? 'section' : 'project') + ' override controls this chat’s visible colour.'));
    const error = el('div');
    error.setAttribute('role', 'alert');
    const setting = kind + '/' + savedId();
    const force = 'override-' + setting;
    async function change(entries,mode) {
      try { await write(entries,mode); return true; }
      catch { error.textContent = 'Could not save the colour in this profile.'; return false; }
    }
    async function finishOverride(reset=false) {
      const base=read(kind,id);
      if(!hex.test(base))return change([[force,null],...(reset?[[setting,null]]:[])]);
      try {
        const members=await groupChats(kind,id);
        return await change([...members.map(chat=>['chat/'+chat,base]),[force,null],...(reset?[[setting,null]]:[])],'finish');
      }catch{
        error.textContent='Could not load all chats in this group. The override is still on.';
        return false;
      }
    }
    async function save(value) {
      if (value !== null && !hex.test(value)) return;
      if(value===null && (project || section) && read('override-'+kind,id)==='on'){
        if(await finishOverride(true))box.close();
        return;
      }
      const entries = [[setting,value]];
      if (value === null && (project || section)) entries.push([force,null]);
      if (await change(entries)) box.close();
    }
    if (project || section) {
      const label = el('label','csc-override');
      const toggle = el('input');
      toggle.type = 'checkbox';
      toggle.checked = read('override-' + kind,id) === 'on';
      toggle.onchange = async () => {
        toggle.disabled = true;
        const ok=toggle.checked ? await change([[force,'on']]) : await finishOverride();
        if (!ok) toggle.checked = !toggle.checked;
        toggle.disabled = false;
      };
      label.title='Turning this off saves the current group colour on each chat.';
      label.append(toggle, document.createTextNode('Override all chat colours'));
      box.append(label);
    }
    const grid = el('div', 'csc-grid');
    for (const [name, colour] of palette) {
      const swatch = action('', () => save(colour));
      swatch.title = name;
      swatch.setAttribute('aria-label', name);
      swatch.setAttribute('aria-pressed', String(current?.toLowerCase() === colour));
      swatch.style.backgroundColor = colour;
      grid.append(swatch);
    }
    const saved = el('div');
    let removing = false;
    function showSaved() {
      saved.replaceChildren();
      const keys = [...colourKeys()].filter(k => k.startsWith('swatch/') && read('swatch',k.slice(7))).sort();
      if (!keys.length) { removing = false; return; }
      const header = el('div','csc-saved-heading');
      header.append(el('span',null,'Saved colours'), action(removing ? 'Done' : 'Remove…', () => {
        removing = !removing; showSaved();
      }));
      const swatches = el('div','csc-grid');
      for (const key of keys) {
        const value = '#' + key.slice(7);
        const button = action('', async () => {
          if (!removing) return save(value);
          if (await change([[key,null]])) showSaved();
        });
        button.title = (removing ? 'Remove ' : 'Use ') + value;
        button.setAttribute('aria-label',button.title);
        button.style.backgroundColor = value;
        swatches.append(button);
      }
      saved.append(header,swatches);
    }
    showSaved();
    const customRow = customPicker(current || inherited || '#299e93', save, async colour => {
      if (await change([['swatch/' + colour.slice(1),colour]])) showSaved();
    });
    const footer = el('div', 'csc-footer');
    footer.append(action(kind === 'chat' && owner && (read('project',owner) || !sectionId) ? 'Use project colour' :
      sectionId ? 'Use section colour' : 'Use default colour', () => save(null)),
      action('Cancel', () => box.close()));
    box.append(grid, saved, customRow, footer, error);
    box.addEventListener('close', () => box.remove(), {once: true});
    document.body.append(box);
    box.showModal();
  }

  function menuItem(kind, id) {
    return {
      id: 'sidebar-colours-' + kind,
      message: {id: 'local.sidebarColours.' + kind, defaultMessage: kind === 'project' ? 'Project colour…' : 'Chat colour…'},
      onSelect: () => requestAnimationFrame(() => open(kind, id)),
    };
  }
  // This alternate gesture also works on rows whose regular actions are hidden.
  function targetRow(target) {
    if (!(target instanceof Element)) return null;
    const row = target.closest('[' + threadAttr + '],[' + projectAttr + ']');
    if (row) return row;
    return target.closest(sectionToggle)?.closest(sectionSelector);
  }
  function showForRow(e, row) {
    e.preventDefault();
    e.stopImmediatePropagation();
    if (row.hasAttribute(threadAttr)) open('chat', row.getAttribute(threadAttr));
    else if (row.hasAttribute(projectAttr)) open('project', row.getAttribute(projectAttr));
    else open('section',row.getAttribute(sectionAttr).slice(7));
  }
  window.addEventListener('contextmenu', e => {
    const row = e.shiftKey && targetRow(e.target);
    if (row) showForRow(e, row);
  }, true);
  window.addEventListener('keydown', e => {
    const row = e.altKey && e.shiftKey && e.code === 'KeyC' && targetRow(e.target);
    if (row) showForRow(e, row);
  }, true);
  window.addEventListener('csc-open-section-colour', e => {
    if (typeof e.detail === 'string') requestAnimationFrame(() => open('section',e.detail));
  });
  ready.then(() => {
    watchPreferencePrefix(sectionStore, refresh);
    watchPreferencePrefix(prefix, refresh);
    refresh();
  });
  async function migrate(temporary, stable) {
    if (!temporary || !stable || temporary === stable) return;
    await ready;
    const old = read('chat', temporary);
    if (!hex.test(old)) return;
    const edits = [['chat/' + temporary,null]];
    if (!read('chat',stable)) edits.push(['chat/' + stable,old]);
    await write(edits).catch(() => {});
  }
  return {menuItem, migrate, activityDay};
}

// Paint the relationship in the row gutter without changing native order or hit targets.
function installForkLines() {
  if (document.getElementById('csc-fork-lines')) return;
  const attr = 'data-csc-fork-id', selector = '[' + attr + ']';
  const namespace = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(namespace, 'svg');
  svg.id = 'csc-fork-lines';
  svg.setAttribute('aria-hidden', 'true');
  svg.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:2;overflow:hidden;';
  const path = document.createElementNS(namespace, 'path');
  path.setAttribute('fill', 'none');
  path.setAttribute('stroke', 'var(--color-text-secondary,GrayText)');
  path.setAttribute('stroke-opacity', '0.55');
  path.setAttribute('stroke-width', '1');
  svg.append(path); document.body.append(svg);
  let frame, observed, reading = false;
  const forkParents = new Map(), attempted = new Map();
  const parentOf = row => row.dataset.cscForkParent || (row.dataset.cscForkHost === 'local' && forkParents.get(row.dataset.cscForkId));
  function fetchParents(rows) {
    if (reading || !window.localChatDetails?.forkParents) return;
    const now = Date.now();
    const ids = [...new Set(rows.filter(r => r.dataset.cscForkHost==='local' && !r.dataset.cscForkParent)
      .map(r => r.dataset.cscForkId))].filter(id => !forkParents.has(id) && now-(attempted.get(id)||0)>30000).slice(0,128);
    if (!ids.length) return;
    reading = true;
    for (const id of ids) attempted.set(id,now);
    window.localChatDetails.forkParents(ids).then(parents => {
      for (const [id,parent] of Object.entries(parents)) forkParents.set(id,parent);
    }).catch(() => {}).finally(() => { reading=false; schedule(); });
  }
  const sizes = new ResizeObserver(schedule);
  const lists = new Set();
  const groupOf = row => row.closest('[role="list"],[data-app-action-sidebar-project-list-id],[data-csc-project-group],[data-sidebar-project-container-id],[data-app-action-sidebar-section]');
  function afterLayout(event) {
    if (observed?.contains(event.target)) schedule();
  }
  for (const type of ['transitionend', 'animationend', 'transitioncancel', 'animationcancel'])
    document.addEventListener(type, afterLayout, true);
  function schedule() { if (!frame) frame = requestAnimationFrame(draw); }
  function draw() {
    frame = 0;
    const rows = Array.from(document.querySelectorAll(selector));
    fetchParents(rows);
    const branch = rows.find(parentOf);
    const sidebar = branch?.closest('[data-app-shell-focus-area="sidebar"],nav,aside') || branch?.parentElement;
    if (observed !== sidebar) {
      sizes.disconnect(); lists.clear(); if (sidebar) sizes.observe(sidebar);
      observed = sidebar;
    }
    const nextLists = new Set(branch ? rows.map(groupOf).filter(Boolean) : []);
    for (const list of lists) if (!nextLists.has(list)) { sizes.unobserve(list); lists.delete(list); }
    for (const list of nextLists) if (!lists.has(list)) { sizes.observe(list); lists.add(list); }
    const segments = [];
    for (const child of rows) {
      const parentId = parentOf(child);
      if (!parentId || !child.clientHeight) continue;
      const a = child.getBoundingClientRect();
      const choices = rows.filter(row => row !== child && row.getAttribute(attr) === parentId
        && row.dataset.cscForkHost === child.dataset.cscForkHost && groupOf(row) === groupOf(child) && row.clientHeight);
      // Prefer the nearest copy when a chat appears in more than one list.
      choices.sort((x,y) => Math.abs(x.getBoundingClientRect().top-a.top) - Math.abs(y.getBoundingClientRect().top-a.top));
      if (!choices.length) continue;
      const parent = choices[0], b = parent.getBoundingClientRect();
      const y1 = Math.round(b.top+b.height/2)+0.5, y2 = Math.round(a.top+a.height/2)+0.5;
      const firstLabel = Math.min(...[parent, child].map(row =>
        (row.querySelector('[data-csc-chat-label]') || row).getBoundingClientRect().left));
      const x = Math.round(Math.min(a.left + 3, b.left + 3, firstLabel - 7)) + 0.5;
      // Clipped or virtualised rows must not connect across headers or other panes.
      if (y1<0 || y2<0 || Math.max(y1,y2)>=innerHeight || x<0) continue;
      if (!document.elementFromPoint(b.left+6,y1)?.closest(selector)?.isSameNode(parent)
        || !document.elementFromPoint(a.left+6,y2)?.closest(selector)?.isSameNode(child)) continue;
      segments.push(`M${x+4},${y1}H${x}V${y2}H${x+4}`);
    }
    const d=segments.join(' ');
    if (path.getAttribute('d')!==d) path.setAttribute('d',d);
  }
  new MutationObserver(records => {
    if (frame) return;
    for (const r of records) {
      if (r.type==='attributes' || [...r.addedNodes,...r.removedNodes].some(n =>
        n.nodeType===1 && (n.matches(selector) || n.querySelector(selector)))) { schedule(); return; }
    }
  }).observe(document.body,{subtree:true,childList:true,attributes:true,
    attributeFilter:[attr,'data-csc-fork-parent','data-csc-fork-host','data-app-shell-sidebar-open','data-app-action-sidebar-section-collapsed']});
  document.addEventListener('scroll', e => {
    if (observed && (e.target===document || e.target.contains?.(observed) || observed.contains(e.target))) schedule();
  },{capture:true,passive:true});
  window.addEventListener('resize',schedule);
  schedule();
}

function installDeliveryStatus() {
  const read=window.localChatDetails?.delivery;
  if (!read) return;
  const style=document.createElement('style');
  style.textContent=`
    [data-csc-delivery] [data-csc-chat-label]::after {content:attr(data-sync-mark);font-size:.78em;margin-inline-start:.4em;opacity:0}
    [data-csc-delivery]:is(:hover,:focus-within) [data-csc-chat-label]::after,
    [data-csc-delivery="busy"] [data-csc-chat-label]::after,
    [data-csc-delivery="conflict"] [data-csc-chat-label]::after {opacity:.8}
  `;
  document.head.append(style);
  let busy=false;
  const names={saved:'Saved to sync folder',busy:'Waiting for this chat to close',conflict:'Two desktops edited this chat',
    local_only:'Kept on this device',waiting:'Waiting for sync files'};
  async function update(){
    if (document.hidden || busy) return;
    busy=true;
    try {
      const rows=new Map();
      for (const row of document.querySelectorAll('[data-csc-chat-key]')){
        const id=/^(?:codex:thread:)?local:([a-f0-9-]{36})$/.exec(row.getAttribute('data-csc-chat-key'))?.[1];
        if (id){if (!rows.has(id)) rows.set(id,[]);rows.get(id).push(row);}
      }
      const ids=[...rows.keys()];
      for(let n=0;n<ids.length;n+=128){
        const batch=ids.slice(n,n+128), records=await read(batch);
        for (const id of batch) for (const row of rows.get(id)){
          const label=row.querySelector('[data-csc-chat-label]');if(!label)continue;
          const r=records[id];
          if(!r){if(row.hasAttribute('data-csc-delivery')){row.removeAttribute('data-csc-delivery');label.removeAttribute('data-sync-mark');if(label.title===label.dataset.syncTitle)label.removeAttribute('title');delete label.dataset.syncTitle;}continue;}
          const received=(r.received_by||[]).map(p=>p.name), waiting=(r.waiting_on||[]).map(p=>p.name);
          const origin=r.origin ? (r.origin.basis==='started'?'Started on ':'First seen on ')+r.origin.name : null;
          const message=[...(origin?[origin]:[]),names[r.status] || ('Last synced version received by '+received.join(', ')), ...(waiting.length?['Waiting for '+waiting.join(', ')+' to close the chat']:[])].join(' · ');
          row.dataset.cscDelivery=waiting.length?'busy':r.status;
          label.dataset.syncMark=({received:'✓',busy:'◷',conflict:'!',local_only:'⌂'})[row.dataset.cscDelivery]||'·';
          label.title=message;label.dataset.syncTitle=message;
        }
      }
    } finally {busy=false;}
  }
  const refresh=()=>update().catch(()=>{});
  window.setInterval(refresh,5000);window.addEventListener('focus',refresh);refresh();
}
