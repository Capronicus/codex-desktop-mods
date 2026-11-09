// Exercise the desktop's exact colour IPC handler against the disposable test profile.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const handlers = new Map();
const source = fs.readFileSync(path.join(__dirname, '../../../chat-details-main.cjs'), 'utf8');
new Function('require','process',source)(name => name === 'electron'
  ? {ipcMain:{handle:(channel,fn) => handlers.set(channel,fn)}} : require(name),process);
const call = handlers.get('local-sidebar-colours:preferences');
const frame = {url:'app://-/index.html'};
const event = {senderFrame:frame,sender:{mainFrame:frame}};
const [stage,id] = process.argv.slice(2);
const chat = 'chat/local:' + id;
const write = entries => call(event,'write',entries,null);
assert.throws(() => call({...event,senderFrame:{url:'https://example.org'}},'read',[],null));
assert.throws(() => write([['../outside/profile','#123456']]));
if (stage === 'seed') {
  call(event,'seed',[['project/fixture','#654321']],null);
  write([[chat,'#112233'],['override-project/fixture','on'],['swatch/aabbcc','#aabbcc']]);
} else {
  const view = call(event,'read',[],null);
  const values = new Map(view.entries);
  assert.equal(values.get('project/fixture'),'#654321');
  if (stage === 'reply') {
    assert.equal(values.get(chat),'#112233');
    assert.equal(values.get('override-project/fixture'),'on');
    assert.equal(values.get('swatch/aabbcc'),'#aabbcc');
    assert.deepEqual(view.inherited.map(r=>[r.chat,r.project]),[['local:'+id,'fixture']]);
    write([[chat,null],['override-project/fixture',null],['swatch/aabbcc',null],['swatch/bb9900','#bb9900']]);
  } else {
    assert.equal(values.get(chat),null);
    assert.equal(values.get('swatch/aabbcc'),null);
    assert.equal(values.get('override-project/fixture'),null);
    assert.equal(values.get('swatch/bb9900'),'#bb9900');
    // Starting an older native preference seed cannot revive a deleted colour.
    call(event,'seed',[[chat,'#112233']],null);
    assert.equal(new Map(call(event,'read',[],null).entries).get(chat),null);
    // Save an entire group atomically even beyond the ordinary IPC page size.
    write([['project/bulk','#123456'],['override-project/bulk','on']]);
    const batch=Array.from({length:550},(_,i)=>['chat/local:bulk-'+i,'#123456']);
    const release=['override-project/bulk',null];
    assert.throws(()=>call(event,'finish',[...batch,[chat,'#abcdef'],release],null));
    assert.equal(new Map(call(event,'read',[],null).entries).has(batch[0][0]),false);
    const saved=new Map(call(event,'finish',[...batch,release],null).entries);
    assert.equal(saved.get(release[0]),null);
    assert.ok(batch.every(([key,value])=>saved.get(key)===value));
  }
}
