// The desktop owns pet selection, sprite animation and the floating pet window.
// This component owns only the available space beside an active thread.
const cscTimeline = '[data-app-action-timeline-scroll]';
const cscTranscript = '[data-thread-user-message-navigation-content]';
const cscFooter = '[data-pip-obstacle="thread-footer"]';
const cscCard = '[data-summary-panel-variant]';
const cscLayoutCSS = `
[data-csc-wide-chat] :is([data-thread-user-message-navigation-content],[data-pip-obstacle="thread-footer"]) {
  --thread-content-responsive-max-width: var(--csc-chat-width);
  max-width: var(--csc-chat-width);
  margin-left: max(0px, calc((100% - var(--csc-chat-width) - var(--csc-right-gap)) / 2)) !important;
  margin-right: auto !important;
}
.csc-chat-pet {
  position: fixed; z-index: 3; width: 80px; padding: 0; border: 0;
  background: transparent; border-radius: 12px; cursor: pointer;
  --codex-pet-width: 80px;
}
.csc-chat-pet [data-codex-pet-id] {
  width: 80px; aspect-ratio: 192/208; image-rendering: pixelated; background-repeat: no-repeat;
}
.csc-chat-pet:focus-visible { outline: 2px solid var(--color-text-secondary, currentColor); }
`;

function cscTrackChat(place, animate) {
  const visuals = window.localChatDetails?.visuals || {};
  if (visuals.wideChat === false && visuals.petDock === false) return () => {};
  const style = document.createElement('style');
  style.textContent = cscLayoutCSS;
  document.head.append(style);
  let scroll, content, pane, card, frame, stopTimer, lastTop, previousPosition;
  let motion = 'idle';
  function setMotion(next) {
    if (motion === next) return;
    motion = next;
    animate(next);
  }
  const resize = new ResizeObserver(schedule);
  function position(value) {
    if (previousPosition?.left === value?.left && previousPosition?.top === value?.top) return;
    previousPosition = value;
    place(value);
  }
  function onScroll() {
    const top = scroll.scrollTop;
    if (lastTop === top) return;
    setMotion(top > lastTop ? 'running-right' : 'running-left');
    lastTop = top;
    clearTimeout(stopTimer);
    stopTimer = setTimeout(() => setMotion('idle'), 320);
  }
  function clearWidth() {
    if (!scroll?.hasAttribute('data-csc-wide-chat')) return;
    scroll.removeAttribute('data-csc-wide-chat');
    scroll.style.removeProperty('--csc-chat-width');
    scroll.style.removeProperty('--csc-right-gap');
  }
  function measure() {
    frame = 0;
    const next = Array.from(document.querySelectorAll(cscTimeline)).find(n =>
      n.clientWidth && !n.closest('[data-app-shell-active-page="false"]') && n.querySelector(cscTranscript));
    const nextContent = next?.querySelector(cscTranscript);
    const nextPane = next?.closest('[data-app-shell-focus-area="main"]') || next;
    const nextCard = nextPane?.closest('main')?.querySelector(cscCard);
    if (scroll !== next || content !== nextContent || pane !== nextPane || card !== nextCard) {
      if (scroll !== next) {
        clearWidth();
        scroll?.removeEventListener('scroll', onScroll);
        clearTimeout(stopTimer);
        setMotion('idle');
        lastTop = next?.scrollTop;
        if (visuals.petDock !== false && visuals.petMotion !== false)
          next?.addEventListener('scroll', onScroll, {passive: true});
      }
      scroll = next; content = nextContent; pane = nextPane; card = nextCard;
      resize.disconnect();
      for (const node of [scroll, content?.parentElement, pane, card]) if (node) resize.observe(node);
    }
    if (!scroll || !content?.parentElement) { position(null); return; }
    const bounds = pane.getBoundingClientRect();
    const body = content.parentElement.getBoundingClientRect();
    const panel = card?.getBoundingClientRect();
    const hasPanel = panel?.width > 0 && panel.left > bounds.left + bounds.width / 2;
    if (visuals.wideChat !== false && body.width >= 945) {
      const end = hasPanel ? panel.left - 12 : bounds.right - (visuals.petDock === false ? 0 : 106);
      const reserve = Math.max(0, Math.ceil(body.right - end));
      const width = Math.floor(Math.min(1152, body.width - reserve - 24));
      if (width >= 768) {
        scroll.setAttribute('data-csc-wide-chat', '');
        scroll.style.setProperty('--csc-right-gap', reserve + 'px');
        scroll.style.setProperty('--csc-chat-width', width + 'px');
      } else clearWidth();
    } else clearWidth();
    if (visuals.petDock === false) return;
    const right = Math.max(content.getBoundingClientRect().right,
      scroll.querySelector(cscFooter)?.getBoundingClientRect().right ?? 0);
    const gap = bounds.right - right;
    const minTop = Math.max(bounds.top + 16, hasPanel ? panel.bottom + 16 : 0);
    const maxTop = Math.min(window.innerHeight, bounds.bottom) - 104;
    if (gap < 100 || maxTop < minTop || bounds.width < 945 || document.hidden) {
      position(null);
    } else {
      position({left: Math.round(right + (gap - 80) / 2),
        top: Math.round(Math.min(maxTop, Math.max(minTop, bounds.top + bounds.height * 0.35)))});
    }
  }
  function schedule() {
    if (!frame) frame = requestAnimationFrame(measure);
  }
  // Ignore streaming text, sprite frames and edits inside a turn. Observe only
  // changes that can introduce, move or replace the measured layout elements.
  const relevant = [cscTimeline, cscTranscript, cscCard, cscFooter].join(',');
  const changes = new MutationObserver(records => {
    if (frame) return;
    for (const record of records) {
      if (record.type === 'attributes' || [...record.addedNodes, ...record.removedNodes].some(node =>
        node.nodeType === 1 && (node.matches(relevant) || node.querySelector(relevant)))) {
        schedule(); return;
      }
    }
  });
  changes.observe(document.body, {childList:true, subtree:true, attributes:true, attributeFilter:['data-app-shell-active-page']});
  window.addEventListener('resize', schedule);
  document.addEventListener('visibilitychange', schedule);
  measure();
  return () => {
    changes.disconnect(); resize.disconnect();
    window.removeEventListener('resize', schedule);
    document.removeEventListener('visibilitychange', schedule);
    scroll?.removeEventListener('scroll', onScroll);
    clearTimeout(stopTimer); cancelAnimationFrame(frame);
    clearWidth(); style.remove();
  };
}

