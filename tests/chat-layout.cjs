const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('/usr/lib/chatgpt/resources/cua_node/lib/node_modules/playwright-core');

async function run() {
  const browser = await chromium.launch({channel:'chrome', chromiumSandbox:true});
  try {
    const page = await browser.newPage({viewport:{width:1900, height:900}});
    await page.setContent(`
      <style>
        body { margin:0; }
        main { position:relative; margin-left:340px; }
        [data-app-action-timeline-scroll] { height:100vh; overflow-y:scroll; }
        article, footer { max-width:768px; margin-inline:auto !important; }
        article { height:2000px; }
        aside { position:absolute; top:12px; right:12px; width:300px; height:180px; }
      </style>
      <main data-app-shell-focus-area="main">
        <div data-app-action-timeline-scroll>
          <div><article data-thread-user-message-navigation-content></article></div>
          <footer data-pip-obstacle="thread-footer"></footer>
        </div>
        <aside data-summary-panel-variant="summary"></aside>
      </main>`);
    await page.addScriptTag({path:path.join(__dirname, '..', 'chat-layout.js')});
    await page.evaluate(() => {
      window.stopTracking = cscTrackChat(value => window.petPosition = value,
        state => window.petMotion = state);
    });
    const rect = selector => page.locator(selector).boundingBox();
    const pet = () => page.evaluate(() => window.petPosition);
    const chat = await rect('article'), composer = await rect('footer'), card = await rect('aside');
    assert.ok(chat.width > 1000);
    assert.equal(composer.x, chat.x);
    assert.equal(composer.width, chat.width);
    assert.ok(chat.x + chat.width < card.x);
    const initial = await pet();
    assert.ok(initial.left > chat.x + chat.width);
    assert.ok(initial.top > card.y + card.height);
    await page.locator('aside').evaluate(e => e.style.height = '550px');
    await page.waitForFunction(() => window.petPosition.top > 562);
    const fixed = await pet();
    for (const [top, state] of [[400, 'running-right'], [200, 'running-left']]) {
      await page.locator('[data-app-action-timeline-scroll]').evaluate((e, y) => e.scrollTop = y, top);
      await page.waitForFunction(s => window.petMotion === s, state);
      assert.deepEqual(await pet(), fixed);
      await page.waitForFunction(() => window.petMotion === 'idle');
    }
    await page.setViewportSize({width:1000, height:900});
    await page.waitForFunction(() => !window.petPosition && !document.querySelector('[data-csc-wide-chat]'));
    await page.setViewportSize({width:1900, height:900});
    await page.waitForFunction(() => window.petPosition);
    await page.locator('main').evaluate(n => n.remove());
    await page.waitForFunction(() => !window.petPosition);
    await page.evaluate(() => window.stopTracking());
    assert.equal(await page.locator('style').count(), 1);
    console.log('Chat width, card clearance, scroll direction, idle, resize and navigation passed.');

    await page.setContent(`<div data-turn-key="test">
      <div data-local-chat-detail="user"><time></time></div>
      <span class="group-hover/user-message:opacity-100"><span class="text-xs text-tertiary">Native user time</span></span>
      <span class="group-hover/user-message:opacity-100"><button>Edit</button></span>
      <span data-assistant-message-sent-time>Native assistant time</span>
      <div data-local-chat-detail="assistant"><time></time></div>
    </div>`);
    await page.addScriptTag({path:path.join(__dirname, '..', 'chat-details.js')});
    const visibility = await page.evaluate(() => {
      const user = markTimestampRow('user'), assistant = markTimestampRow('assistant');
      const visible = () => ['.text-xs','[data-assistant-message-sent-time]','button']
        .map(selector => document.querySelector(selector).checkVisibility());
      const initial = visible();
      user(document.querySelector('[data-local-chat-detail="user"] time'));
      assistant(document.querySelector('[data-local-chat-detail="assistant"] time'));
      const mounted = visible();
      user(null);
      const userUnmounted = visible();
      assistant(null);
      return [initial,mounted,userUnmounted,visible()];
    });
    assert.deepEqual(visibility, [[true,true,true],[false,false,true],[true,false,true],[true,true,true]]);
    console.log('Native timestamps restored when replacement times unmount, with message actions still visible.');
    await page.setContent(`<div data-turn-key="turn"><span hidden data-csc-origin-thread="thread" data-csc-reply-turn="turn" data-csc-reply-role="turn"></span>
      <div data-response-annotation-target="reply"><span role="button" data-file-reference data-markdown-copy-text="C:/Projects/Sample/src/file.txt:9:2-12:4">file.txt</span></div></div>`);
    await page.addScriptTag({path:path.join(__dirname,'..','sidebar-colours.js')});
    await page.evaluate(()=>{
      window.localChatDetails={listenActions(){},openModControls:ctx=>{window.testAction=ctx;}};
      window.electronBridge={showContextMenu:()=>({id:'file'})};
      installChatActions();
    });
    await page.getByRole('button',{name:'file.txt'}).click({button:'right'});
    const action=await page.evaluate(()=>window.testAction);
    assert.deepEqual(action,{thread:'thread',turn:'turn',item:'reply',action:'file',reference:'C:/Projects/Sample/src/file.txt:9:2-12:4'});
    console.log('Native file-reference menu preserves the original path and exact reply.');
  } finally {
    await browser.close();
  }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
