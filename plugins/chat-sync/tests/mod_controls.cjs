// Exercise the sandboxed UI with the real plugin server and a disposable profile.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const {chromium} = require('/usr/lib/chatgpt/resources/cua_node/lib/node_modules/playwright-core');

async function run() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'mod-controls-ui-'));
  const home = path.join(root, 'profile');
  const child = spawn('python3', [path.join(__dirname, '..', 'server.py')], {
    env: {...process.env, CODEX_HOME:home}, stdio:['pipe', 'pipe', 'inherit']
  });
  const lines = readline.createInterface({input:child.stdout});
  const waiting = new Map();
  lines.on('line', line => {
    const msg = JSON.parse(line);
    waiting.get(msg.id)?.(msg);
    waiting.delete(msg.id);
  });
  let id = 0, browser;
  function request(method, params) {
    return new Promise(resolve => {
      const seq = ++id;
      waiting.set(seq, resolve);
      child.stdin.write(JSON.stringify({jsonrpc:'2.0', id:seq, method, params}) + '\n');
    });
  }
  try {
    const split = await request('resources/read', {uri:'ui://chat-sync/mod-controls.html'});
    const html = split.result.contents[0].text;
    browser = await chromium.launch({channel:'chrome', chromiumSandbox:true});
    const page = await browser.newPage({viewport:{width:1000,height:800}});
    await page.exposeFunction('pluginCall', params => request('tools/call', params));
    await page.setContent('<iframe title="Mod Controls" sandbox="allow-scripts" style="width:100%;height:735px"></iframe>');
    await page.evaluate(html => {
      window.addEventListener('message', async event => {
        const m = event.data;
        if (m.jsonrpc !== '2.0' || !m.method || m.id == null) return;
        let reply;
        if (m.method === 'ui/initialize') reply = {result:{protocolVersion:'2026-01-26',hostCapabilities:{},hostContext:{theme:'dark'}}};
        else if (m.method === 'tools/call') reply = await window.pluginCall(m.params);
        else throw Error('Unexpected request ' + m.method);
        event.source.postMessage({...reply, id:m.id, jsonrpc:'2.0'}, '*');
      });
      document.querySelector('iframe').srcdoc = html;
    }, html);
    const panel = page.frameLocator('iframe');
    await panel.getByText('Disconnected', {exact:true}).waitFor();
    assert.ok(await panel.getByText('Sync now').isDisabled());
    const dock = panel.getByRole('switch', {name:'Pet in the chat margin'});
    const motion = panel.getByRole('switch', {name:'Pet runs while scrolling'});
    await dock.uncheck();
    await panel.getByText('Saved on this computer.').waitFor();
    assert.ok(await motion.isDisabled());
    assert.equal(JSON.parse(await fs.readFile(path.join(home, 'desktop-mod-controls.json'))).petDock, false);
    const folder = path.join(root, 'folder');
    await fs.mkdir(folder);
    await request('tools/call', {name:'configure_chat_sync', arguments:{folder, automatic:false}});
    await panel.getByRole('button', {name:'Refresh'}).click();
    await panel.getByText('Manual sync', {exact:true}).waitFor();
    await panel.getByRole('button', {name:'Pause sync'}).click();
    await panel.getByText('Sync paused', {exact:true}).waitFor();
    assert.ok(await panel.getByText('Sync now').isDisabled());
    await panel.getByRole('button', {name:'Resume sync'}).click();
    await panel.getByText('Manual sync', {exact:true}).waitFor();
    await panel.getByRole('button', {name:'Sync now'}).click();
    await panel.getByText('Failed', {exact:true}).waitFor();
    assert.ok(await panel.getByText('Last attempt', {exact:true}).isVisible());
    await page.setViewportSize({width:360,height:740});
    const frame = page.frames().find(frame => frame.parentFrame());
    assert.ok(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    console.log('Passed disconnected, preference persistence, pause, resume, sync failure and narrow layout in Chromium.');
  } finally {
    child.stdin.end();
    if (browser) await browser.close();
    lines.close();
    await fs.rm(root, {recursive:true, force:true});
  }
}
run().catch(error => {console.error(error); process.exitCode = 1});
