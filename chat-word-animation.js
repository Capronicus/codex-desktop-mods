// CSS highlights paint the original text nodes, including selections across inline Markdown.
function cscWordAnimations() {
  if(window.__cscWordEffects)return window.__cscWordEffects;
  if (!window.CSS?.highlights || typeof Highlight === 'undefined') return null;
  const prefix = 'codex.mods.animated-words.v1/';
  const annotation = '[data-response-annotation-target]';
  const bubble = '[data-user-message-bubble]';
  const markdown = '[data-markdown-text-style="assistant-message"]';
  const cloud = '[data-chatgpt-selection-message-id],[data-message-author-role][data-message-id]';
  const bodies = [annotation, bubble, markdown, cloud].join(',');
  const ignored = 'button,input,textarea,select,svg,[hidden],[aria-hidden="true"],[contenteditable="true"],[data-local-chat-detail]';
  const saved = new Map(), mounted = new Map(), dirty = new Set();
  const palettes = {gold:['#b87e23','#edbd55','#fff1a6','#e9ad32','#c18a26'],
    rainbow:['#f36a83','#f0a24c','#dbc948','#50bc91','#5f9ce8','#b480df']};
  const effects = new Map([['csc-words', new Highlight()]]);
  for(const [name,stops] of Object.entries(palettes))
    stops.forEach((_,i)=>effects.set('csc-'+name+i,new Highlight()));
  const rulesKey='codex.mods.phrase-rules.v1/'+(window.localChatDetails?.effectsAccount||'local'), validEffects=['pulse',...Object.keys(palettes)];
  let rules=[];
  function readRules(){
    try{return JSON.parse(localStorage.getItem(rulesKey)||'[]').filter(r=>typeof r?.phrase==='string' &&
      r.phrase.trim() && r.phrase.length<=160 && validEffects.includes(r.effect)).slice(0,100);}catch{return [];}
  }
  let observer, style, frame;

  function message(element) {
    if (!element) return null;
    const row = element.closest('[data-turn-key]');
    const marker = row?.querySelector('[data-csc-origin-thread][data-csc-reply-turn]');
    let root, id;
    if (marker) {
      root = element.closest(bubble) || element.closest(annotation) || element.closest(markdown);
      if (!root || !row.contains(root)) return null;
      const role = root.matches(bubble) ? 'user' : 'assistant';
      const item = root.getAttribute('data-response-annotation-target') || marker.dataset.cscReplyItem || row.dataset.turnKey;
      id = ['local', marker.dataset.cscOriginThread, marker.dataset.cscReplyTurn, role, item];
    } else {
      root = element.closest('[data-chatgpt-selection-message-id]') || element.closest('[data-message-author-role][data-message-id]');
      if (!root) return null;
      id = ['cloud', root.dataset.chatgptSelectionMessageId || root.dataset.messageId];
    }
    return {root, key:JSON.stringify(id)};
  }
  function textNodes(root) {
    const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: node => node.parentElement.closest(ignored) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT
    });
    const nodes = [];
    while (walk.nextNode()) nodes.push(walk.currentNode);
    return nodes;
  }
  function signature(text) {
    let hash = 2166136261;
    for (let i = 0; i < text.length; i++) hash = Math.imul(hash ^ text.charCodeAt(i), 16777619);
    return (hash >>> 0).toString(36);
  }
  function capture(target) {
    const selection = window.getSelection();
    if (!selection?.rangeCount || selection.isCollapsed || target.closest(ignored)) return;
    const range = selection.getRangeAt(0), msg = message(target);
    if (!msg || !msg.root.contains(range.commonAncestorContainer)) return;
    const nodes = textNodes(msg.root);
    let offset = 0, start, end, text = '';
    for (const node of nodes) {
      if (range.intersectsNode(node)) {
        const a = range.startContainer === node ? range.startOffset : 0;
        const b = range.endContainer === node ? range.endOffset : node.length;
        if (a < b) {
          start ??= offset + a;
          end = offset + b;
          text += node.data.slice(a,b);
        }
      }
      offset += node.length;
    }
    if (!text.trim() || text !== range.toString()) return;
    const tag = JSON.stringify([msg.key, start, end, signature(text)]);
    const overlaps = [...(saved.get(msg.key)?.entries() || [])].filter(([, m]) => m.start < end && m.end > start);
    return {tag, excerpt:text, label:overlaps.length ? 'Remove text animation' : 'Animate selected text', apply(effect) {
      if(effect && !validEffects.includes(effect))return;
      for (const [id] of overlaps) {localStorage.removeItem(prefix + id); forget(id);}
      if (effect || !overlaps.length) {localStorage.setItem(prefix + tag, effect==='pulse'||!effect?'1':effect); remember(tag);}
      watch();
      {
        collect(document.body);
        for (const root of mounted.keys()) if (message(root)?.key === msg.key) dirty.add(root);
        repaint();
      }
    }};
  }
  function remember(tag) {
    let fields;
    try { fields = JSON.parse(tag); } catch { return; }
    if (!Array.isArray(fields)) return;
    const [key, start, end, hash] = fields;
    if (typeof key !== 'string' || !Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || end <= start || typeof hash !== 'string') return;
    if (!saved.has(key)) saved.set(key, new Map());
    const value=localStorage.getItem(prefix+tag);
    saved.get(key).set(tag, {start,end,hash,effect:validEffects.includes(value)?value:'pulse'});
  }
  function forget(tag) {
    let key;
    try { [key] = JSON.parse(tag); } catch { return; }
    const marks = saved.get(key);
    marks?.delete(tag);
    if (!marks?.size) saved.delete(key);
  }
  function clear(root) {
    for (const [ink,range] of mounted.get(root) || []) ink.delete(range);
    mounted.delete(root);
    root.removeAttribute('data-csc-word-motion');
  }
  function paint(root, marks) {
    const links=root.querySelectorAll('a[href$="#fx-gold"],a[href$="#fx-rainbow"],a[href$="#fx-pulse"]');
    if(!marks.size && !rules.length && !links.length && !root.textContent.includes('[[') && !root.textContent.includes('](#fx-'))return;
    const nodes = textNodes(root), ranges = [];
    const full=nodes.map(n=>n.data).join('');
    const entries=[], explicit=[...marks.values()].filter(m=>m.end<=full.length && signature(full.slice(m.start,m.end))===m.hash);
    function addAutomatic(entry){
      if (entries.length>=1500 || explicit.some(m=>m.start<entry.end && m.end>entry.start) ||
          entries.some(m=>m.start<entry.end && m.end>entry.start))return;
      entries.push(entry);
    }
    for(const link of links){
      const href=link.getAttribute('href');
      if(!/^#fx-(gold|rainbow|pulse)$/.test(href) || link.closest('pre,code'))continue;
      let offset=0,start=null,end=0;
      for(const node of nodes){if(link.contains(node)){start??=offset;end=offset+node.length;}offset+=node.length;}
      if(start!==null)addAutomatic({start,end,effect:href.slice(4),automatic:true});
    }
    for(const match of full.matchAll(/\[([^\]\n]{1,1024})\]\(#fx-(gold|rainbow|pulse)\)/g)){
      const start=match.index+1;
      addAutomatic({start,end:start+match[1].length,effect:match[2],automatic:true});
    }
    const inline=/\[\[(gold|rainbow|pulse)\|([^\]\n]{1,1024})\]\]/g;
    for(const match of full.matchAll(inline)){
      const start=match.index+match[1].length+3;
      addAutomatic({start,end:start+match[2].length,effect:match[1],automatic:true});
    }
    // Explicit selections win over markup, then literal phrase rules.
    for(const {phrase,effect} of rules){
      let at=0;
      while((at=full.indexOf(phrase,at))>=0 && entries.length<1500){
        addAutomatic({start:at,end:at+phrase.length,effect,automatic:true});at+=phrase.length;
      }
    }
    for(const {start,end,hash,effect,automatic} of [...entries,...explicit]) {
      let offset = 0, range;
      for (const node of nodes) {
        const next = offset + node.length;
        if (!range && start < next) {
          range = new Range();
          range.setStart(node,start-offset);
        }
        if (range && end <= next) {
          range.setEnd(node,end-offset);
          if (automatic || signature(range.toString()) === hash) {
            if(automatic && (range.startContainer.parentElement.closest('pre,code') || range.endContainer.parentElement.closest('pre,code')))break;
            const segments=palettes[effect];
            if(!segments){const ink=effects.get('csc-words');ink.add(range);ranges.push([ink,range]);}
            else {
              // Vary the ink along the phrase without wrapping or replacing React-owned text.
              let cursor=0, phase=0;
              for(const part of nodes){
                const a=Math.max(0,start-cursor),b=Math.min(part.length,end-cursor);
                cursor+=part.length;
                if(a>=b)continue;
                let index=a;
                for(const char of part.data.slice(a,b)){
                  const piece=new Range();piece.setStart(part,index);index+=char.length;piece.setEnd(part,index);
                  const ink=effects.get('csc-'+effect+(phase++ % segments.length));
                  ink.add(piece);ranges.push([ink,piece]);
                }
              }
            }
          }
          break;
        }
        offset = next;
      }
    }
    if (ranges.length) {mounted.set(root,ranges); root.setAttribute('data-csc-word-motion','');}
  }
  function repaint() {
    cancelAnimationFrame(frame); frame = 0;
    for (const root of mounted.keys()) if (!root.isConnected || dirty.has(root)) clear(root);
    for (const candidate of dirty) {
      if (!candidate.isConnected) continue;
      const msg = message(candidate);
      if (msg && !mounted.has(msg.root)) paint(msg.root,saved.get(msg.key)||new Map());
    }
    dirty.clear();
  }
  function collect(node) {
    if (node.nodeType !== 1) return;
    if (node.matches(bodies)) dirty.add(node);
    for (const root of node.querySelectorAll(bodies)) dirty.add(root);
  }
  function watch() {
    if (observer) return;
    if (!style) {
      style = document.createElement('style');
      style.textContent = `
        @property --csc-word-tint {syntax:"<color>";inherits:true;initial-value:transparent}
        @keyframes csc-word-pulse {from {--csc-word-tint:color-mix(in srgb, Highlight 16%, transparent)}
          to {--csc-word-tint:color-mix(in srgb, Highlight 56%, transparent)}}
        [data-csc-word-motion] {animation:csc-word-pulse 1.6s ease-in-out infinite alternate}
        ::highlight(csc-words) {background-color:var(--csc-word-tint);text-decoration:underline}
        @media (prefers-reduced-motion:reduce) {[data-csc-word-motion] {animation:none;--csc-word-tint:color-mix(in srgb, Highlight 32%, transparent)}}
      `;
      for (const [name,stops] of Object.entries(palettes)) {
        for(let i=0;i<stops.length;i++){
          const variable='--csc-'+name+i;
          style.textContent+='@property '+variable+' {syntax:"<color>";inherits:true;initial-value:'+stops[i]+'}'+
            '::highlight(csc-'+name+i+'){color:var('+variable+');text-shadow:0 0 1px currentColor}';
        }
      }
      const frames=Array.from({length:31},(_,step)=>{
        let values='';
        for(const [name,stops] of Object.entries(palettes))
          stops.forEach((_,i)=>{values+='--csc-'+name+i+':'+stops[(i+step)%stops.length]+';';});
        return (step/30*100)+'%{'+values+'}';
      }).join('');
      style.textContent+='@keyframes csc-phrase-inks{'+frames+'}'+
        '@media (prefers-reduced-motion:no-preference){[data-csc-word-motion]{animation:csc-word-pulse 1.6s ease-in-out infinite alternate,csc-phrase-inks 18s linear infinite}}';
      document.head.append(style);
    }
    for(const [key,ink] of effects)CSS.highlights.set(key,ink);
    observer = new MutationObserver(records => {
      for (const record of records) {
        const el = record.target.nodeType === 1 ? record.target : record.target.parentElement;
        const root = el?.closest(bodies);
        if (root) dirty.add(message(root)?.root || root);
        if (record.type === 'attributes' && el?.matches('[data-turn-key],[data-csc-reply-turn]')) {
          const row = el.closest('[data-turn-key]');
          if (row) collect(row);
        }
        for (const node of record.addedNodes) collect(node);
      }
      if (!frame && (dirty.size || mounted.size)) frame = requestAnimationFrame(repaint);
    });
    observer.observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,
      attributeFilter:['data-message-id','data-chatgpt-selection-message-id','data-response-annotation-target','data-csc-reply-turn','data-turn-key']});
    collect(document.body); repaint();
  }
  function load() {
    observer?.disconnect(); observer = null;
    saved.clear();rules=readRules();
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key?.startsWith(prefix)) remember(key.slice(prefix.length));
    }
    for (const root of mounted.keys()) clear(root);
    watch();
  }
  function manageRules(phrase=''){
    if(document.getElementById('csc-phrase-rules'))return;
    const dialog=document.createElement('dialog');
    dialog.id='csc-phrase-rules';dialog.setAttribute('aria-label','Text effects');
    dialog.style.cssText='padding:18px;border-radius:12px;border:1px solid GrayText;background:Canvas;color:CanvasText;max-width:min(30rem,92vw);max-height:75vh;overflow:auto';
    const title=document.createElement('h3');title.textContent='Text effects';
    const info=document.createElement('p');info.textContent='Style matching phrases in prompts and replies. For one phrase use [your phrase](#fx-rainbow), or [[gold|your phrase]] in plain text. Effects: pulse, gold, rainbow.';
    const input=document.createElement('input');input.placeholder='Exact phrase';input.setAttribute('aria-label','Exact phrase');input.maxLength=160;input.value=phrase.slice(0,160);
    const select=document.createElement('select');select.setAttribute('aria-label','Effect');
    for(const [id,label] of [['gold','Gold shimmer'],['rainbow','Fabulous rainbow'],['pulse','Pulse']])select.add(new Option(label,id));
    const list=document.createElement('ul'),status=document.createElement('p');status.setAttribute('role','status');
    const button=(label,fn)=>{const b=document.createElement('button');b.type='button';b.textContent=label;b.onclick=fn;return b;};
    function persist(items){localStorage.setItem(rulesKey,JSON.stringify(items));load();draw();}
    function draw(){list.replaceChildren();for(const rule of rules){
      const li=document.createElement('li');li.append(rule.phrase+' · '+rule.effect+' ',button('Remove',()=>persist(readRules().filter(r=>r.phrase!==rule.phrase))));list.append(li);
    }}
    dialog.append(title,info,input,select,button('Save rule',()=>{
      const phrase=input.value.trim();if(!phrase){input.focus();return;}
      const items=readRules().filter(r=>r.phrase!==phrase);
      if(items.length>=100){status.textContent='Remove a rule before adding another.';return;}
      persist([...items,{phrase,effect:select.value}]);input.value='';status.textContent='Rule saved.';
    }),list,status,button('Close',()=>dialog.close()));
    dialog.addEventListener('close',()=>dialog.remove(),{once:true});document.body.append(dialog);draw();dialog.showModal();input.focus();
  }
  window.addEventListener('storage', event => {if (!event.key || event.key===rulesKey || event.key.startsWith(prefix)) load();});
  load();
  document.addEventListener('click',event=>{
    const link=event.target.closest?.('a[href^="#fx-"]');
    if(link && /^#fx-(gold|rainbow|pulse)$/.test(link.getAttribute('href')) && message(link)){
      event.preventDefault();event.stopPropagation();
    }
  },true);
  function bookmark(target, selection) {
    const msg=message(target);
    if(!msg)return;
    const address=JSON.parse(msg.key), cloud=address[0]==='cloud';
    const thread=cloud?(target.closest('[data-chatgpt-selection-conversation-id]')?.dataset.chatgptSelectionConversationId ||
      target.closest('[data-response-annotation-conversation]')?.dataset.responseAnnotationConversation):address[1];
    if(!thread)return;
    return {action:'new-bookmark',thread,turn:cloud?'':address[2],item:cloud?address[1]:address[4],
      anchor:selection?.tag||JSON.stringify([msg.key,0,0,'']),label:(selection?.excerpt||msg.root.textContent).trim().slice(0,100)};
  }
  function jump(scope, anchor) {
    const [key,start,end,hash]=JSON.parse(anchor);
    for(const element of scope.querySelectorAll(bodies)){
      const msg=message(element);
      if(msg?.key!==key || !msg.root.checkVisibility())continue;
      if(end===0){msg.root.scrollIntoView({block:'center'});return true;}
      let offset=0, range;
      for(const node of textNodes(msg.root)){
        const next=offset+node.length;
        if(!range && start<next){range=new Range();range.setStart(node,start-offset);}
        if(range && end<=next){
          range.setEnd(node,end-offset);
          if(signature(range.toString())!==hash)break;
          range.startContainer.parentElement.scrollIntoView({block:'center'});
          const sel=getSelection();sel.removeAllRanges();sel.addRange(range);return true;
        }
        offset=next;
      }
      msg.root.scrollIntoView({block:'center'});
      return 'changed';
    }
    return false;
  }
  return window.__cscWordEffects={capture,bookmark,jump,manageRules};
}
