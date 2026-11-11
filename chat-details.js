// Appended to the local transcript module. React and the JSX runtime are passed
// by its row renderer so this module does not depend on another copy of React.
const chatDetailStores = new Map();
const chatDetailDate = new Intl.DateTimeFormat(undefined, {dateStyle:'medium', timeStyle:'short'});
const chatDetailNumber = new Intl.NumberFormat();
const chatDetailStyle = {fontSize:'11px', lineHeight:'16px', color:'var(--color-text-secondary, GrayText)', marginBlock:'3px', fontVariantNumeric:'tabular-nums'};
const chatDetailStyles=document.createElement('style');
chatDetailStyles.textContent=`
  [data-csc-assistant-time] [data-assistant-message-sent-time],
  [data-csc-user-time] span[class*="group-hover/user-message:opacity-100"] > span.text-xs.text-tertiary:only-child {
    display:none;
  }
`;
document.head.append(chatDetailStyles);

// Only the row with a mounted replacement time needs to hide its native time.
// The virtual list frequently mounts and recycles rows during scrolling.
function markTimestampRow(role) {
  const attribute = 'data-csc-' + role + '-time';
  let row;
  return time => {
    const parent = time?.parentElement?.parentElement;
    const next = parent?.hasAttribute('data-turn-key') ? parent : null;
    if (row === next) return;
    row?.removeAttribute(attribute);
    next?.setAttribute(attribute, '');
    row = next;
  };
}

function chatDetailStore(threadId) {
  let store=chatDetailStores.get(threadId);
  if (store) return store;
  const listeners=new Map(), values=new Map();
  let timer, busy=false, queued=false;
  function schedule(delay) { clearTimeout(timer); timer=setTimeout(refresh,delay); }
  async function refresh() {
    if (busy || !listeners.size) return;
    if (document.hidden) { schedule(6000); return; }
    busy=true;queued=false;
    try {
      const ids=[...listeners.keys()];
      for (let start=0;start<ids.length;start+=128) {
        const batch=ids.slice(start,start+128);
        const rows=await window.localChatDetails.readTurns(threadId,batch);
        for(const id of batch) {
          const next=rows[id]??null;
          if (!listeners.has(id) || JSON.stringify(next)===JSON.stringify(values.get(id)??null)) continue;
          values.set(id,next);
          for (const notify of listeners.get(id)) notify();
        }
      }
    } catch { /* Native timestamps still render when history is unavailable. */ }
    finally {busy=false;if(listeners.size)schedule(queued?0:4000);}
  }
  store={
    get:id=>values.get(id)??null,
    listen(id,notify) {
      chatDetailStores.set(threadId,store);
      if(!listeners.has(id))listeners.set(id,new Set());
      listeners.get(id).add(notify);queued=true;schedule(0);
      return ()=>{
        const group=listeners.get(id);group.delete(notify);
        if(!group.size){listeners.delete(id);values.delete(id);}
        if(!listeners.size){clearTimeout(timer);if(chatDetailStores.get(threadId)===store)chatDetailStores.delete(threadId);}
      };
    },
  };
  chatDetailStores.set(threadId,store);
  return store;
}

function ChatDetailLine({entry,role,React,jsx}) {
  const thread=entry.conversationId, id=entry.turnId;
  const local=window.localChatDetails && (!entry.hostId || entry.hostId==='local');
  const subscribe=React.useMemo(()=>local&&thread&&id ? (notify=>chatDetailStore(thread).listen(id,notify)) : (()=>()=>{}),[local,thread,id]);
  const snapshot=React.useMemo(()=>()=>chatDetailStores.get(thread)?.get(id)??null,[thread,id]);
  const record=React.useSyncExternalStore(subscribe,snapshot);
  const timeRef=React.useMemo(()=>markTimestampRow(role),[role]);
  const turn=entry.turn, block=entry.transcriptBlock;
  const user=role==='user';
  const finalBlock=!block || (block.type==='assistant-message' && block.item?.phase!=='commentary' &&
    (block.item?.phase==='final_answer' || entry.includeTranscriptTurnExtras));
  const stamp=user ? block?.item?.sentAtMs??turn?.turnStartedAtMs??record?.start
    : block?.item?.sentAtMs??record?.reply??turn?.finalAssistantStartedAtMs;
  const text=[];
  if(window.localChatDetails?.visuals?.timestamps !== false && Number.isFinite(stamp) && stamp>0) {
    const date=new Date(stamp);
    if(Number.isFinite(date.getTime()))text.push(jsx.jsx('time',{ref:timeRef,dateTime:date.toISOString(),title:date.toLocaleString(),children:chatDetailDate.format(date)},'time'));
  }
  const finished=record?.end || ['completed','interrupted','failed'].includes(entry.turnStatus??turn?.status);
  if(window.localChatDetails?.visuals?.tokens !== false && !user && finalBlock && finished && Number.isSafeInteger(record?.total)){
    const total=chatDetailNumber.format(record.total);
    const usage=Number.isSafeInteger(record.tokens)?chatDetailNumber.format(record.tokens)+' tokens this turn · '+total+' chat total':total+' tokens in chat';
    text.push(jsx.jsx('span',{title:'Recorded input and output tokens across all model calls. Chat total through this turn.',children:usage},'tokens'));
  }
  if(!text.length)return null;
  return jsx.jsxs('div',{'data-local-chat-detail':role,style:{...chatDetailStyle,textAlign:user?'end':'start'},children:[text[0],text[1]?' · ':null,text[1]]});
}

const originPanes = new Set();
let originScheduled = false;
function markOriginThread(node) {
  const chat = node?.closest('[data-thread-user-message-navigation-content]');
  if (chat) originPanes.add(chat);
  if (originScheduled) return;
  originScheduled = true;
  queueMicrotask(() => {
    originScheduled = false;
    let changed = false;
    for (const pane of originPanes) {
      const id = pane.isConnected && pane.querySelector('[data-csc-origin-thread]')?.dataset.cscOriginThread;
      if (!id) {
        delete pane.dataset.cscActiveThread;
        originPanes.delete(pane);
        changed = true;
      } else if (pane.dataset.cscActiveThread !== id) {
        pane.dataset.cscActiveThread = id;
        changed = true;
      }
    }
    if (changed) window.dispatchEvent(new Event('csc-chat-origin-changed'));
  });
}

function withChatDetails(entry, row, React, jsx) {
  const visuals = window.localChatDetails?.visuals;
  if (!entry.turnId || !entry.conversationId || entry.liveVoiceSource || entry.type) return row;
  const type=entry.transcriptBlock?.type;
  if(type && type!=='user-message' && type!=='assistant-message')return row;
  const line=role=>role==='user' && visuals?.timestamps===false ? null : jsx.jsx(ChatDetailLine,{entry,role,React,jsx},role);
  const marker=(!entry.hostId || entry.hostId==='local') && jsx.jsx('span',{
    hidden:true,'data-csc-origin-thread':entry.conversationId,'data-csc-reply-turn':entry.turnId,
    'data-csc-reply-item':entry.transcriptBlock?.item?.id,
    'data-csc-reply-role':type==='user-message'?'user':type==='assistant-message'?'assistant':'turn',ref:markOriginThread
  },'origin');
  const details=visuals?.timestamps!==false || visuals?.tokens!==false;
  return jsx.jsxs(jsx.Fragment,{children:[marker,details&&(!type || type==='user-message')&&line('user'),row,details&&(!type || type==='assistant-message')&&line('assistant')]});
}
