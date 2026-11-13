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

  // The preload takes one local snapshot per window before mounting the visual mods.
  ipcMain.on?.('local-mod-controls:visuals', event => {
    let values = {};
    const frame = event.senderFrame;
    if (frame && frame === event.sender.mainFrame && /^app:\/\/-\/(?:index|detached-window)\.html(?:[?#]|$)/.test(frame.url)) {
      try {
        const saved = JSON.parse(require('node:fs').readFileSync(path.join(home, 'desktop-mod-controls.json'), 'utf8'));
        const keys = ['wideChat', 'petDock', 'petMotion', 'timestamps', 'tokens', 'branchLines'];
        values = Object.fromEntries(keys.map(key => [key, saved[key] !== false]));
      } catch { /* First run uses the existing desktop defaults. */ }
    }
    event.returnValue = values;
  });

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
      for (const ext of ['.jsonl', '.jsonl.zst']) {
        const f = names.find(n => n.isFile() && n.name.startsWith('rollout-') && n.name.endsWith('-' + id + ext));
        if (f) return path.join(dir, f.name);
      }
    }
    // Older IDs do not encode a date. Use the existing thread catalogue.
    let db;
    try {
      const {DatabaseSync} = require('node:sqlite');
      db = new DatabaseSync(path.join(home,'state_5.sqlite'), {readOnly:true});
      return db.prepare('SELECT rollout_path FROM threads WHERE id = ?').get(id)?.rollout_path;
    } catch { return null; } finally { db?.close(); }
  }

  async function historyFile(id, cached) {
    let file = cached && await fs.realpath(cached).catch(() => null);
    if (!file) {
      const candidate = await findFile(id);
      if (!candidate) return null;
      // The catalogue can still name the plain transcript after compression.
      file = await fs.realpath(candidate).catch(error => {
        if (error.code !== 'ENOENT' || !candidate.endsWith('.jsonl')) throw error;
        return fs.realpath(candidate + '.zst');
      });
    }
    const root = await fs.realpath(home);
    const dir = path.relative(root,file).split(path.sep)[0];
    if (!['sessions','archived_sessions'].includes(dir) || !path.basename(file).replace(/\.zst$/, '').endsWith('-'+id+'.jsonl')) return null;
    return file;
  }

  function newTranscript() {
    return {turns:new Map(), current:null, total:null, pos:0, partial:Buffer.alloc(0), skipping:false};
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

  async function readLines(s, stream) {
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
  }

  function stamp(stat) {
    return [stat.size, stat.mtimeNs, stat.ctimeNs].join(':');
  }

  async function update(cacheEntry, id) {
    const file = await historyFile(id, cacheEntry.file);
    if (!file) { cacheEntry.data=null; cacheEntry.file=null; return; }
    const handle = await fs.open(file, 'r');
    let raw;
    try {
      const stat = await handle.stat({bigint:true});
      if (!stat.isFile()) throw Error('Expected a transcript file');
      const identity = stat.dev + ':' + stat.ino;
      const version = stamp(stat);
      const sameFile = cacheEntry.data && cacheEntry.file === file && cacheEntry.identity === identity;
      if (sameFile && cacheEntry.version === version) return;
      const compressed = file.endsWith('.zst');
      const size = Number(stat.size);
      const data = sameFile && !compressed && size > cacheEntry.data.pos ? cacheEntry.data : newTranscript();
      if (size) {
        raw = handle.createReadStream({start:data.pos, end:size-1, autoClose:false});
        if (compressed) {
          const {pipeline} = require('node:stream/promises');
          await pipeline(raw, require('node:zlib').createZstdDecompress(), chunks => readLines(data, chunks));
          if (stamp(await handle.stat({bigint:true})) !== version) throw Error('Archive changed during read');
        } else await readLines(data, raw);
      }
      Object.assign(cacheEntry, {file, identity, version, data});
    } finally { raw?.destroy(); await handle.close(); }
  }

  async function readForkParent(id) {
    const file = await historyFile(id);
    if (!file) return undefined;
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
  function colourAccount() {
    try {
      const id=JSON.parse(require('node:fs').readFileSync(path.join(home,'auth.json'),'utf8')).tokens?.account_id;
      return typeof id==='string'?id:'';
    } catch {return '';}
  }
  const sectionKey = key => /^(?:override-)?section\//.test(key);
  let migratedScope;
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
      const account=colourAccount();
      const bucket=account && state['electron-persisted-atom-state']?.['sidebar-custom-sections-v3']?.[account];
      for(const sec of bucket?.sections || []) if(sec.id===id){
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
    const account=colourAccount();
    const scope='@'+(account?require('node:crypto').createHash('sha256').update(account).digest('hex'):'signed-out')+'/';
    const dbKey = key => sectionKey(key) ? scope+key : key;
    if((mode==='write'||mode==='finish') && previous?.startsWith('@') && !previous.startsWith(scope))
      throw Error('The active account changed. Reopen the colour picker.');
    if (entries.length || migratedScope!==scope) {
      db.exec('BEGIN IMMEDIATE');
      try {
        let eligible=new Set();
        if(account && (migratedScope!==scope || mode==='seed' && entries.some(([k])=>sectionKey(k)))){
          let buckets={};
          try{buckets=JSON.parse(require('node:fs').readFileSync(path.join(home,'.codex-global-state.json'),'utf8'))['electron-persisted-atom-state']?.['sidebar-custom-sections-v3']||{};}
          catch(error){if(error.code!=='ENOENT')throw error;}
          eligible=new Set((buckets[account]?.sections||[]).map(s=>s.id));
          for(const [id,data] of Object.entries(buckets))if(id!==account)
            for(const s of data?.sections||[])eligible.delete(s.id);
          const copy=db.prepare('INSERT OR IGNORE INTO colours VALUES (?,?,?,?)');
          const legacy=db.prepare("SELECT * FROM colours WHERE key GLOB 'section/*' OR key GLOB 'override-section/*'").all();
          const claimed=new Set(db.prepare("SELECT key FROM colours WHERE key GLOB '@*/*'").all().map(r=>r.key.slice(r.key.indexOf('/')+1)));
          for(const r of legacy)if(eligible.has(r.key.split('/')[1]) && !claimed.has(r.key))copy.run(scope+r.key,r.value,r.rev,r.author);
          if(mode==='seed')entries=entries.filter(([k])=>!sectionKey(k)||eligible.has(k.split('/')[1])&&!claimed.has(k));
        }else if(mode==='seed')entries=entries.filter(([k])=>!sectionKey(k));
        if(mode==='finish'){
          const force=entries.find(([k,v])=>k.startsWith('override-') && v===null)?.[0];
          const group=force?.slice(9);
          const get=db.prepare('SELECT value FROM colours WHERE key=?');
          if(!group || get.get(dbKey(force))?.value!=='on')throw Error('Group override changed while saving');
          const colour=get.get(dbKey(group))?.value;
          if (!entries.every(([k,v])=>(k.startsWith('chat/') && v===colour) || (k===force && v===null) || (k===group && v===null)))
            throw Error('Group colour changed while saving');
        }
        const author = db.prepare('SELECT author FROM identity WHERE id=1').get().author;
        const rev = mode === 'seed' ? 0 : db.prepare('SELECT COALESCE(MAX(rev),0)+1 AS next FROM colours').get().next;
        const upsert = db.prepare(mode === 'seed' ? 'INSERT OR IGNORE INTO colours VALUES (?,?,?,?)' :
          'INSERT OR REPLACE INTO colours VALUES (?,?,?,?)');
        for (const [key,value] of entries) upsert.run(dbKey(key),value,rev,author);
        db.exec('COMMIT');
        migratedScope=scope;
      } catch (error) { db.exec('ROLLBACK'); throw error; }
    }
    const version = scope + db.prepare('PRAGMA data_version').get().data_version + ':' +
      db.prepare('SELECT total_changes() AS n').get().n;
    if (previous === version) return null;
    const rows=[];
    for(const {key,value} of db.prepare('SELECT key,value FROM colours').all()){
      if(key.startsWith(scope))rows.push([key.slice(scope.length),value]);
      else if(!key.startsWith('@') && !sectionKey(key))rows.push([key,value]);
    }
    const hints=new Map();
    for(const {chat,project,section} of db.prepare('SELECT chat,project,section FROM inherited').all()){
      const scoped=chat.startsWith(scope);
      if(scoped ? !section : chat.startsWith('@') || !project)continue;
      const id=scoped?chat.slice(scope.length):chat;
      const hint=hints.get(id)||{chat:id,project:null,section:null};
      if(scoped)hint.section=section;else hint.project=project;
      hints.set(id,hint);
    }
    return {version,account,entries:rows,inherited:[...hints.values()]};
  });

  const actionDir = path.join(home, 'chat-sync');
  const primaryFrame = event => event.senderFrame && event.senderFrame === event.sender.mainFrame &&
    /^app:\/\/-\/(?:index|detached-window)\.html(?:[?#]|$)/.test(event.senderFrame.url);
  ipcMain.handle('local-sidebar:saved-view-data', async event => {
    if (!primaryFrame(event)) throw Error('Unavailable sidebar.');
    async function snapshot(file) {
      try { return JSON.parse(await fs.readFile(file,'utf8')); }
      catch (error) { if (error.code==='ENOENT') return {}; throw error; }
    }
    const [layout,cfg,receipts] = await Promise.all([
      snapshot(path.join(home,'.codex-global-state.json')),
      snapshot(path.join(actionDir,'config.json')), snapshot(path.join(actionDir,'delivery.json'))
    ]);
    const projects = {}, devices = {};
    const aliases = layout['app-server-project-id-by-legacy-project-id-by-host']?.['local:'+home] || {};
    const stable = new Map(Object.entries(aliases).map(([key,id])=>[id,key]));
    for (const [key,record] of Object.entries(layout['local-projects'] || {}))
      if (idPattern.test(key)) projects[key] = record.name || key;
    const catalog = path.join(home,'state_5.sqlite');
    const chats = [];
    if (cfg.device) devices[cfg.device] = cfg.device_name || 'This device';
    const current = !!cfg.device && cfg.device === receipts.device;
    if (current) for (const [id,name] of Object.entries(receipts.peers || {}))
      if (idPattern.test(id) && typeof name==='string') devices[id]=name;
    const privateChats = new Set(cfg.private_chats || []);
    let db;
    try {
      if (await fs.stat(catalog).catch(error=>{if(error.code!=='ENOENT')throw error;})) {
        const {DatabaseSync} = require('node:sqlite');
        db = new DatabaseSync(catalog,{readOnly:true});
        const cols = new Set(db.prepare('PRAGMA table_info(threads)').all().map(row=>row.name));
        const fields = ['id','title','name','updated_at','updated_at_ms','project_id','source'].filter(key=>cols.has(key));
        if (cols.has('id')) for (const row of db.prepare('SELECT '+fields.join(',')+' FROM threads'+
            (cols.has('archived')?' WHERE archived=0':'')).iterate()) {
          if (!idPattern.test(row.id) || row.source?.includes('subagent')) continue;
          const receipt = current && !privateChats.has(row.id) ? receipts.chats?.[row.id] : null;
          const received = receipt?.received_by || [], busy = receipt?.waiting_on || [];
          for (const peer of [receipt?.origin,...received,...busy])
            if(peer && idPattern.test(peer.device)) devices[peer.device]=peer.name;
          const assignment = layout['thread-project-assignments']?.[row.id];
          const project = assignment?.projectId ?? stable.get(row.project_id) ?? row.project_id;
          chats.push({id:row.id,title:row.name || row.title || row.id,project,
            updated:row.updated_at_ms || (row.updated_at || 0)*1000,origin:receipt?.origin?.device,
            shared:['saved','received'].includes(receipt?.status),received:received.map(p=>p.device),busy:busy.map(p=>p.device)});
        }
      }
    } finally { db?.close(); }
    return {account:require('node:crypto').createHash('sha256').update(colourAccount()).digest('hex'),
      chats,projects,devices,device:cfg.device};
  });
  async function saveAction(name, value) {
    await fs.mkdir(actionDir, {recursive:true, mode:0o700});
    const tmp = path.join(actionDir, '.action-' + require('node:crypto').randomUUID());
    try {
      await fs.writeFile(tmp, JSON.stringify(value), {mode:0o600, flag:'wx'});
      await fs.rename(tmp, path.join(actionDir, name));
    } finally { await fs.unlink(tmp).catch(()=>{}); }
  }
  ipcMain.handle('local-chat-actions:context', async (event, input) => {
    if (!primaryFrame(event) || !input || !['handoff','inbox','file','family','project','organize','conflicts','performance','bookmarks','new-bookmark'].includes(input.action))
      throw new Error('Unavailable desktop action.');
    const ctx = {action:input.action,id:require('node:crypto').randomUUID(),at:Date.now()};
    for (const key of ['thread','turn','project','section']) if (typeof input[key]==='string' && idPattern.test(input[key])) ctx[key]=input[key];
    for (const [key,limit] of [['reference',4096],['item',160]]) if (typeof input[key]==='string') ctx[key]=input[key].slice(0,limit);
    if(input.action==='new-bookmark'){
      if(typeof input.anchor!=='string'||input.anchor.length>2048)throw Error('Invalid bookmark selection.');
      const [key,start,end,hash]=JSON.parse(input.anchor), address=JSON.parse(key);
      if(!Array.isArray(address)||!['local','cloud'].includes(address[0])||!Number.isSafeInteger(start)||
          !Number.isSafeInteger(end)||start<0||end<start||typeof hash!=='string')throw Error('Invalid message selection.');
      ctx.anchor=input.anchor;ctx.label=String(input.label||'').slice(0,160);ctx.account=colourAccount();
    }
    await saveAction('desktop-request.json',ctx);
  });
  ipcMain.handle('local-chat-actions:performance', async (event, report) => {
    if (!primaryFrame(event)) return;
    const value={at:Date.now()};
    for (const key of ['frames','slow','p95','switchMs']) {
      const n=report?.[key];
      value[key]=Number.isFinite(n) && n>=0 && n<=120000 ? Math.round(n) : null;
    }
    await saveAction('desktop-performance.json',value);
  });
  const clients=new Set();
  ipcMain.on?.('local-sidebar:parents', event => {
    const parents = {};
    if (primaryFrame(event)) try {
      const data = JSON.parse(require('node:fs').readFileSync(path.join(home,'.codex-global-state.json'),'utf8'));
      const account = colourAccount();
      const scoped = data['codex-mods-sidebar-groups-v1']?.[account]?.parents || {};
      const sections = new Set((data['electron-persisted-atom-state']?.['sidebar-custom-sections-v3']?.[account]?.sections || []).map(row => row.id));
      const aliases = data['app-server-project-id-by-legacy-project-id-by-host']?.['local:'+home] || {};
      for (const [section, parent] of Object.entries(scoped)) {
        if (!idPattern.test(section) || typeof parent !== 'string') continue;
        if (parent.startsWith('custom:') && sections.has(parent.slice(7)) && parent.slice(7) !== section)
          parents[section] = {section: parent.slice(7)};
        else if (idPattern.test(parent)) parents[section] = [parent, aliases[parent]].filter(Boolean);
      }
    } catch { /* A fresh profile has no nested sections. */ }
    event.returnValue = parents;
  });
  let watcher;
  async function dispatchCommand(){
    const targets=[...clients].filter(w=>!w.isDestroyed());
    const contents=targets.find(w=>w.isFocused()) || targets[targets.length-1];
    if (!contents || !/^app:\/\/-\/(?:index|detached-window)\.html(?:[?#]|$)/.test(contents.mainFrame.url)) return;
    const command=path.join(actionDir,'desktop-command.json');
    const claimed=path.join(actionDir,'.command-'+require('node:crypto').randomUUID());
    try {
      await fs.rename(command,claimed);
      const doc=JSON.parse(await fs.readFile(claimed,'utf8'));
      if(Date.now()-doc.at>=0 && Date.now()-doc.at<60000 && (doc.action==='measure' || (doc.action==='bookmark' && doc.account===colourAccount() && idPattern.test(doc.thread) && typeof doc.anchor==='string' && doc.anchor.length<=2048) ||
          (doc.action==='navigate' && idPattern.test(doc.thread) && (!doc.turn || idPattern.test(doc.turn)))))
        {
          if (doc.action === 'bookmark' && doc.cloud)
            contents.send('codex_desktop:message-for-view', {type:'navigate-to-route', path:'/c/' + doc.thread});
          contents.send('local-chat-actions:command',doc);
        }
    } catch { /* Another window may already have claimed the request. */ }
    finally {await fs.unlink(claimed).catch(()=>{});}
  }
  ipcMain.on?.('local-chat-actions:listen',async event=>{
    if(!primaryFrame(event))return;
    const contents=event.sender;
    if(!clients.has(contents)){
      clients.add(contents);
      contents.once('destroyed',()=>{clients.delete(contents);if(!clients.size){watcher?.close();watcher=null;}});
    }
    try {
      await fs.mkdir(actionDir,{recursive:true,mode:0o700});
      if(!watcher){
        watcher=require('node:fs').watch(actionDir, (_event,file)=>{
          if(!file || file==='desktop-command.json') void dispatchCommand();
        });
        watcher.on('error',()=>{watcher?.close();watcher=null;});
      }
      await dispatchCommand();
    } catch { /* Other desktop features work without the local panel. */ }
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
      s={};cache.set(id,s);
      if(cache.size>8) for(const [key,v] of cache) if(key!==id && !v.busy){cache.delete(key);break;}
    } else {cache.delete(id);cache.set(id,s);}
    if (!s.busy) s.busy=update(s,id).catch(()=>{s.file=null; s.data=null;}).finally(()=>{s.busy=null;});
    await s.busy;
    const result={};
    for(const key of turns) {
      const t=s.data?.turns.get(key);
      if(t) result[key]={start:t.start,reply:t.reply??t.end,end:t.end,tokens:t.unknownDelta?null:t.tokens,total:t.total};
    }
    return result;
  });
})();
