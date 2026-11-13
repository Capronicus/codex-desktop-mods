const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('/usr/lib/chatgpt/resources/cua_node/lib/node_modules/playwright-core');

async function run() {
  const browser = await chromium.launch({channel:'chrome', chromiumSandbox:true});
  try {
    const page = await browser.newPage();
    const markup = '<div data-turn-key="first"><span hidden data-csc-origin-thread="t" data-csc-reply-turn="r" data-csc-reply-role="turn"></span>' +
      '<div data-user-message-bubble>again <b>again</b> please</div><div data-response-annotation-target="answer">again please</div></div>' +
      '<div data-chatgpt-selection-conversation-id="conversation" data-chatgpt-selection-message-id="cloud">again please</div>';
    await page.route('http://fixture.test/', r=>r.fulfill({contentType:'text/html',body:markup}));
    await page.goto('http://fixture.test/');
    async function init() {
      for (const f of ['chat-word-animation.js','sidebar-colours.js']) await page.addScriptTag({path:path.join(__dirname,'..',f)});
      await page.evaluate(()=>{
        window.localChatDetails={listenActions(){}};
        window.electronBridge={showContextMenu(items){window.lastMenu=items;return {id:'animate'};}};
        installChatActions();
      });
    }
    await init();
    const marked=()=>page.evaluate(()=>[...(CSS.highlights.get('csc-words')||[])].map(r=>[r.toString(),r.startContainer.parentElement.tagName]));
    async function toggle(selector, endSelector, start, end) {
      return page.evaluate(({selector,endSelector,start,end})=>{
        const el=document.querySelector(selector),last=document.querySelector(endSelector);
        const range=new Range();
        range.setStart(el.firstChild,start);range.setEnd(last.lastChild,end);
        const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
        el.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true}));
        return window.lastMenu.find(item=>item.id==='animate')?.label;
      },{selector,endSelector,start,end});
    }
    const user='[data-user-message-bubble]',cloud='[data-chatgpt-selection-message-id]',reply='[data-response-annotation-target]';
    assert.equal(await toggle('b',user,0,7),'Animate selected text');
    assert.deepEqual(await marked(),[['again please','B']]);
    assert.equal(await page.locator(user).textContent(),'again again please');
    assert.equal(await page.evaluate(()=>[...Object.keys(localStorage)].join('').includes('again')),false);
    const tint=()=>page.locator('b').evaluate(el=>getComputedStyle(el,'::highlight(csc-words)').backgroundColor);
    const before=await tint();
    await page.waitForFunction(previous=>getComputedStyle(document.querySelector('b'),'::highlight(csc-words)').backgroundColor!==previous,before,{timeout:2500});
    await page.emulateMedia({reducedMotion:'reduce'});
    assert.equal(await page.locator(user).evaluate(el=>getComputedStyle(el).animationName),'none');
    await page.reload();await init();
    assert.deepEqual(await marked(),[['again please','B']]);
    await page.locator(user).evaluate(el=>el.replaceWith(el.cloneNode(true)));
    await page.waitForFunction(()=>[...CSS.highlights.get('csc-words')].some(r=>r.startContainer.isConnected && r.startContainer.parentElement.tagName==='B'));
    assert.deepEqual(await marked(),[['again please','B']]);
    assert.equal(await toggle('b','b',1,3),'Remove text animation');
    assert.deepEqual(await marked(),[]);
    for(const sel of [reply,cloud]){
      assert.equal(await toggle(sel,sel,0,5),'Animate selected text');
      assert.deepEqual(await marked(),[['again','DIV']]);
      assert.equal(await toggle(sel,sel,0,5),'Remove text animation');
    }
    assert.equal(await page.evaluate(()=>localStorage.length),0);
    await toggle('b',user,0,7);
    const bookmark=await page.evaluate(()=>{
      const engine=cscWordAnimations(), target=document.querySelector('b');
      const mark=engine.bookmark(target,engine.capture(target));
      getSelection().removeAllRanges();
      target.replaceWith(target.cloneNode(true));
      const found=engine.jump(document,mark.anchor);
      return {mark,found,selection:getSelection().toString(),tag:getSelection().anchorNode.parentElement.tagName};
    });
    assert.equal(bookmark.mark.thread,'t');assert.equal(bookmark.found,true);
    assert.equal(bookmark.selection,'again please');assert.equal(bookmark.tag,'B');
    assert.equal(await page.evaluate(anchor=>{
      document.querySelector('b').textContent='other';
      return cscWordAnimations().jump(document,anchor);
    },bookmark.mark.anchor),'changed');
    const cloudMark=await page.evaluate(()=>{
      const engine=cscWordAnimations(), target=document.querySelector('[data-chatgpt-selection-message-id]');
      const mark=engine.bookmark(target);
      return {thread:mark.thread,found:engine.jump(document,mark.anchor)};
    });
    assert.deepEqual(cloudMark,{thread:'conversation',found:true});
    console.log('Selected occurrence pulses across Markdown, survives reload and row replacement, respects reduced motion and can be removed in local and cloud chats.');
  } finally {await browser.close();}
}
run().catch(err=>{console.error(err);process.exitCode=1;});
