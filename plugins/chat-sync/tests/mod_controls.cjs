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
    await panel.getByRole('button', {name:'Check folder'}).click();
    await panel.getByText(/No shared chat revisions to check/).waitFor();
    await panel.getByRole('button', {name:'Pause sync'}).click();
    await panel.getByText('Sync paused', {exact:true}).waitFor();
    assert.ok(await panel.getByText('Sync now').isDisabled());
    await panel.getByRole('button', {name:'Resume sync'}).click();
    await panel.getByText('Manual sync', {exact:true}).waitFor();
    await panel.getByRole('button', {name:'Sync now'}).click();
    await panel.getByText('Failed', {exact:true}).waitFor();
    assert.ok(await panel.getByText('Last attempt', {exact:true}).isVisible());
    const {DatabaseSync} = require('node:sqlite');
    const db = new DatabaseSync(path.join(home, 'state_5.sqlite'));
    db.exec('CREATE TABLE threads(id,rollout_path,name,archived,source,cwd)');
    db.close();
    const tid = 'aaaaaaaa-1111-4111-8111-111111111111';
    const transcript = JSON.stringify({type:'session_meta',payload:{id:tid,source:'cli'}})+'\n';
    await fs.mkdir(path.join(home,'archived_sessions'));
    await fs.writeFile(path.join(home,'archived_sessions','rollout-2026-11-08T12-00-00-'+tid+'.jsonl'),transcript);
    await fs.mkdir(path.join(home,'thread-writer-locks'));
    await fs.writeFile(path.join(home,'thread-writer-locks','.coordination.lock'),'');
    await panel.getByRole('button',{name:'Sync now'}).click();
    await panel.getByText('Completed',{exact:true}).waitFor();
    const key=require('node:crypto').createHash('sha256').update(transcript).digest('hex');
    const block=path.join(folder,'codex-chat-sync-v1','blocks',key.slice(0,2),key+'.gz');
    await fs.unlink(block);
    await panel.getByRole('button',{name:'Check folder'}).click();
    await panel.getByText(/missing blocks: 1/).waitFor();
    await panel.getByRole('button',{name:'Repair from this desktop'}).click();
    await panel.getByText(/Blocks restored: 1/).waitFor();
    assert.equal(require('node:zlib').gunzipSync(await fs.readFile(block)).toString(),transcript);
    await page.setViewportSize({width:360,height:740});
    const frame = page.frames().find(frame => frame.parentFrame());
    assert.ok(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    console.log('Passed disconnected, preference persistence, pause, resume, sync failure, check, repair and narrow layout in Chromium.');
  } finally {
    child.stdin.end();
    if (browser) await browser.close();
    lines.close();
    await fs.rm(root, {recursive:true, force:true});
  }
}
run().catch(error => {console.error(error); process.exitCode = 1});
