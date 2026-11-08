// Loaded alongside the desktop sidebar renderer. Each key is one local preference,
// so edits to different items in separate windows cannot overwrite each other.
function createSidebarColours({preferencesReady, getPreference, setPreference, preferenceKeys, watchPreferencePrefix}) {
  const prefix = 'codex.sidebar-colours.v1/';
  const projectAttr = 'data-app-action-sidebar-project-id';
  const projectListAttr = 'data-app-action-sidebar-project-list-id';
  const threadAttr = 'data-csc-chat-key';
  const rawThreadAttr = 'data-app-action-sidebar-thread-id';
  const ownerAttr = 'data-csc-owner';
  const hex = /^#[0-9a-f]{6}$/i;
  let rules, dialog;
  const ready = preferencesReady();

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
  function customPicker(initial, save) {
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
    row.append(swatch, action('Apply custom colour', () => save(swatch.value)));
    section.append(wheel, brightness, row);
    return section;
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
    const customRow = customPicker(current || inherited || '#299e93', save);
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
}
