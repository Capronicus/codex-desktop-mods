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
  // Simulate an open desktop without starting a worker that waits on the user's app.
  const fixture = [
    'import runpy, desktop_layout',
    'desktop_layout.desktop_running = lambda: True',
    'desktop_layout.start_worker = lambda home: None',
    'runpy.run_path("server.py", run_name="__main__")'
  ].join('\n');
  const child = spawn('python3', ['-c', fixture], {
    cwd:path.join(__dirname,'..'), env: {...process.env, CODEX_HOME:home}, stdio:['pipe', 'pipe', 'inherit']
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
    assert.ok(await panel.getByRole('heading',{name:'Sidebar changes'}).isHidden());
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
    await panel.getByText('Device name and emoji',{exact:true}).click();
    await panel.getByLabel('Device name',{exact:true}).fill('Work');
    await panel.getByLabel('Origin emoji').fill('💼');
    await panel.getByRole('button',{name:'Save device'}).click();
    await panel.getByText('💼 Work',{exact:true}).waitFor();
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
    const tid = 'aaaaaaaa-1111-4111-8111-111111111111';
    db.prepare('INSERT INTO threads(id,name,source,archived) VALUES (?,?,?,?)').run(tid,'Home fixture','cli',1);
    db.close();
    const transcript = JSON.stringify({type:'session_meta',payload:{id:tid,source:'cli'}})+'\n';
    await fs.mkdir(path.join(home,'archived_sessions'));
    await fs.writeFile(path.join(home,'archived_sessions','rollout-2026-11-08T12-00-00-'+tid+'.jsonl'),transcript);
    await fs.mkdir(path.join(home,'thread-writer-locks'));
    await fs.writeFile(path.join(home,'thread-writer-locks','.coordination.lock'),'');
    await panel.getByRole('button',{name:'Pause sync'}).click();
    await panel.getByRole('button',{name:'Preview outgoing chats'}).click();
    await panel.getByText('New on this desktop · Archived').waitFor();
    await panel.getByRole('button',{name:'Keep local · Home fixture'}).click();
    await panel.getByText('Excluded by chat rule · Archived').waitFor();
    await page.setViewportSize({width:360,height:740});
    assert.ok(await page.frames().find(f=>f.parentFrame()).evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await panel.getByLabel('Chat title or ID').fill('absent');
    await panel.getByRole('button',{name:'Search',exact:true}).click();
    await panel.getByText('No matching local chats.').waitFor();
    await panel.getByLabel('Chat title or ID').fill('Home');
    await panel.getByLabel('Chat title or ID').press('Enter');
    await panel.getByText('Excluded by chat rule · Archived').waitFor();
    await panel.getByRole('combobox',{name:'Chat status',exact:true}).selectOption('excluded');
    await panel.getByRole('button',{name:'Remove chat rule · Home fixture'}).click();
    await panel.getByText('Chat exclusion removed.').waitFor();
    assert.deepEqual(await fs.readdir(path.join(folder,'codex-chat-sync-v1')),[]);
    assert.ok(await panel.getByRole('button',{name:'Sync now'}).isDisabled());
    await panel.getByRole('button',{name:'Resume sync'}).click();
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
    const project=require('node:crypto').randomUUID(), peer=require('node:crypto').randomUUID();
    const sqlite=new DatabaseSync(path.join(home,'state_5.sqlite'));
    sqlite.exec('CREATE TABLE projects(id,name,metadata); CREATE TABLE thread_sections(id,name); ALTER TABLE threads ADD project_id; ALTER TABLE threads ADD thread_section_id');
    sqlite.prepare('INSERT INTO projects VALUES (?,?,?)').run(project,'From Work','{}');
    sqlite.close();
    const layoutFile=path.join(home,'.codex-global-state.json');
    const layout=JSON.stringify({'local-projects':{[project]:{id:project,name:'At Home',rootPaths:[]}}});
    await fs.writeFile(layoutFile,layout);
    const shared=path.join(folder,'codex-chat-sync-v1','sidebar-v1');
    await fs.mkdir(shared);
    const writeHead=name=>fs.writeFile(path.join(shared,peer+'.json'),JSON.stringify({v:1,rows:{['project/'+project]:[3,peer,name]}}));
    await panel.getByText('Sharing options and local-only rules').click();
    await panel.getByRole('switch',{name:'Projects and sections'}).check();
    const review=panel.getByRole('button',{name:'Preview changes'});
    const apply=panel.getByRole('button',{name:'Apply reviewed changes'});
    await writeHead('At Home');
    await review.click();
    await panel.getByText('No incoming changes to apply.').waitFor();
    assert.ok(await apply.isHidden());
    await writeHead('First draft');
    await review.click();
    await panel.getByText('At Home → First draft').waitFor();
    assert.deepEqual(await fs.readdir(shared),[peer+'.json']);
    await writeHead('From Work');
    await apply.click();
    await panel.getByText(/preview changed/).waitFor();
    assert.ok(await apply.isHidden());
    assert.equal(await fs.readFile(layoutFile,'utf8'),layout);
    await review.click();
    await panel.getByText('At Home → From Work').waitFor();
    await apply.click();
    await panel.getByText('Layout changes are queued for when you fully quit the desktop app.').waitFor();
    const queue=path.join(home,'chat-sync','sidebar-layout-pending.json');
    assert.equal(JSON.parse(await fs.readFile(queue)).changes['project/'+project][1],'From Work');
    const undo=panel.getByRole('button',{name:'Undo last batch'});
    const journal=path.join(home,'chat-sync','sidebar-undo.json');
    const latest=JSON.parse(await fs.readFile(journal));
    await fs.writeFile(journal,JSON.stringify({...latest,id:'f'.repeat(64)}));
    await undo.click();
    await panel.getByText(/newer sidebar batch/).waitFor();
    assert.ok(JSON.parse(await fs.readFile(queue)).changes);
    await undo.click();
    await panel.getByText('Undo is queued for when you fully quit the desktop app.').waitFor();
    assert.equal(JSON.parse(await fs.readFile(queue)).batch_id,'f'.repeat(64));
    await panel.getByRole('switch',{name:'Projects and sections'}).uncheck();
    await panel.getByRole('button',{name:'Pause sync'}).click();
    await panel.getByText('Sync paused', {exact:true}).waitFor();
    assert.equal(JSON.parse(await fs.readFile(queue)).undo,true);
    await page.setViewportSize({width:360,height:740});
    const frame = page.frames().find(frame => frame.parentFrame());
    assert.ok(await frame.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    console.log('Passed outgoing search and exclusions while paused, sidebar review and undo, sync controls and narrow layout in Chromium.');
  } finally {
    child.stdin.end();
    if (browser) await browser.close();
    lines.close();
    await fs.rm(root, {recursive:true, force:true});
  }
}
run().catch(error => {console.error(error); process.exitCode = 1});
