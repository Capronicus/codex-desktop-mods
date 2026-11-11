// The ChatGPT message graph exposes timestamps, but does not promise the model
// usage counters that Codex rollouts record. Message length is a different metric.
let chatDetailsCloudTimeSelector;
const chatDetailsCloudFormat=new Intl.DateTimeFormat(undefined,{dateStyle:'medium',timeStyle:'short'});
function CloudChatTime({conversationId,messageId,role,fallback}) {
  chatDetailsCloudTimeSelector??=g(Y,({conversationId,messageId},{get})=>{
    const m=get(Nr,conversationId)?.[messageId]?.message;
    return m?.create_time??m?.update_time??null;
  });
  const args=EP.useMemo(()=>({conversationId,messageId}),[conversationId,messageId]);
  const value=Z(chatDetailsCloudTimeSelector,args)??fallback;
  if(!Number.isFinite(value) || value<=0)return null;
  const date=new Date(value*1000);
  if(!Number.isFinite(date.getTime()))return null;
  return $.jsx('div',{'data-local-chat-detail':role,style:{textAlign:role==='user'?'end':'start',fontSize:'11px',lineHeight:'16px',color:'var(--color-text-secondary, GrayText)',marginBlock:'3px'},
    children:$.jsx('time',{dateTime:date.toISOString(),title:date.toLocaleString(),children:chatDetailsCloudFormat.format(date)})});
}
function withCloudChatTime(content,item,conversationId) {
  if (window.localChatDetails?.visuals?.timestamps === false) return content;
  const role=item.type==='user-message'?'user':item.type==='assistant-message'?'assistant':null;
  const messageId=item.latestMessageId??item.messageId;
  if(!content || !role || !messageId || !conversationId)return content;
  const fallback=item.message?.create_time??item.message?.update_time;
  return $.jsxs($.Fragment,{children:[content,$.jsx(CloudChatTime,{role,messageId,conversationId,fallback})]});
}
