// The window hands its saved views and text animations to the main process and applies what it receives.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const {createHash, randomUUID} = require('node:crypto');
const {chromium} = require('/usr/lib/chatgpt/resources/cua_node/lib/node_modules/playwright-core');

(async()=>{
  const dir = await fs.mkdtemp(path.join(os.tmpdir(),'marks-bridge-'));
  const browser = await chromium.launch({channel:'chrome',chromiumSandbox:true});
  try {
    const state = path.join(dir,'chat-sync');
    await fs.mkdir(state);
    const account = createHash('sha256').update('first').digest('hex');
    await fs.writeFile(path.join(dir,'auth.json'),JSON.stringify({tokens:{account_id:'first'}}));
    const code = await fs.readFile(path.join(__dirname,'..','chat-details-main.cjs'),'utf8');
    let exchange;
    const frame={url:'app://-/index.html'},event={sender:{mainFrame:frame},senderFrame:frame};
    new Function('require','process',code)(key=>key==='electron'?{ipcMain:{handle:(id,fn)=>{
      if(id==='local-marks:exchange') exchange=fn;
    }}}:require(key),{env:{CODEX_HOME:dir}});
    await assert.rejects(()=>exchange({...event,senderFrame:{}},{}));
    const read = async name=>JSON.parse(await fs.readFile(path.join(state,name),'utf8'));
    const present = name=>fs.access(path.join(state,name)).then(()=>true,()=>false);
    const until = async (check,label)=>{
      for(let i=0;i<100;i++){if(await check().catch(()=>false))return;await new Promise(r=>setTimeout(r,100));}
      throw Error('Timed out: '+label);
    };

    const page = await browser.newPage();
    await page.route('http://localhost/',route=>route.fulfill({contentType:'text/html',body:'<aside data-app-action-sidebar-scroll></aside>'}));
    await page.goto('http://localhost/');
    await page.exposeFunction('marksExchange',payload=>exchange(event,payload));
    const [kept,incoming] = [randomUUID(),randomUUID()];
    const view = name=>({name,project:'',origin:'',age:'7',waiting:''});
    const word = JSON.stringify([JSON.stringify(['cloud','message']),2,6,'abc']);
    await page.evaluate(([account,kept,view,word])=>{
      localStorage.setItem('codex.mods.saved-view.v1/'+account+'/'+kept,JSON.stringify(view));
      localStorage.setItem('codex.mods.saved-view.v1/someone-else/'+kept,JSON.stringify(view));
      localStorage.setItem('codex.mods.animated-words.v1/'+word,'1');
    },[account,kept,view('Mine'),word]);
    await page.addScriptTag({path:path.join(__dirname,'..','sidebar-views.js')});
    const start = ()=>page.evaluate(()=>{
      window.localChatDetails={savedViewData:async()=>({}),openChat(){},marks:marksExchange};installSidebarViews();
    });

    // Nothing leaves the window while the option is off.
    await fs.writeFile(path.join(state,'config.json'),JSON.stringify({marks:false}));
    await start();
    await new Promise(r=>setTimeout(r,400));
    assert.equal(await present('renderer-marks.json'),false);

    await fs.writeFile(path.join(state,'config.json'),JSON.stringify({marks:true}));
    await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
    await until(()=>present('renderer-marks.json'),'first snapshot');
    const snapshot = await read('renderer-marks.json');
    assert.deepEqual([snapshot.account,snapshot.views,snapshot.words],[account,{[kept]:view('Mine')},[word]]);

    // Instructions from another desktop add a view and remove the animation.
    const todo = {account,id:'batch-1',set:{['view/'+incoming]:view('Theirs')},remove:['word/'+word]};
    await fs.writeFile(path.join(state,'renderer-marks-apply.json'),JSON.stringify(todo));
    await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
    await until(async()=>!(await present('renderer-marks-apply.json')),'acknowledged instructions');
    const stored = await page.evaluate(()=>Object.fromEntries(Object.entries(localStorage)));
    assert.deepEqual(JSON.parse(stored['codex.mods.saved-view.v1/'+account+'/'+incoming]),view('Theirs'));
    assert.equal(stored['codex.mods.animated-words.v1/'+word],undefined);
    await until(async()=>Object.keys((await read('renderer-marks.json')).views).length===2,'updated snapshot');
    assert.deepEqual((await read('renderer-marks.json')).words,[]);

    // Instructions for another account are neither returned nor removed.
    await fs.writeFile(path.join(state,'renderer-marks-apply.json'),JSON.stringify({...todo,account:'other',id:'batch-2'}));
    assert.equal((await exchange(event,{applied:'batch-2'})).apply,null);
    assert.equal(await present('renderer-marks-apply.json'),true);
    console.log('Passed opt-in snapshot, account scope, received views and removals, and acknowledgement.');
  } finally {await browser.close();await fs.rm(dir,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
