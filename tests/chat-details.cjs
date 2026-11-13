const assert=require('node:assert/strict');
const fs=require('node:fs/promises'),os=require('node:os'),path=require('node:path');
(async()=>{
 const codeDir=path.resolve(__dirname, '..');
 const dir=await fs.mkdtemp(path.join(os.tmpdir(),'chat-details-reader-'));
 const id='01a1cccc-1111-7000-9000-000000000001', t1='01a1cccc-1111-7000-9000-000000000011',t2='01a1cccc-1111-7000-9000-000000000012',t3='01a1cccc-1111-7000-9000-000000000013';
 try {
  const archive=path.join(dir,'archived_sessions');await fs.mkdir(archive);
  const file=path.join(archive,'rollout-legacy-'+id+'.jsonl');
  const line=(type,payload)=>JSON.stringify({timestamp:new Date().toISOString(),type,payload})+'\n';
  const start=id=>line('event_msg',{type:'task_started',turn_id:id});
  const done=id=>line('event_msg',{type:'task_complete',turn_id:id});
  const count=(last,total)=>line('event_msg',{type:'token_count',info:{last_token_usage:{total_tokens:last},total_token_usage:{total_tokens:total}}});
  const exact=(id,turn,chat)=>line('token_usage_record',{turn_id:id,turn_token_usage:{total_tokens:turn},thread_token_usage:{total_tokens:chat}});
  await fs.writeFile(file,start(t1)+count(30,30)+count(30,30)+count(70,100)+done(t1)+start(t2)+count(10,110)+exact(t2,60,160)+count(50,160)+done(t2)+start(t3));
  let handler, decodes=0;
  const code=await fs.readFile(path.join(codeDir,'chat-details-main.cjs'),'utf8');
  const zlib=require('node:zlib');
  function load(key) {
    if (key==='electron') return {ipcMain:{handle:(key,fn)=>{if(key==='local-chat-details:read-turns') handler=fn;}}};
    if (key==='node:zlib') return {createZstdDecompress(){decodes++; return zlib.createZstdDecompress();}};
    return require(key);
  }
  new Function('require','process',code)(load,{env:{CODEX_HOME:dir}});
  const frame={url:'app://-/index.html'},event={senderFrame:frame,sender:{mainFrame:frame}};
  assert.deepEqual(await handler({...event,senderFrame:{url:'https://example.org'}},id,[t1]),{});
  assert.deepEqual(await handler(event,'../../whatever',[t1]),{});
  let data=await handler(event,id,[t1,t2,t3]);
  assert.equal(data[t1].tokens,100);assert.equal(data[t1].total,100);
  assert.equal(data[t2].tokens,60);assert.equal(data[t2].total,160);
  assert.equal(data[t3].tokens,undefined);
  const record=exact(t3,30,190);await fs.appendFile(file,record.slice(0,-7));
  data=await handler(event,id,[t3]);assert.equal(data[t3].total,undefined);
  await fs.appendFile(file,record.slice(-7)+done(t3));data=await handler(event,id,[t3]);assert.equal(data[t3].tokens,30);assert.equal(data[t3].total,190);
  // Optional counters give a per-turn breakdown. Records without them keep only the totals.
  assert.equal(data[t3].parts,undefined);
  const id2=id.slice(0,-1)+'2', usage=(input,cached,output,reasoning)=>({input_tokens:input,cached_input_tokens:cached,output_tokens:output,reasoning_output_tokens:reasoning,total_tokens:input+output});
  const split=(last,total)=>line('event_msg',{type:'token_count',info:{last_token_usage:last,total_token_usage:total}});
  await fs.writeFile(path.join(archive,'rollout-legacy-'+id2+'.jsonl'),
    start(t1)+split(usage(20,5,10,2),usage(20,5,10,2))+split(usage(30,10,10,4),usage(50,15,20,6))+done(t1)+
    start(t2)+line('token_usage_record',{turn_id:t2,turn_token_usage:usage(40,10,20,0),thread_token_usage:usage(90,25,40,6)})+done(t2)+
    start(t3)+split(usage(7,0,3,0),usage(97,25,43,6))+line('event_msg',{type:'token_count',info:{total_token_usage:{total_tokens:150}}})+done(t3));
  data=await handler(event,id2,[t1,t2,t3]);
  assert.deepEqual(data[t1].parts,{input:50,cached:15,output:20,reasoning:6});assert.equal(data[t1].tokens,70);
  assert.deepEqual(data[t2].parts,{input:40,cached:10,output:20,reasoning:0});assert.equal(data[t2].total,130);
  assert.equal(data[t3].tokens,20);assert.equal(data[t3].parts,undefined);
  assert.ok(data[t1].end>=data[t1].start);
  const jsxStub={jsx:(name,props)=>({name,props}),jsxs:(name,props)=>({name,props}),Fragment:'fragment'};
  const view=new Function('document','window','Intl',await fs.readFile(path.join(codeDir,'chat-details.js'),'utf8')+'\nreturn {ChatDetailLine,chatDetailDuration};')(
    {createElement:()=>({}),head:{append(){}}},{localChatDetails:{visuals:{}}},Intl);
  assert.deepEqual([4200,65000,3725000].map(view.chatDetailDuration),['4 s','1 min 05 s','1 h 02 min']);
  const react={useMemo:f=>f(),useSyncExternalStore:()=>({start:1000,reply:30000,end:43000,tokens:70,total:70,parts:data[t1].parts})};
  const shown=view.ChatDetailLine({entry:{conversationId:id2,turnId:t1,turnStatus:'completed'},role:'assistant',React:react,jsx:jsxStub}).props.children;
  assert.deepEqual(shown.filter(node=>node.props).map(node=>node.props.children).slice(1),['took 42 s','70 tokens this turn · 70 chat total']);
  assert.match(shown.at(-1).props.title,/^This turn: 50 input \(15 cached\), 20 output \(6 reasoning\)\. /);
  console.log('PASS exact per-turn and chat snapshots, multi-call legacy deltas, duplicate events, missing usage, incomplete append, only selected turns, unsafe path and frame denial.');
  console.log('PASS token breakdown, turns without one, and the turn duration line.');
  const before=await handler(event,id,[t1,t2,t3]);
  const compressed=file+'.zst';
  await fs.writeFile(compressed,zlib.zstdCompressSync(await fs.readFile(file)));
  await fs.unlink(file);
  const polls=await Promise.all([handler(event,id,[t1,t2,t3]),handler(event,id,[t1,t2,t3])]);
  for(const poll of polls) assert.deepEqual(poll,before);
  assert.deepEqual(await handler(event,id,[t1,t2,t3]),before);
  assert.equal(decodes,1,'concurrent and repeated polls reuse one decode');
  const replacement=zlib.zstdCompressSync(Buffer.from(start(t3)+exact(t3,45,205)+done(t3)));
  await fs.writeFile(compressed,replacement);
  data=await handler(event,id,[t1,t3]);assert.equal(data[t1],undefined);assert.equal(data[t3].total,205);
  await fs.writeFile(compressed,replacement.subarray(0,-3));
  assert.deepEqual(await handler(event,id,[t3]),{});
  // An older ID needs the catalogue when its session is outside the dated search.
  const legacy=id.replace('-7000-','-4000-'), dated=path.join(dir,'sessions','2026','11','08');
  await fs.mkdir(dated,{recursive:true});
  const legacyPath=path.join(dated,'rollout-legacy-'+legacy+'.jsonl');
  await fs.writeFile(legacyPath+'.zst',replacement);
  const db=new (require('node:sqlite').DatabaseSync)(path.join(dir,'state_5.sqlite'));
  try {
    db.exec('CREATE TABLE threads (id TEXT, rollout_path TEXT)');
    db.prepare('INSERT INTO threads VALUES (?,?)').run(legacy,legacyPath);
    assert.equal((await handler(event,legacy,[t3]))[t3].tokens,45);
    new Function('require','process',code)(load,{env:{CODEX_HOME:dir}});
    db.prepare('UPDATE threads SET rollout_path=?').run(legacyPath+'.zst');
    assert.equal((await handler(event,legacy,[t3]))[t3].total,205);
  } finally {db.close();}
  console.log('PASS compressed timestamps and usage, archive cache, replacement and incomplete files, and legacy catalogue lookup.');
  const cloud=await fs.readFile(path.join(codeDir,'chat-details-cloud.js'),'utf8');
  let msg={create_time:1700000000,metadata:{message_content_token_count:82}};
  const jsx={jsx:(name,props)=>({name,props}),jsxs:(name,props)=>({name,props}),Fragment:'fragment'};
  const stamp=new Function('g','Y','Nr','EP','Z','$',cloud+'\nreturn CloudChatTime;')((_,fn)=>fn,{}, {}, {useMemo:f=>f()},(fn,args)=>fn(args,{get:()=>({m:{message:msg}})}),jsx);
  assert.equal(stamp({conversationId:'chat',messageId:'m',role:'user'}).props.children.props.dateTime,'2023-11-14T22:13:20.000Z');
  msg={};assert.equal(stamp({conversationId:'chat',messageId:'m',role:'assistant'}),null);
  console.log('PASS cloud timestamp selector and missing metadata.');
 }finally{await fs.rm(dir,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1});
