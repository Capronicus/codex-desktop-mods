import {
  $Bn as sidebarColourLocalProjects, zcr as initializePreferences, $cr as preferencesReady,
  Lcr as getPreference, qcr as setPreference,
  Rcr as preferenceKeys, Qcr as watchPreferencePrefix,
} from './app-initial-fdea07eb2719.js';

// Loaded alongside the desktop sidebar renderer. Each key is one local preference,
// so edits to different items in separate windows cannot overwrite each other.
const sidebarColours = (() => {
  const prefix = 'codex.sidebar-colours.v1/';
  const projectAttr = 'data-app-action-sidebar-project-id';
  const projectListAttr = 'data-app-action-sidebar-project-list-id';
  const threadAttr = 'data-csc-chat-key';
  const rawThreadAttr = 'data-app-action-sidebar-thread-id';
  const ownerAttr = 'data-csc-owner';
  const hex = /^#[0-9a-f]{6}$/i;
  let rules, dialog;
  initializePreferences();
  const ready = preferencesReady();

  const displayRules = document.createElement('style');
  displayRules.textContent = `
    .csc-picker { color-scheme: inherit; font: inherit; font-size: 14px; padding: 20px;
      color: var(--color-text-primary, CanvasText); background: var(--color-background-surface, Canvas);
      border: 1px solid color-mix(in srgb, currentColor 25%, transparent); border-radius: 16px;
      box-shadow: 0 12px 48px #0004; width: min(300px, calc(100vw - 40px)); margin: auto; }
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
    .csc-footer { justify-content: space-between; }
    .csc-action { background: transparent; border-radius: 7px; padding: 5px 8px; color: inherit; font: inherit; }
    .csc-action:hover { background: color-mix(in srgb, currentColor 12%, transparent); }
    [data-csc-chat-key][data-csc-activity-day] {
      --csc-freshness: clamp(0%, calc((attr(data-csc-activity-day type(<number>)) - var(--csc-clock-day) + 14) * 100% / 13), 100%);
      --csc-chat-tone: color-mix(in srgb, var(--csc-new) var(--csc-freshness), var(--csc-old));
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

  function key(kind, id) { return prefix + kind + '/' + id; }
  function attr(name, id) { return '[' + name + '="' + CSS.escape(id) + '"]'; }
  function read(kind, id) { return id ? getPreference(key(kind, id), null) : null; }

  function blend(rgb, target, amount) {
    return rgb.map((value, index) => {
      const end = Array.isArray(target) ? target[index] : target;
      return Math.round(value + (end - value) * amount);
    });
  }
  function luminance(rgb) {
    const [r, g, b] = rgb.map(channel => {
      const s = channel / 255;
      return s <= .04045 ? s / 12.92 : ((s + .055) / 1.055) ** 2.4;
    });
    return .2126 * r + .7152 * g + .0722 * b;
  }
  function readable(rgb, backdrop, minimum, toward) {
    const bg = luminance([backdrop, backdrop, backdrop]) + .05;
    for (let step = 0; step <= 20; step++) {
      const shade = blend(rgb, toward, step / 20);
      const ratio = (luminance(shade) + .05) / bg;
      if (Math.max(ratio, 1 / ratio) >= minimum) return shade;
    }
    return blend(rgb, toward, 1);
  }
  function chatRules(selectors, value) {
    const base = value.slice(1).match(/../g).map(channel => parseInt(channel, 16));
    const recentLight = readable(blend(base, 255, .15), 238, 4.5, 0);
    const vivid = base.map(channel => Math.min(255, Math.round(channel * 1.18)));
    const recentDark = readable(vivid, 36, 7, 255);
    const oldLight = blend(recentLight, 0, .35);
    const oldDark = readable(blend(base, 0, .48), 36, 4.5, recentDark);
    const themes = (light, dark) => 'light-dark(rgb(' + light.join(' ') + '),rgb(' + dark.join(' ') + '))';
    return selectors.join(',') + '{--csc-row-base:' + value + ';--csc-new:' + themes(recentLight, recentDark)
      + ';--csc-old:' + themes(oldLight, oldDark) + '}' +
      selectors.map(s => s + ' [data-csc-chat-label]').join(',') + '{color:var(--csc-chat-tone,var(--csc-row-base)) !important}';
  }

  function refresh() {
    const projects = [], chats = [];
    for (const k of preferenceKeys(prefix)) {
      const value = getPreference(k, null);
      if (!hex.test(value)) continue;
      const parts = k.slice(prefix.length);
      const split = parts.indexOf('/');
      const kind = parts.slice(0, split), id = parts.slice(split + 1);
      if (!id || split < 0) continue;
      if (kind === 'project') {
        projects.push(attr(projectAttr, id) + ' [data-csc-project-label]{color:' + value + ' !important}');
        const descendant = ' :where([' + threadAttr + '])';
        projects.push(chatRules([attr(projectListAttr, id) + descendant,
          attr('data-csc-project-group', id) + descendant, attr(ownerAttr, id)], value));
      } else if (kind === 'chat') {
        chats.push(chatRules([attr(threadAttr, id)], value));
      }
    }
    if (!rules) {
      rules = document.createElement('style');
      rules.id = 'codex-sidebar-colour-rules';
      document.head.append(rules);
    }
    rules.textContent = projects.concat(chats).join('\n');
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
  async function open(kind, id) {
    if (!id) return;
    await ready;
    if (dialog && dialog.open) dialog.close();
    const project = kind === 'project';
    const selector = project ? attr(projectAttr, id) : [attr(threadAttr, id), attr(rawThreadAttr, id)].join(',');
    const row = document.querySelector(selector);
    const savedId = () => project ? id : row?.getAttribute(threadAttr) || id;
    const title = row?.getAttribute('data-app-action-sidebar-' + (project ? 'project-label' : 'thread-title'))
      ?? row?.querySelector('[data-csc-chat-label]')?.textContent;
    const owner = row?.closest('[data-csc-project-group]')?.getAttribute('data-csc-project-group')
      || row?.getAttribute(ownerAttr) || row?.closest('[' + projectListAttr + ']')?.getAttribute(projectListAttr);
    const current = read(kind, savedId());
    const inherited = project ? null : read('project', owner);
    const box = el('dialog', 'csc-picker');
    dialog = box;
    const heading = el('h2', null, project ? 'Project colour' : 'Chat colour');
    heading.id = 'csc-picker-heading';
    box.setAttribute('aria-labelledby', heading.id);
    box.append(heading);
    if (title) box.append(el('p', null, title));
    const error = el('div');
    error.setAttribute('role', 'alert');
    function save(value) {
      try {
        if (value === null) setPreference(key(kind, savedId()), undefined);
        else if (hex.test(value)) setPreference(key(kind, savedId()), value);
        else return;
        box.close();
      } catch { error.textContent = 'Could not save the colour in this profile.'; }
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
    const customRow = el('div', 'csc-custom');
    const custom = el('input');
    custom.type = 'color';
    custom.value = current || inherited || '#299e93';
    custom.setAttribute('aria-label', 'Custom colour');
    customRow.append(custom, action('Apply custom colour', () => save(custom.value)));
    const footer = el('div', 'csc-footer');
    footer.append(action(project || !owner ? 'Use default colour' : 'Use project colour', () => save(null)),
      action('Cancel', () => box.close()));
    box.append(grid, customRow, footer, error);
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
    return target instanceof Element ? target.closest('[' + threadAttr + '],[' + projectAttr + ']') : null;
  }
  function showForRow(e, row) {
    e.preventDefault();
    e.stopImmediatePropagation();
    if (row.hasAttribute(threadAttr)) open('chat', row.getAttribute(threadAttr));
    else open('project', row.getAttribute(projectAttr));
  }
  window.addEventListener('contextmenu', e => {
    const row = e.shiftKey && targetRow(e.target);
    if (row) showForRow(e, row);
  }, true);
  window.addEventListener('keydown', e => {
    const row = e.altKey && e.shiftKey && e.code === 'KeyC' && targetRow(e.target);
    if (row) showForRow(e, row);
  }, true);
  ready.then(() => {
    watchPreferencePrefix(prefix, refresh);
    refresh();
  });
  async function migrate(temporary, stable) {
    if (!temporary || !stable || temporary === stable) return;
    await ready;
    const old = read('chat', temporary);
    if (!hex.test(old)) return;
    if (!read('chat', stable)) setPreference(key('chat', stable), old);
    setPreference(key('chat', temporary), undefined);
  }
  return {menuItem, migrate, activityDay};
})();
