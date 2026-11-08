const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('/usr/lib/chatgpt/resources/cua_node/lib/node_modules/playwright-core');

async function run() {
  const browser = await chromium.launch({channel: 'chrome', chromiumSandbox: true});
  try {
    const page = await browser.newPage();
    await page.setContent(`
      <div data-app-action-sidebar-project-id="project"><span data-csc-project-label>Project</span></div>
      <div data-app-action-sidebar-project-list-id="project">
        <div data-csc-chat-key="local:chat"><span data-csc-chat-label>Local chat</span></div>
      </div>
      <div data-csc-project-group="project">
        <div data-csc-chat-key="cloud:other"><span data-csc-chat-label>Linked chat</span></div>
      </div>
      <div data-csc-chat-key="local:chat" data-csc-owner="project"><span data-csc-chat-label>Recents</span></div>
    `);
    await page.addScriptTag({path: path.join(__dirname, '..', 'sidebar-colours.js')});
    const result = await page.evaluate(async () => {
      const prefs = new Map();
      const prefix = 'codex.sidebar-colours.v1/';
      prefs.set(prefix + 'project/project', '#cc2a1d');
      let refresh;
      createSidebarColours({
        preferencesReady: () => Promise.resolve(),
        getPreference: key => prefs.get(key),
        preferenceKeys: () => prefs.keys(),
        watchPreferencePrefix: (_, listener) => { refresh = listener; },
      });
      await Promise.resolve();
      const root = document.documentElement;
      root.style.setProperty('--csc-clock-day', '21000');
      const rows = document.querySelectorAll('[data-csc-chat-key]');
      const labels = document.querySelectorAll('[data-csc-chat-label]');
      const ctx = document.createElement('canvas').getContext('2d');
      function colour(node) {
        ctx.fillStyle = getComputedStyle(node).color;
        ctx.fillRect(0, 0, 1, 1);
        return Array.from(ctx.getImageData(0, 0, 1, 1).data).slice(0, 3);
      }
      function colours(age) {
        for (const row of rows) {
          if (age == null) row.removeAttribute('data-csc-activity-day');
          else row.setAttribute('data-csc-activity-day', String(21000 - age));
        }
        return Array.from(labels, colour);
      }
      return ['dark', 'light'].map(theme => {
        root.style.colorScheme = theme;
        const recent = colours(1), middle = colours(7), old = colours(14);
        const expired = colours(40), undated = colours(null);
        const overrideKey = prefix + 'chat/local:chat';
        prefs.set(overrideKey, '#299e93');
        refresh();
        const override = colours(0);
        prefs.delete(overrideKey);
        refresh();
        return {heading: colour(document.querySelector('[data-csc-project-label]')), recent, middle,
          old, expired, undated, override, reset: colours(0)};
      });
    });
    const base = [204, 42, 29], teal = [41, 158, 147];
    for (const {heading, recent, middle, old, expired, undated, override, reset} of result) {
      assert.deepEqual(heading, base);
      assert.deepEqual(recent, [base, base, base], 'fresh chats should match their project exactly');
      assert.deepEqual(undated, recent);
      assert.deepEqual(reset, recent);
      assert.deepEqual(override, [teal, base, teal]);
      assert.deepEqual(expired, old);
      for (const shades of [middle, old]) {
        for (const shade of shades) {
          assert.deepEqual(shade, shades[0]);
          const factor = shade[0] / base[0];
          assert(factor > 0 && factor < 1);
          assert(shade.every((channel, index) => Math.abs(channel - base[index] * factor) <= 1));
        }
      }
      assert(middle[0][0] > old[0][0]);
    }
    console.log('PASS exact project colours, same-hue ageing, Recents, override and reset in both themes.');
  } finally {
    await browser.close();
  }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
