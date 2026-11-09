// Private to the desktop main process. The renderer can request numeric metadata
// for local turn IDs. Colour IPC is restricted to the mod’s preference database.
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

  async function readForkParent(id) {
    const candidate = await findFile(id);
    if (!candidate) return undefined;
    const file = await fs.realpath(candidate).catch(() => fs.realpath(candidate + '.zst'));
    const root = await fs.realpath(home);
    const dir = path.relative(root,file).split(path.sep)[0];
    if (!['sessions','archived_sessions'].includes(dir) || !file.replace(/\.zst$/, '').endsWith('-'+id+'.jsonl')) return undefined;
    const raw = require('node:fs').createReadStream(file);
    const stream = file.endsWith('.zst') ? require('node:zlib').createZstdDecompress() : raw;
    if (stream !== raw) { raw.on('error',error => stream.destroy(error)); raw.pipe(stream); }
    try {
      const pieces = [];
      let size = 0;
      for await (const chunk of stream) {
        const end = chunk.indexOf(10);
        const part = end < 0 ? chunk : chunk.subarray(0,end);
        size += part.length;
        if (size > maxLine) break;
        pieces.push(part);
        if (end >= 0) {
          const header = JSON.parse(Buffer.concat(pieces).toString('utf8'));
          if (header.type !== 'session_meta' || header.payload?.id !== id) break;
          const parent = header.payload.forked_from_id;
          return idPattern.test(parent) ? parent : null;
        }
      }
    } finally { raw.destroy(); stream.destroy(); }
    return undefined;
  }

  let colours;
  function colourDB() {
    if (colours) return colours;
    const {DatabaseSync} = require('node:sqlite');
    const db = new DatabaseSync(path.join(home, 'sidebar-colours.sqlite'));
    try {
      db.exec(`PRAGMA busy_timeout=300; PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS colours (key TEXT PRIMARY KEY, value TEXT, rev INTEGER NOT NULL, author TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS identity (id INTEGER PRIMARY KEY CHECK(id=1), author TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inherited (chat TEXT PRIMARY KEY, project TEXT, section TEXT);`);
      db.prepare('INSERT OR IGNORE INTO identity VALUES (1,?)').run(require('node:crypto').randomUUID());
      colours = db;
      return db;
    } catch (error) { db.close(); throw error; }
  }
  function validEdit(entry) {
    if (!Array.isArray(entry) || entry.length !== 2) return false;
    const [key, value] = entry;
    if (typeof key !== 'string' || !/^(?:project|section|chat|override-project|override-section|swatch)\/[A-Za-z0-9:_@.-]{1,256}$/.test(key)) return false;
    if (key.startsWith('swatch/')) return /^swatch\/[a-f0-9]{6}$/.test(key) && (value === null || value === '#' + key.slice(7));
    return value === null || (key.startsWith('override-') ? value === 'on' :
      typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value));
  }
  // Find members even when a project is collapsed or the chat is outside the visible page.
  ipcMain.handle('local-sidebar-colours:members', async (event,kind,id) => {
    const frame=event.senderFrame;
    if (!frame || frame!==event.sender.mainFrame || !/^app:\/\/-\/(?:index|detached-window)\.html(?:[?#]|$)/.test(frame.url) ||
        !['project','section'].includes(kind) || typeof id!=='string' || !/^[a-z0-9:@._-]{1,256}$/i.test(id))
      throw Error('Invalid colour group');
    let state={};
    try {state=JSON.parse(await fs.readFile(path.join(home,'.codex-global-state.json'),'utf8'));}
    catch(error){if(error.code!=='ENOENT')throw error;}
    const chats=new Set(), cloudProjects=new Set(), projects=new Set(), sections=new Set();
    function addItem(key){
      if (typeof key!=='string') return;
      if (key.startsWith('codex:project:')||key.startsWith('chatgpt:project:')) projects.add(key.slice(key.indexOf(':project:')+9));
      else if (key.startsWith('codex:thread:')) chats.add(key.slice(13));
      else if (key.startsWith('chatgpt:conversation:')) chats.add(key);
    }
    if (kind==='project') projects.add(id);
    else {
      for(const account of Object.values(state['electron-persisted-atom-state']?.['sidebar-custom-sections-v3'] || {}))
        for(const sec of account?.sections || []) if(sec.id===id){
          if(sec.hostSectionIds?.local)sections.add(sec.hostSectionIds.local);
          for(const key of sec.itemKeys || [])addItem(key);
        }
    }
    const aliases=state['app-server-project-id-by-legacy-project-id-by-host']?.['local:'+home] || {};
    for(const [key,record] of Object.entries(state['local-projects'] || {})){
      const cloud=record.chatGptBacking?.projectId;
      if(projects.has(key) || projects.has(aliases[key]) || (cloud && projects.has(cloud))){
        projects.add(key);if(aliases[key])projects.add(aliases[key]);if(cloud)projects.add(cloud);
      }
    }
    for(const project of projects)if(project.startsWith('g-p-'))cloudProjects.add(project);
    for(const [tid,record] of Object.entries(state['thread-project-assignments'] || {}))
      if(idPattern.test(tid) && projects.has(record?.projectId))chats.add('local:'+tid);
    let db;
    try{
      const {DatabaseSync}=require('node:sqlite');
      const catalog=path.join(home,'state_5.sqlite');
      if(await fs.stat(catalog).then(v=>v.isFile(),e=>{if(e.code==='ENOENT')return false;throw e;})){
        db=new DatabaseSync(catalog,{readOnly:true});
        const cols=new Set(db.prepare('PRAGMA table_info(threads)').all().map(r=>r.name));
        const local=state['thread-project-assignments'] || {};
        if (cols.has('project_id')) for(const pid of projects) for(const {id:tid} of db.prepare('SELECT id FROM threads WHERE project_id=?').all(pid))
          if(idPattern.test(tid) && (!local[tid] || projects.has(local[tid].projectId)))chats.add('local:'+tid);
        if (cols.has('thread_section_id')) for(const sid of sections) for(const {id:tid} of db.prepare('SELECT id FROM threads WHERE thread_section_id=?').all(sid))
          if(idPattern.test(tid))chats.add('local:'+tid);
      }
    } finally{db?.close();}
    return {chats:[...chats],cloudProjects:[...cloudProjects]};
  });

  ipcMain.handle('local-sidebar-colours:preferences', (event, mode, entries, previous) => {
    const frame = event.senderFrame;
    if (!frame || frame !== event.sender.mainFrame || !/^app:\/\/-\/(?:index|detached-window)\.html(?:[?#]|$)/.test(frame.url) ||
        !['read','seed','write','finish'].includes(mode) || !Array.isArray(entries) || entries.length > (mode==='finish' ? 250000 : 512) ||
        (mode === 'read' && entries.length) || !entries.every(validEdit)) throw new Error('Invalid colour preference request');
    const db = colourDB();
    if (entries.length) {
      db.exec('BEGIN IMMEDIATE');
      try {
        if(mode==='finish'){
          const force=entries.find(([k,v])=>k.startsWith('override-') && v===null)?.[0];
          const group=force?.slice(9);
          const get=db.prepare('SELECT value FROM colours WHERE key=?');
          if(!group || get.get(force)?.value!=='on')throw Error('Group override changed while saving');
          const colour=get.get(group)?.value;
          if (!entries.every(([k,v])=>(k.startsWith('chat/') && v===colour) || (k===force && v===null) || (k===group && v===null)))
            throw Error('Group colour changed while saving');
        }
        const author = db.prepare('SELECT author FROM identity WHERE id=1').get().author;
        const rev = mode === 'seed' ? 0 : db.prepare('SELECT COALESCE(MAX(rev),0)+1 AS next FROM colours').get().next;
        const upsert = db.prepare(mode === 'seed' ? 'INSERT OR IGNORE INTO colours VALUES (?,?,?,?)' :
          'INSERT OR REPLACE INTO colours VALUES (?,?,?,?)');
        for (const [key,value] of entries) upsert.run(key,value,rev,author);
        db.exec('COMMIT');
      } catch (error) { db.exec('ROLLBACK'); throw error; }
    }
    const version = db.prepare('PRAGMA data_version').get().data_version + ':' +
      db.prepare('SELECT total_changes() AS n').get().n;
    if (previous === version) return null;
    return {version, entries:db.prepare('SELECT key,value FROM colours').all().map(r=>[r.key,r.value]),
      inherited:db.prepare('SELECT chat,project,section FROM inherited').all()};
  });

  let receiptCache;
  ipcMain.handle('local-chat-sync:delivery', async (event,ids) => {
    const frame=event.senderFrame;
    if (!frame || frame!==event.sender.mainFrame || !/^app:\/\/-\/(?:index|detached-window)\.html(?:[?#]|$)/.test(frame.url) ||
        !Array.isArray(ids) || ids.length>128 || !ids.every(id=>typeof id==='string' && idPattern.test(id))) return {};
    try {
      const folder=path.join(home,'chat-sync');
      const config=JSON.parse(await fs.readFile(path.join(folder,'config.json'),'utf8'));
      const file=path.join(folder,'delivery.json'), stamp=await fs.stat(file);
      if (!receiptCache || receiptCache.mtime!==stamp.mtimeMs || receiptCache.ino!==stamp.ino)
        receiptCache={mtime:stamp.mtimeMs,ino:stamp.ino,doc:JSON.parse(await fs.readFile(file,'utf8'))};
      if (receiptCache.doc.device!==config.device) return {};
      return Object.fromEntries(ids.filter(id=>receiptCache.doc.chats[id]).map(id=>
        [id,config.private_chats?.includes(id)?{status:'local_only'}:receiptCache.doc.chats[id]]));
    } catch { return {}; }
  });

  ipcMain.handle('local-chat-details:fork-parents', async (event, ids) => {
    const frame = event.senderFrame;
    if (!frame || frame !== event.sender.mainFrame || !/^app:\/\/-\/(?:index|detached-window)\.html(?:[?#]|$)/.test(frame.url)
      || !Array.isArray(ids) || ids.length > 128 || !ids.every(id => typeof id==='string' && idPattern.test(id))) return {};
    const parents = {};
    for (const id of new Set(ids)) {
      const parent = await readForkParent(id).catch(() => undefined);
      if (parent !== undefined) parents[id] = parent;
    }
    return parents;
  });

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
