// Private to the desktop main process. The renderer can request numeric metadata
// for local turn IDs. It cannot supply paths, read message bodies or write files.
;(() => {
  const {ipcMain} = require('electron');
  const fs = require('node:fs/promises');
  const path = require('node:path');
  const os = require('node:os');
  const idPattern = /^[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}$/i;
  const home = path.resolve(process.env.CODEX_HOME || path.join(os.homedir(), '.codex'));
  const cache = new Map();
  const maxLine = 2 * 1024 * 1024;

  async function findFile(id) {
    // UUIDv7 encodes the start time. Rollouts use the host's date at creation.
    const date = parseInt(id.replaceAll('-', '').slice(0, 12), 16);
    const dirs = [];
    if (id[14] === '7') {
      for (const offset of [0, -1, 1]) {
        const day = new Date(date + offset * 86400000).toISOString().slice(0, 10);
        dirs.push(path.join(home, 'sessions', ...day.split('-')));
      }
    }
    dirs.push(path.join(home, 'archived_sessions'));
    for (const dir of dirs) {
      const names = await fs.readdir(dir, {withFileTypes:true}).catch(() => []);
      const f = names.find(n => n.isFile() && n.name.startsWith('rollout-') && n.name.endsWith('-' + id + '.jsonl'));
      if (f) return path.join(dir, f.name);
    }
    // Older IDs do not encode a date. Use the existing thread catalogue.
    let db;
    try {
      const {DatabaseSync} = require('node:sqlite');
      db = new DatabaseSync(path.join(home,'state_5.sqlite'), {readOnly:true});
      return db.prepare('SELECT rollout_path FROM threads WHERE id = ?').get(id)?.rollout_path;
    } catch { return null; } finally { db?.close(); }
  }

  function newState() {
    return {turns:new Map(), current:null, total:null, pos:0, identity:null, file:null, busy:null};
  }
  function count(n) { return Number.isSafeInteger(n) && n >= 0 ? n : null; }
  function record(s, line) {
    // Rollouts can contain very large tool results. Most records are unrelated
    // to timings or usage and need no JSON parsing on the desktop main thread.
    if (!/"type"\s*:\s*"(?:token_usage_record|turn_context|task_started|task_complete|token_count|item_completed)"/.test(line.slice(0,512))) return;
    let row;
    try { row = JSON.parse(line); } catch { return; }
    const p = row?.payload;
    if (!p) return;
    const event = row.type === 'event_msg';
    const at = Date.parse(row.timestamp);
    if (row.type === 'token_usage_record' && idPattern.test(p.turn_id)) {
      const tokens=count(p.turn_token_usage?.total_tokens), total=count(p.thread_token_usage?.total_tokens);
      if (tokens!=null && total!=null) {
        const turn=s.turns.get(p.turn_id)??{};
        Object.assign(turn,{tokens,total,exact:true,unknownDelta:false});
        s.turns.set(p.turn_id,turn);
        s.total=total;
      }
      return;
    }
    if ((event && p.type === 'task_started') || row.type === 'turn_context') {
      if (!idPattern.test(p.turn_id)) return;
      s.current = p.turn_id;
      if (!s.turns.has(s.current)) s.turns.set(s.current, {start:Number.isFinite(at)?at:null});
      return;
    }
    const turn = s.turns.get(p.turn_id??s.current);
    if (event && p.type === 'token_count') {
      const total = count(p.info?.total_token_usage?.total_tokens);
      if (total == null || total === s.total) return;
      const last = count(p.info?.last_token_usage?.total_tokens);
      // 'last' describes one model call. A turn can make many. Differences of
      // successive cumulative snapshots also avoid counting repeated events.
      const delta = s.total != null && s.total <= total ? total - s.total : last;
      s.total = total;
      if (turn && !turn.exact) {
        if (delta == null) turn.unknownDelta = true;
        if (delta != null && !turn.unknownDelta) turn.tokens = (turn.tokens ?? 0) + delta;
        turn.total = total;
      }
    } else if (turn && Number.isFinite(at)) {
      if (event && p.type === 'item_completed' && p.item?.type === 'AgentMessage' && p.item.phase === 'final_answer')
        turn.reply = count(p.completed_at_ms) ?? at;
      if (event && p.type === 'task_complete' && (!p.turn_id || p.turn_id === s.current)) turn.end = at;
    }
  }

  async function update(s, id) {
    const candidate = s.file || await findFile(id);
    if (!candidate) return;
    const file = await fs.realpath(candidate);
    const root = await fs.realpath(home);
    const rel = path.relative(root,file).split(path.sep);
    if (!['sessions','archived_sessions'].includes(rel[0]) || !path.basename(file).endsWith('-'+id+'.jsonl')) return;
    const handle = await fs.open(file, 'r');
    try {
      const stat = await handle.stat();
      if (!stat.isFile()) return;
      const identity = stat.dev + ':' + stat.ino;
      if (s.identity !== identity || stat.size < s.pos) {
        s.turns.clear(); s.current=null; s.total=null; s.pos=0; s.partial=Buffer.alloc(0); s.skipping=false;
      }
      s.identity=identity; s.file=file;
      if (stat.size===s.pos) return;
      const stream = handle.createReadStream({start:s.pos,end:stat.size-1,autoClose:false});
      for await (const chunk of stream) {
        let start=0;
        while (start<chunk.length) {
          const nl=chunk.indexOf(10,start), end=nl<0?chunk.length:nl;
          if (!s.skipping) {
            const part=chunk.subarray(start,end);
            if (s.partial.length+part.length>maxLine) { s.skipping=true; s.partial=Buffer.alloc(0); }
            else if (nl<0) s.partial=Buffer.concat([s.partial,part]);
            else record(s, s.partial.length?Buffer.concat([s.partial,part]).toString():part.toString());
          }
          if (nl<0) break;
          s.partial=Buffer.alloc(0);s.skipping=false;start=nl+1;
        }
        s.pos+=chunk.length;
      }
    } finally { await handle.close(); }
  }

  ipcMain.handle('local-chat-details:read-turns', async (event, id, turns) => {
    const frame=event.senderFrame;
    if (!frame || frame!==event.sender.mainFrame || !/^app:\/\/-\/(?:index|detached-window)\.html(?:[?#]|$)/.test(frame.url)
      || typeof id!=='string' || !idPattern.test(id) || !Array.isArray(turns) || turns.length>128
      || !turns.every(t=>typeof t==='string' && idPattern.test(t))) return {};
    let s=cache.get(id);
    if (!s) {
      s=newState();cache.set(id,s);
      if(cache.size>8) for(const [key,v] of cache) if(key!==id && !v.busy){cache.delete(key);break;}
    } else {cache.delete(id);cache.set(id,s);}
    if (!s.busy) s.busy=update(s,id).catch(()=>{s.file=null;}).finally(()=>{s.busy=null;});
    await s.busy;
    const result={};
    for(const key of turns) {
      const t=s.turns.get(key);
      if(t) result[key]={start:t.start,reply:t.reply??t.end,end:t.end,tokens:t.unknownDelta?null:t.tokens,total:t.total};
    }
    return result;
  });
})();
