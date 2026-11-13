const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const {randomUUID} = require('node:crypto');
const {DatabaseSync} = require('node:sqlite');
const {chromium} = require('/usr/lib/chatgpt/resources/cua_node/lib/node_modules/playwright-core');

(async()=>{
  const dir = await fs.mkdtemp(path.join(os.tmpdir(),'saved-sidebar-views-'));
  const browser = await chromium.launch({channel:'chrome',chromiumSandbox:true});
  const [work,home,project,native] = Array.from({length:4},()=>randomUUID());
  try {
    const state = path.join(dir,'chat-sync');
    await fs.mkdir(state);
    const write = (file,doc)=>fs.writeFile(file,JSON.stringify(doc));
    await write(path.join(dir,'.codex-global-state.json'),{
      'local-projects':{[project]:{name:'Tartarus'}},
      'app-server-project-id-by-legacy-project-id-by-host':{['local:'+dir]:{[project]:native}}
    });
    await write(path.join(dir,'auth.json'),{tokens:{account_id:'first'}});
    const db = new DatabaseSync(path.join(dir,'state_5.sqlite'));
    db.exec('CREATE TABLE threads(id,name,updated_at,project_id,archived,source)');
    const add = db.prepare('INSERT INTO threads VALUES(?,?,?,?,0,?)');
    const chats = {};
    let expected;
    for (const [name,days,status,from,pid] of [
      ['Pending',1,'saved',work,native],['Delivered',1,'received',work,native],
      ['Old',12,'saved',work,native],['Home origin',1,'saved',home,native],
      ['No project',1,'saved',work,null],['Local only',1,'local_only',null,native]
    ]) {
      const id = randomUUID();
      add.run(id,name,Math.floor(Date.now()/1000)-days*86400,pid,'cli');
      if (name==='Pending') expected=id;
      chats[id]={status,origin:from && {device:from,name:from===home?'Home':'Work'},
        received_by:status==='received'?[{device:home,name:'Home'}]:[],waiting_on:[]};
    }
    db.close();
    await write(path.join(state,'config.json'),{device:work,device_name:'Work'});
    await write(path.join(state,'delivery.json'),{device:work,chats,peers:{[home]:'Home'}});
    const code = await fs.readFile(path.join(__dirname,'..','chat-details-main.cjs'),'utf8');
    let read,opened;
    const frame={url:'app://-/index.html'},event={sender:{mainFrame:frame},senderFrame:frame};
    new Function('require','process',code)(key=>key==='electron'?{ipcMain:{handle:(id,fn)=>{
      if(id==='local-sidebar:saved-view-data') read=fn;
    }}}:require(key),{env:{CODEX_HOME:dir}});
    await assert.rejects(()=>read({...event,senderFrame:{}}));
    const page = await browser.newPage();
    await page.route('http://localhost/',route=>route.fulfill({contentType:'text/html',body:'<aside data-app-action-sidebar-scroll><div>Projects</div></aside>'}));
    await page.goto('http://localhost/');
    let reads=0;
    await page.exposeFunction('loadView',()=>{reads++;return read(event);});
    await page.exposeFunction('openViewChat',id=>{opened=id;});
    async function init(){
      await page.addScriptTag({path:path.join(__dirname,'..','sidebar-views.js')});
      await page.evaluate(()=>{window.localChatDetails={savedViewData:loadView,openChat:openViewChat};installSidebarViews();});
    }
    await init();
    assert.equal(reads,0);
    await page.getByText('Saved views',{exact:true}).click();
    await page.getByRole('button',{name:'New view'}).click();
    await page.getByLabel('View name').fill('Work waiting on Home');
    await page.getByLabel('Project',{exact:true}).selectOption(project);
    await page.getByLabel('Started on').selectOption(work);
    await page.getByLabel('Last updated').selectOption('7');
    await page.getByLabel('Awaiting receipt from').selectOption(home);
    await page.getByRole('button',{name:'Save',exact:true}).click();
    await page.getByRole('button',{name:'Pending',exact:true}).click();
    assert.equal(opened,expected);
    assert.equal(await page.locator('#csc-saved-views li').count(),1);
    await page.reload();await init();
    await page.getByText('Saved views',{exact:true}).click();
    await page.getByLabel('Saved sidebar view').selectOption({label:'Work waiting on Home'});
    assert.equal(await page.locator('#csc-saved-views li').count(),1);
    await write(path.join(dir,'auth.json'),{tokens:{account_id:'other'}});
    await page.getByRole('button',{name:'Refresh'}).click();
    await page.waitForFunction(()=>!document.querySelector('#csc-saved-views li') && document.querySelector('#csc-saved-views select')?.length===1);
    console.log('Passed native catalogue, weekly project/origin/receipt filter, navigation, reload and account isolation.');
  } finally {await browser.close();await fs.rm(dir,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
