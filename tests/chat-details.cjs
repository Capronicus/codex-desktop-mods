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
  let handler;
  const code=await fs.readFile(path.join(codeDir,'chat-details-main.cjs'),'utf8');
  new Function('require','process',code)(key=>key==='electron'?{ipcMain:{handle:(key,fn)=>handler=fn}}:require(key),{env:{CODEX_HOME:dir}});
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
  console.log('PASS exact per-turn and chat snapshots, multi-call legacy deltas, duplicate events, missing usage, incomplete append, only selected turns, unsafe path and frame denial.');
  const cloud=await fs.readFile(path.join(codeDir,'chat-details-cloud.js'),'utf8');
  let msg={create_time:1700000000,metadata:{message_content_token_count:82}};
  const jsx={jsx:(name,props)=>({name,props}),jsxs:(name,props)=>({name,props}),Fragment:'fragment'};
  const stamp=new Function('g','Y','Nr','EP','Z','$',cloud+'\nreturn CloudChatTime;')((_,fn)=>fn,{}, {}, {useMemo:f=>f()},(fn,args)=>fn(args,{get:()=>({m:{message:msg}})}),jsx);
  assert.equal(stamp({conversationId:'chat',messageId:'m',role:'user'}).props.children.props.dateTime,'2023-11-14T22:13:20.000Z');
  msg={};assert.equal(stamp({conversationId:'chat',messageId:'m',role:'assistant'}),null);
  console.log('PASS cloud timestamp selector and missing metadata.');
 }finally{await fs.rm(dir,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1});
