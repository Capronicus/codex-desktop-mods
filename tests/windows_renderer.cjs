// This endpoint belongs to the disposable Windows CI profile in windows_store_smoke.py.
const assert = require('node:assert/strict');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

async function probe(url) {
  const socket = new WebSocket(url);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, {once:true});
    socket.addEventListener('error', reject, {once:true});
  });
  let id = 0;
  async function evaluate(expression) {
    const seq = ++id;
    const reply = new Promise((resolve, reject) => {
      function listen(message) {
        const response = JSON.parse(message.data);
        if (response.id !== seq) return;
        socket.removeEventListener('message', listen);
        if (response.error || response.result?.exceptionDetails)
          reject(Error(JSON.stringify(response.error || response.result.exceptionDetails)));
        else resolve(response.result.result.value);
      }
      socket.addEventListener('message', listen);
    });
    socket.send(JSON.stringify({id:seq, method:'Runtime.evaluate',
      params:{expression, returnByValue:true, awaitPromise:true}}));
    return reply;
  }
  try {
    const thread = '01a1aa55-1234-7000-8000-000000000011';
    const turn = '01a1aa55-1234-7000-8000-000000000012';
    const parent = '01a1aa55-1234-7000-8000-000000000010';
    const data = await evaluate(`window.localChatDetails.readTurns("${thread}", ["${turn}"])`);
    console.log('Renderer fixture', JSON.stringify(data), 'at', await evaluate('location.pathname'));
    assert.equal(data[turn]?.tokens, 25, 'reply token count');
    assert.equal(data[turn]?.total, 125, 'cumulative token count');
    assert.ok(data[turn]?.end, 'reply timestamp');
    const parents = await evaluate(`window.localChatDetails.forkParents(["${thread}"])`);
    assert.equal(parents[thread], parent);
    console.log('Verified real Windows timestamp, token and fork IPC.');
    const project = 'document.querySelector(\'[data-app-action-sidebar-project-label="Windows QA"]\')';
    for (let i = 0; i < 45 && !await evaluate(`!!${project}`); i++) await wait(700);
    assert.equal(await evaluate(`!!${project}`), true, 'native sidebar project');
    await evaluate(`${project}.dispatchEvent(new MouseEvent('contextmenu',{shiftKey:true,bubbles:true,cancelable:true}))`);
    for (let i = 0; i < 30 && !await evaluate("!!document.querySelector('dialog.csc-picker[open]')"); i++) await wait(100);
    assert.equal(await evaluate("document.querySelector('dialog.csc-picker h2')?.innerText"), 'Project colour');
    await evaluate("document.querySelector('dialog.csc-picker input[type=checkbox]').click()");
    await evaluate("Array.from(document.querySelectorAll('dialog button')).find(b => b.textContent === 'Save swatch').click()");
    await evaluate("document.querySelector('dialog.csc-picker [aria-label=Violet]').click()");
    for (let i = 0; i < 20; i++) {
      if (await evaluate(`getComputedStyle(${project}.querySelector('[data-csc-project-label]') || ${project}).color === 'rgb(154, 119, 211)'`)) {
        const prefs = new Map(await evaluate("(async () => (await window.localChatDetails.colours('read',[],null)).entries)()"));
        assert.ok([...prefs].some(([k,v])=>k.startsWith('override-project/') && v==='on'));
        assert.ok([...prefs.keys()].some(k=>k.startsWith('swatch/')));
        console.log('Verified native Windows picker, group override and saved swatch.');
        return;
      }
      await wait(150);
    }
    throw Error('Project title did not take its chosen colour.');
  } finally {
    socket.close();
  }
}
probe(process.argv[2]).catch(e => {console.error(e); process.exitCode = 1;});