function CscChatLayout({react}) {
  const [position, place] = react.useState(null);
  const [state, animate] = react.useState('idle');
  react.useLayoutEffect(() => cscTrackChat(place, animate), []);
  return position && react.createElement(CscChatPet, {react, position, state});
}

async function cscPetContextMenu(event) {
  const bridge = window.electronBridge;
  if (!bridge?.showContextMenu || !window.localChatDetails?.openModControls) return;
  event.preventDefault();
  event.stopPropagation();
  const selection = await bridge.showContextMenu([{id: 'mod-controls', label: 'Mod Controls'}]);
  if (selection?.id === 'mod-controls') await window.localChatDetails.openModControls();
}

function CscChatPet({react, position, state}) {
  const {overlayOpen, avatar, source, sprite, assets, summon} = cscUseNativePet();
  if (overlayOpen || !avatar) return null;
  const label = 'Open ' + (avatar.displayName || 'pet') + ' in the floating window';
  return react.createElement('button', {type:'button', className:'csc-chat-pet',
    style:position, title:label + ' · Right-click for Mod Controls', 'aria-label':label,
    onClick:summon, onContextMenu:cscPetContextMenu},
  react.createElement(sprite, {source, assetMap:assets, state, loop:true}));
}

// A failed pet integration must never take the native conversation down with it.
let cscPetBoundary;
function cscLayoutElement(react) {
  cscPetBoundary ??= class extends react.Component {
    state = {failed:false};
    static getDerivedStateFromError() { return {failed:true}; }
    render() { return this.state.failed ? null : this.props.children; }
  };
  return react.createElement(cscPetBoundary, null, react.createElement(CscChatLayout, {react}));
}
