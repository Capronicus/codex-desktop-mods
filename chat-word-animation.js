// CSS highlights paint the original text nodes, including selections across inline Markdown.
function cscWordAnimations() {
  if (!window.CSS?.highlights || typeof Highlight === 'undefined') return null;
  const prefix = 'codex.mods.animated-words.v1/';
  const annotation = '[data-response-annotation-target]';
  const bubble = '[data-user-message-bubble]';
  const markdown = '[data-markdown-text-style="assistant-message"]';
  const cloud = '[data-chatgpt-selection-message-id],[data-message-author-role][data-message-id]';
  const bodies = [annotation, bubble, markdown, cloud].join(',');
  const ignored = 'button,input,textarea,select,svg,[hidden],[aria-hidden="true"],[contenteditable="true"],[data-local-chat-detail]';
  const saved = new Map(), mounted = new Map(), dirty = new Set();
  const ink = new Highlight();
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
    return {tag, excerpt:text, label:overlaps.length ? 'Remove text animation' : 'Animate selected text', apply() {
      for (const [id] of overlaps) {localStorage.removeItem(prefix + id); forget(id);}
      if (!overlaps.length) {localStorage.setItem(prefix + tag, '1'); remember(tag);}
      watch();
      if (saved.size) {
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
    saved.get(key).set(tag, {start,end,hash});
  }
  function forget(tag) {
    let key;
    try { [key] = JSON.parse(tag); } catch { return; }
    const marks = saved.get(key);
    marks?.delete(tag);
    if (!marks?.size) saved.delete(key);
  }
  function clear(root) {
    for (const range of mounted.get(root) || []) ink.delete(range);
    mounted.delete(root);
    root.removeAttribute('data-csc-word-motion');
  }
  function paint(root, marks) {
    const nodes = textNodes(root), ranges = [];
    for (const {start,end,hash} of marks.values()) {
      let offset = 0, range;
      for (const node of nodes) {
        const next = offset + node.length;
        if (!range && start < next) {
          range = new Range();
          range.setStart(node,start-offset);
        }
        if (range && end <= next) {
          range.setEnd(node,end-offset);
          if (signature(range.toString()) === hash) {ranges.push(range); ink.add(range);}
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
      if (msg && !mounted.has(msg.root) && saved.has(msg.key)) paint(msg.root,saved.get(msg.key));
    }
    dirty.clear();
  }
  function collect(node) {
    if (node.nodeType !== 1) return;
    if (node.matches(bodies)) dirty.add(node);
    for (const root of node.querySelectorAll(bodies)) dirty.add(root);
  }
  function watch() {
    if (!saved.size) {
      observer?.disconnect(); observer = null;
      for (const root of mounted.keys()) clear(root);
      CSS.highlights.delete('csc-words');
      return;
    }
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
      document.head.append(style);
    }
    CSS.highlights.set('csc-words', ink);
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
    saved.clear();
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key?.startsWith(prefix)) remember(key.slice(prefix.length));
    }
    for (const root of mounted.keys()) clear(root);
    watch();
  }
  window.addEventListener('storage', event => {if (!event.key || event.key.startsWith(prefix)) load();});
  load();
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
  return {capture,bookmark,jump};
}
