// Runs inside the Mod Controls MCP App. All shared strings are rendered as text.
let dashboardState, activeRequest, loadingActions=false;
const actionText = (tag,value) => Object.assign(document.createElement(tag),{textContent:value});
function actionButton(label, run) {
  const button=actionText('button',label);
  button.type='button';
  button.onclick=async()=>{
    button.disabled=true;
    el('action-status').textContent='';
    try{await run();}catch(e){el('action-status').textContent=e.message;}finally{button.disabled=false;}
  };
  return button;
}
function selectValues(select, pairs) {
  select.replaceChildren(...pairs.map(([value,name])=>new Option(name,value)));
}
function reportAction(message) {el('action-status').textContent=message;}
function showReply(text,truncated) {
  const block=el('action-content');
  block.replaceChildren(actionText('pre',text+(truncated?'\n…':'')));
}
async function loadActions() {
  if(loadingActions)return;
  loadingActions=true;
  try{
    const data=await call('chat_action_dashboard');
    if(!data || !data.context)return;
    dashboardState=data;
    const ctx=data.context;
    if(ctx.id && ctx.id!==activeRequest){
      activeRequest=ctx.id;
      if(ctx.action==='organize'){
        el('group-editor').open=true;
        await loadGroups(ctx);
        el('group-editor').scrollIntoView({block:'start'});
      }else el('chat-actions').open=true;
      const section=({handoff:'send-handoff',file:'file-copy',family:'branches',project:'exclude-project',inbox:'handoff-inbox',performance:'performance',conflicts:'conflict-preview'})[ctx.action];
      if(section)el(section).open=true;
      el('file-reference').value=ctx.reference||'';
      el('handoff-note').value='';el('action-content').replaceChildren();reportAction('');
    }
    const devices=el('handoff-device'),prior=devices.value;
    selectValues(devices,Object.entries(data.devices));if(data.devices[prior])devices.value=prior;
    el('handoff-send').disabled=!ctx.turn||!devices.value||state?.paused;
    el('send-hint').textContent=!ctx.turn?'Right-click a local reply and choose Hand off this reply.':!devices.value?'Sync both desktops with the same folder to choose a destination.':'';
    el('file-open').disabled=!ctx.thread;
    el('family-show').disabled=!ctx.thread;
    el('file-hint').textContent=ctx.thread?'':'Right-click a reference in a local reply and choose Open local copy.';
    el('family-hint').textContent=ctx.thread?'':'Right-click a local sidebar chat and choose Chat branches.';
    const list=el('inbox-list');list.replaceChildren();
    if(!data.inbox.length)list.append(actionText('p','No replies waiting on this desktop.'));
    for(const msg of data.inbox){
      const row=document.createElement('li');
      row.append(actionText('strong',msg.name),actionText('p',msg.note||'Reply handoff'),actionButton('Read reply',async()=>{
        const r=await call('read_handoff_reply',{thread:msg.thread,turn:msg.turn,item:msg.item});
        showReply(r.text,r.truncated);
      }),actionButton('Open in chat',()=>command('navigate',msg)),
      actionButton('Done',async()=>{await call('dismiss_reply_handoff',{sender:msg.sender,request:msg.id});await loadActions();}));
      list.append(row);
    }
    const project=el('sharing-project'),previous=project.value;
    selectValues(project,Object.entries(data.projects).map(([id,p])=>[id,p.name]));
    const wanted=ctx.action==='project'&&ctx.project&&ctx.id!==project.dataset.request?ctx.project:previous;
    if(data.projects[wanted])project.value=wanted;
    project.dataset.request=ctx.id||'';projectActionLabel();
    const folders=el('excluded-folders');folders.replaceChildren();
    for(const folder of data.folders){const row=document.createElement('li');row.append(actionText('span',folder+' '),
      actionButton('Resume folder sync',()=>changeRule('folder',folder,false)));folders.append(row);}
    const shared=el('shared-exclusions');shared.replaceChildren();
    for(const [key,name] of Object.entries(data.globalRules||{})){const row=document.createElement('li');
      row.append(actionText('span',name+' · all PCs '),actionButton('Resume on all PCs',()=>changeRule('rule',key,false,true)));shared.append(row);}
    const conflicts=el('conflict-list');conflicts.replaceChildren();
    for(const [thread,ids] of Object.entries(data.conflicts)){
      const row=document.createElement('li'),left=document.createElement('select'),right=document.createElement('select');
      const opts=ids.map(id=>[id,data.labels?.[id]||id]);
      selectValues(left,opts);selectValues(right,opts);if(right.options.length>1)right.selectedIndex=1;
      left.setAttribute('aria-label','First desktop');right.setAttribute('aria-label','Second desktop');
      row.append(actionText('p',data.conflictNames?.[thread]||thread),left,right,actionButton('Compare messages',async()=>{
        const r=await call('compare_chat_histories',{thread,left:left.value,right:right.value});
        const block=el('action-content');block.replaceChildren();
        block.append(actionText('h3','Last shared message'),actionText('pre',r.common?.text||'No shared message found.'));
        if(!r.counts.some(Boolean))block.append(actionText('p','Both snapshots have the same visible messages. Tool activity or metadata may differ.'));
        [r.left,r.right].forEach((messages,i)=>{
          block.append(actionText('h3',(i?right:left).selectedOptions[0].text+' · '+r.counts[i]+' later messages'+(r.counts[i]>12?' · first 12 shown':'')));
          for(const m of messages)block.append(actionText('strong',m.role),actionText('pre',m.text));
        });
      }));conflicts.append(row);
    }
    if(!conflicts.children.length)conflicts.append(actionText('p','No conflicts reported at the last sync.'));
    const p=data.performance;
    el('perf-result').textContent=p.at?new Date(p.at).toLocaleString()+' · '+(p.frames?p.slow+' of '+p.frames+' scroll frames exceeded 30 ms. 95th percentile '+p.p95+' ms.':'No scrolling recorded.')+' '+(p.switchMs!=null?'Chat switch '+p.switchMs+' ms.':'No chat switch recorded.'):'';
  }catch(error){reportAction(error.message);}finally{loadingActions=false;}
}
function projectActionLabel() {
  const id=el('sharing-project').value;
  el('project-rule').disabled=!id;
  const stopped=el('sharing-scope').value==='shared'?Object.hasOwn(dashboardState?.globalRules||{},dashboardState?.projectKeys?.[id]):dashboardState?.privateProjects.includes(id);
  el('project-rule').dataset.stopped=String(!!stopped);
  el('project-rule').textContent=stopped?'Resume project sync':'Stop syncing project';
}
async function changeRule(kind,ident,enabled,shared=false) {
  const result=await call(shared?'set_shared_exclusion':'set_local_only',{kind,ident,enabled});
  if(shared){await loadActions();reportAction(result.queued?'Saved here. This rule will publish when sync resumes.':'Shared rule saved. Other updated PCs apply it after folder delivery.');return;}
  await loadActions();reportAction(enabled?'Excluded on this desktop. Copies already shared are retained.':'Exclusion removed on this desktop.');
}
async function command(action, ctx={}) {
  await call('desktop_chat_command',{action,thread:ctx.thread||'',turn:ctx.turn||'',item:ctx.item||''});
  reportAction(action==='measure'?'Switch to a chat and scroll for 12 seconds. Refresh here for the result.':'Opening the chat in Desktop Mods.');
}
function bindAction(id, run){
  const old=el(id),btn=actionButton(old.textContent,run);btn.id=id;old.replaceWith(btn);
}
function handoffRequestId(){
  const bytes=crypto.getRandomValues(new Uint8Array(16));
  bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
  const hex=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
  return [hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join('-');
}
bindAction('handoff-send',async()=>{
  const ctx=dashboardState.context, device=el('handoff-device').value,note=el('handoff-note').value;
  const signature=JSON.stringify([ctx.id,device,note]);
  if(el('handoff-send').dataset.signature!==signature){el('handoff-send').dataset.signature=signature;el('handoff-send').dataset.request=handoffRequestId();}
  await call('send_reply_handoff',{thread:ctx.thread,turn:ctx.turn,item:ctx.item||'',device,note,request:el('handoff-send').dataset.request});
  reportAction('Handoff saved for '+dashboardState.devices[device]+'.');
});
bindAction('file-open',async()=>{
  const args={thread:dashboardState.context.thread,reference:el('file-reference').value};
  const file=await call('open_linked_file',args),block=el('action-content');
  block.replaceChildren(actionText('h3',file.path));
  if(file.image){const img=document.createElement('img');img.alt='Local file preview';img.src=file.image;block.append(img);}
  else block.append(actionText('pre',file.binary?'Preview unavailable for this file type.':file.text+(file.truncated?'\n…':'')));
  if(typeof file.text==='string')block.append(actionButton('Open in text editor',()=>call('edit_linked_file',args)));
  block.append(actionButton('Show in folder',()=>call('reveal_linked_file',args)));
});
bindAction('family-show',async()=>{
  const rows=await call('list_chat_family',{thread:dashboardState.context.thread}),block=el('action-content');
  block.replaceChildren(actionText('h3','Chat branches'));
  if(!rows.length)block.append(actionText('p','No recorded parent or branches on this desktop.'));
  for(const row of rows){const button=actionButton(row.relation+' · '+row.name+(row.available?'':' · unavailable on this PC'),()=>command('navigate',row));button.disabled=!row.available;block.append(button);}
});
bindAction('project-rule',()=>changeRule('project',el('sharing-project').value,el('project-rule').dataset.stopped!=='true',el('sharing-scope').value==='shared'));
bindAction('folder-rule',()=>changeRule('folder',el('sharing-folder').value.trim(),true,el('sharing-scope').value==='shared'));
bindAction('perf-start',()=>command('measure'));
bindAction('actions-refresh',loadActions);
el('sharing-project').onchange=el('sharing-scope').onchange=projectActionLabel;
el('chat-actions').ontoggle=()=>{if(el('chat-actions').open)loadActions();};
window.addEventListener('focus',()=>{if(state)loadActions();});

document.addEventListener('visibilitychange',()=>{if(!document.hidden && state)loadActions();});
window.addEventListener('message',event=>{
  if(event.source===parent && event.data?.method==='ui/notifications/tool-result' && state)loadActions();
});

let groupData;
function renderGroupAction() {
  if (!groupData) return;
  const mode=el('group-action').value, source=el('group-source'), parent=el('group-parent');
  const lastSource=source.value, lastParent=parent.value;
  const groups=mode==='demote'?groupData.projects:groupData.sections;
  selectValues(source,Object.entries(groups));
  if(Object.hasOwn(groups,lastSource))source.value=lastSource;
  const choices=Object.entries(groupData.projects).filter(([id])=>mode!=='demote'||id!==source.value);
  if(mode!=='create')choices.unshift(['','Top-level sections']);
  selectValues(parent,choices);
  if(choices.some(([id])=>id===lastParent))parent.value=lastParent;
  el('group-source-row').hidden=mode==='create';
  el('group-parent-row').hidden=mode==='promote';
  el('group-name-row').hidden=mode!=='create';
  el('group-folder-row').hidden=mode!=='promote';
  el('group-save').disabled=!groupData.ready||!!groupData.pending||(mode==='create'?!parent.value:!source.value);
  el('group-cancel').hidden=!groupData.pending||groupData.started;
  el('group-retry').hidden=!groupData.error;
}
async function loadGroups(ctx={}) {
  try {
    groupData=await call('sidebar_group_inventory');
    if(ctx.project)el('group-action').value='create';
    if(ctx.section)el('group-action').value='promote';
    renderGroupAction();
    if(ctx.project && Object.hasOwn(groupData.projects,ctx.project))el('group-parent').value=ctx.project;
    if(ctx.section && Object.hasOwn(groupData.sections,ctx.section))el('group-source').value=ctx.section;
    el('group-feedback').textContent=groupData.error || (groupData.pending?'Queued: '+groupData.pending+'. Fully quit and reopen the app.':
      groupData.ready?'':'Sign in to the desktop to organize local projects and custom sections.');
  }catch(error){el('group-feedback').textContent=error.message;}
}
el('group-editor').ontoggle=()=>{if(el('group-editor').open && !groupData)loadGroups();};
el('group-action').onchange=renderGroupAction;
el('group-source').onchange=renderGroupAction;
async function saveGroup(action) {
  const save=el('group-save');save.disabled=true;
  try {
    const mode=el('group-action').value;
    await call('change_sidebar_group', {action:action||mode,source:mode==='create'?'':el('group-source').value,
      parent:mode==='promote'?'':el('group-parent').value,name:mode==='create'?el('group-name').value.trim():'',
      folder:mode==='promote'?el('group-folder').value.trim():''});
    await loadGroups();
  }catch(error){renderGroupAction();el('group-feedback').textContent=error.message;}
}
el('group-save').onclick=()=>saveGroup();
el('group-cancel').onclick=()=>saveGroup('cancel');
el('group-retry').onclick=()=>saveGroup('retry');
