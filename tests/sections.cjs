const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('/usr/lib/chatgpt/resources/cua_node/lib/node_modules/playwright-core');

async function run() {
  const browser = await chromium.launch({channel: 'chrome', chromiumSandbox: true});
  try {
    const page = await browser.newPage();
    function row(id, owner = '') {
      return `<div data-csc-chat-key="local:${id}" data-csc-owner="${owner}"><span data-csc-chat-label>${id}</span></div>`;
    }
    await page.setContent(`
      <div data-sidebar-project-container-id="custom:section1">
        <section data-app-action-sidebar-section-heading="Homelab">
          <button data-app-action-sidebar-section-toggle><span>Homelab</span></button>
          ${row('one')}${row('two', 'p')}${row('three','p')}
        </section>
      </div>
      <section>${row('one')}${row('two','p')}${row('three','p')}${row('unrelated')}</section>`);
    await page.addScriptTag({path: path.join(__dirname, '..', 'sidebar-colours.js')});
    await page.evaluate(() => {
      const prefix = 'codex.sidebar-colours.v1/';
      const prefs = new Map([
        [prefix + 'project/p', '#cc2211'], [prefix + 'chat/local:three', '#00cccc'],
        ['sidebar-custom-sections-v3', {account: {sections: [{id:'section1',
          itemKeys:['one','two','three'].map(key => 'codex:thread:local:' + key)}]}}]
      ]);
      const watchers = new Map();
      createSidebarColours({
        preferencesReady: () => Promise.resolve(), getPreference: key => prefs.get(key),
        setPreference(key,value) {
          if (value == null) prefs.delete(key); else prefs.set(key,value);
          for (const [match, callback] of watchers) if (key.startsWith(match)) callback();
        },
        preferenceKeys: () => prefs.keys(), watchPreferencePrefix: (match,cb) => watchers.set(match,cb)
      });
    });
    const title = page.locator('button[data-app-action-sidebar-section-toggle]');
    await title.click({modifiers:['Shift'], button:'right'});
    assert.equal(await page.locator('dialog h2').innerText(), 'Section colour');
    assert.equal(await page.locator('dialog p').innerText(), 'Homelab');
    await page.getByRole('button', {name:'Violet', exact:true}).click();
    const colours = () => page.locator('[data-csc-chat-label]').evaluateAll(labels =>
      labels.map(label => getComputedStyle(label).color));
    const violet = 'rgb(154, 119, 211)', project = 'rgb(204, 34, 17)', chat = 'rgb(0, 204, 204)';
    const native = (await colours()).at(-1);
    assert.equal(await title.evaluate(n => getComputedStyle(n).color), violet);
    assert.deepEqual(await colours(), [violet,project,chat,violet,project,chat,native]);
    const groupPicker = async () => {
      await title.click({modifiers:['Shift'],button:'right'});
      await page.locator('dialog[open]').waitFor();
    };
    await groupPicker();
    await page.getByRole('checkbox',{name:'Override all chat colours'}).check();
    assert.deepEqual(await colours(), [violet,violet,violet,violet,violet,violet,native]);
    await page.getByRole('button',{name:'Save swatch'}).click();
    await page.getByRole('button',{name:'Cancel'}).click();
    await groupPicker();
    await page.getByRole('checkbox',{name:'Override all chat colours'}).uncheck();
    assert.deepEqual(await colours(), [violet,project,chat,violet,project,chat,native]);
    await page.getByRole('button',{name:'Use #9a77d3'}).click();
    await groupPicker();
    await page.getByRole('button',{name:'Remove…'}).click();
    await page.getByRole('button',{name:'Remove #9a77d3'}).click();
    assert.equal(await page.getByText('Saved colours',{exact:true}).count(),0);
    await page.getByRole('button',{name:'Cancel'}).click();
    await page.locator('[data-sidebar-project-container-id] section').evaluate(n => {
      n.setAttribute('data-app-action-sidebar-section-heading', 'Renamed section');
      n.querySelector('button span').textContent = 'Renamed section';
      n.querySelectorAll('[data-csc-chat-key]').forEach(row => row.remove());
    });
    assert.deepEqual(await colours(), [violet, project, chat, native]);
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('csc-open-section-colour',{detail:'section1'})));
    await page.locator('dialog[open]').getByText('Renamed section', {exact:true}).waitFor();
    await page.getByRole('button',{name:'Use default colour'}).click();
    assert.deepEqual(await colours(), [native,project,chat,native]);
    console.log('PASS section picker, inheritance order, Recents, rename, collapse and reset.');
  } finally {
    await browser.close();
  }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
