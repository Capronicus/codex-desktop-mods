"""Adapt reviewed Rspack module factories without depending on whole bundle hashes."""
from collections import defaultdict
import hashlib
import json
import re


def replace(source, pattern, replacement, count=1):
    result, hits = re.subn(pattern, replacement, source)
    if hits != count:
        raise ValueError(f'Unexpected number of matches for renderer hook {pattern}: {hits}')
    return result


def adapt(role, source, files, pet_assets):
    if role == 'dependency':
        return source
    header, body = source.split('{', 1)
    require = header[:-1].split(',')[-1]
    if role == 'prefs':
        factory = files('sidebar-colours.js')
        return header + '{' + f"""
          {require}.d(t,{{sidebarColours:()=>cscGet}});
          let cscInstance;
          function cscGet() {{
            return cscInstance ??= createSidebarColours({{
              preferencesReady:t.Uz, getPreference:t.TB, setPreference:t.jx,
              preferenceKeys:t.b9, watchPreferencePrefix:t.Gk,
              readProjectPage:args=>{require}("YCbu").Pb(args)
            }});
          }}
          {factory}
        """ + body
    extra = ''
    if role == 'pet-api':
        hooks = re.search(re.escape(require) + r'\("(y5DF|zTeN)"\)', body)[1]
        sprite = 'DRpS' if hooks == 'y5DF' else 'FPUb'
        exports = header[:-1].split(',')[1]
        extra = files('chat-layout.js') + f"""
          {require}.d({exports},{{cscLayout:()=>cscLayoutElement}});
          const cscPetAssets={json.dumps(pet_assets)};
          function cscUseNativePet() {{
            const hooks={require}("{hooks}"), avatars={require}("SqlE");
            const open=hooks.Ul({require}("QLtS").k);
            const selection={require}("dYlv").WU();
            const id=selection.selectedAvatarId ?? selection.selectedAvatar.id;
            const local=hooks._U({require}("l2C7").Zn,id).data;
            const cloud=hooks._U({require}("KPGh").lc,id).pet;
            const avatar=avatars.i0(local && [local],cloud && [cloud]).find(pet=>pet.id===id);
            return {{overlayOpen:open,avatar,source:avatar && avatars.aN(avatar),
              assets:cscPetAssets,sprite:{require}("{sprite}").bH,
              summon:()=>{require}("TL6G").O1.dispatchMessage("avatar-overlay-open",{{}})}};
          }}
        """
    elif role == 'row':
        # These selectors are exported by separately fingerprinted modules.
        hook_alias = re.search(r'\(0,(\w+)\.Ul\)', body)[1]
        hooks = re.search(r'\b' + hook_alias + r'=\w+\("([^"]+)"\)', body)[1]
        extra = f"""
          const cscHooks={require}("{hooks}"), cscReact={require}("zd8w");
          function useSidebarColours(active,attrs,props) {{
            const key=active ? attrs?.["data-app-action-sidebar-thread-id"] : null;
            const owner=cscHooks._U(key ? {require}("QGyr").$i : null,key);
            const projects=cscHooks.Ul(owner ? {require}("ndjw").Gv : null);
            const account=cscHooks.Ul(owner ? {require}("WVuR").d : null);
            const linked=owner && {require}("gLQa")._z(owner,projects,account);
            const id=key?.startsWith("local:") ? key.slice(6) : null;
            const server=cscHooks._U(id ? {require}("V3rO").ic : null,id);
            const stable=server ? "local:"+server : key ?? props.cscId;
            const pending=key ?? props.cscPending;
            cscReact.useEffect(()=>{{csc.migrate(pending,stable)}},[pending,stable]);
            if(!active) return;
            return {{"data-csc-chat-key":stable,"data-csc-owner":linked || owner || props.cscOwner,
              "data-csc-cloud-pinned":props.cscPinned || undefined,
              "data-csc-activity-day":csc.activityDay(attrs?.["data-csc-updated"] ?? props.cscUpdated)}};
          }}
        """
        attrs = re.search(r'dataAttributes:(\w+)\}=e', body)[1]
        body = replace(body, r'(\w+)=("sidebar"===\w+\|\|\w+\|\|\w+),',
                       lambda m: f'{m[0]}cscAttrs=useSidebarColours({m[1]},{attrs},e),')
        body = replace(body, r'(\{\.\.\.' + attrs + r',)(ref:\w+,className:\w+,"data-sidebar-indicator")',r'\1...cscAttrs,\2')
        body = replace(body, r'(className:\w+,dir:"auto",)(children:\w+\})',r'\1"data-csc-chat-label":"",\2')
    elif role == 'project':
        body = replace(body, r'null==(\w+)\?(\w+):\(0,(\w+)\.jsx\)\("span",\{className:\1,children:\2\}\)',
                       r'(0,\3.jsx)("span",{className:\1,"data-csc-project-label":"",children:\2})')
    elif role == 'entries':
        def dated(m):
            title = m[2]
            stamp = {'n.label':'n.createdAt', 'n.title':'e.at??n.task?.createdAt',
                     'e.task.title??""':'e.task.updated_at??e.task.created_at',
                     'void 0':'tD?.updatedAtMs??i?.updatedAt??d??e.at'}[title]
            fork = (',"data-csc-fork-id":a,"data-csc-fork-host":h,"data-csc-fork-parent":i?.forkedFromId??tD?.forkedFromId'
                    if title == 'void 0' else '')
            return 'dataAttributes:{...'+m[1]+',"data-csc-updated":'+stamp+fork+'}'
        body = replace(body, r'dataAttributes:(\w+\.rW\.sidebarThreadRow\(\{[^{}]*?title:([^{}]+?)\}\))', dated, 4)
    elif role == 'cloudsidebar':
        # These are bindings from the reviewed cloud row and project components.
        if 'ref:ed,floatStatusIconsEnd:!0' in body:
            thread, pending, owner, chat, pinned, recency, group = 'N', '$', 'z', 'M', 'eT', 'ef', 'eA'
        else:
            thread, pending, owner, chat, pinned, recency, group = 'L', 'Y', 'H', 'N', 'eM', 'ev', 'ey'
        props = (f'cscId:({thread}??{pending})?"chatgpt:conversation:"+({thread}??{pending}):undefined,'
                 f'cscPending:{pending}?"chatgpt:conversation:"+{pending}:undefined,'
                 f'cscOwner:{owner}??{chat}?.gizmo_id,cscPinned:{pinned},'
                 f'cscUpdated:{chat}?.update_time??{chat}?.create_time??{recency},')
        body = replace(body, r'ref:\w+,floatStatusIconsEnd:!0,revealActionsOnStatusHover:', lambda m: props + m[0])
        body = replace(body, r'(ref:\w+,)linkedLocalProjectId:', lambda m: m[1]+f'cscProjectId:{group},linkedLocalProjectId:')
        body = replace(body, r'className:"flex flex-col gap-px",children:(?=\(0,\w+\.jsx\)\([\w.]+,\{animateContentResize:!0,expanded:\w+,contentKey:'+group+r'\b)',
                       lambda m: f'"data-csc-project-group":{group},'+m[0])
    elif role == 'projectmenu':
        body = replace(body, r'(\w+)\.projectId,!1\)\}\);return eK\(\{archiveEnabled:',r'\1.projectId,!1)});return eK({cscId:\1.projectId,archiveEnabled:',2)
        body = replace(body,r'function eK\(\{archiveEnabled:', 'function eK({cscId,archiveEnabled:')
        body = replace(body,r'("edit-project",icon:\w+\.edit,message:\w+\.edit,onSelect:\w+\},)',
                       r'\1csc.menuItem("project",cscId),')
    elif role == 'linkedmenu':
        memo_header = r'([\w$]+)=\(0,r\.c\)\((\d+)\),\{align:'
        memo, slots = re.search(memo_header, body).groups()
        body = replace(body, memo_header, f'cscId=e.cscProjectId,{memo}=(0,r.c)({int(slots)+1}),{{align:')
        body = replace(body, re.escape(memo)+r'\[16\]!==t\)\{(let [\w,]+;)if\(n=\[\{id:(\w+),message:V',
                       lambda m: f'{memo}[16]!==t||{memo}[{slots}]!==cscId){{{m[1]}{memo}[{slots}]=cscId;'
                       f'if(n=[...cscId?[csc.menuItem("project",cscId)]:[],{{id:{m[2]},message:V')
    elif role == 'chatmenu':
        body = replace(body,r'(selectedThreadKeys:(\w+),threadKey:(\w+)\}\)\{if\(\2.length<2\)return )t;',
                       r'\1[csc.menuItem("chat",\3),...t];')
    elif role == 'cloudmenu':
        body = replace(body,r'id:"organization",items:(\w+)\},\{id:"continue"',
                       r'id:"organization",items:t.surface==="sidebar"&&t.conversationId?[csc.menuItem("chat","chatgpt:conversation:"+t.conversationId),...\1]:\1},{id:"continue"')
    elif role == 'transcript':
        entry = re.search(r'entry:(\w+),latestTurnFooter:\w+,latestTurnFollowContentRef:\w+\}',body)[1]
        # Module-scope aliases avoid the row renderer's shadowed React binding.
        extra = f'const cscReact={require}("zd8w"),cscJsx={require}("ABep");\n' + files('chat-details.js')
        body = replace(body, r'(className:"\[&_\[data-virtualized-turn-content\]\]:\[content-visibility:visible\]","data-turn-key":\w+,children:)(\w+)',
                       lambda m: m[1]+f'withChatDetails({entry},{m[2]},cscReact,cscJsx)')
    elif role == 'cloudtime':
        header_re = r'let t,n,i=\(0,s\.c\)\((\d+)\),\{dilPreparationId:'
        slot = int(re.search(header_re,body)[1])
        body = replace(body,header_re,f'let t,n,i=(0,s.c)({slot+1}),{{dilPreparationId:')
        graph = re.search(r'\(\{conversationId:e,messageId:t\},\{get:n\}\)=>\{let a=n\((\w+\.Jv),e\)\?\.\[t\]\?\.message',body)[1]
        scope = re.search(r'\(0,u\.i1\)\((\w+\.u),\(\{conversationId:e,messageId:t\}',body)[1]
        # The native graph and scope variables are initialized later in this factory.
        cloud = files('chat-details-cloud.js')
        cloud = cloud.replace('g(Y,',f'(0,u.i1)({scope},').replace('get(Nr,',f'get({graph},')
        cloud = cloud.replace('EP.useMemo','f.useMemo').replace('=Z(chatDetailsCloudTimeSelector','=u._U(chatDetailsCloudTimeSelector').replace('$.','a.')
        extra = cloud
        body = replace(body,r'i\[4\]!==(\w+)\|\|i\[5\]!==([\w$]+)\?\(n=e=>\(0,a.jsx\)\((\w+),\{item:\2,index:\1,children:e\}\),i\[4\]=\1',
                       lambda m:f'i[{slot}]!==C||i[4]!=={m[1]}||i[5]!=={m[2]}?(n=e=>(0,a.jsx)({m[3]},{{item:{m[2]},index:{m[1]},children:withCloudChatTime(e,{m[2]},C)}}),i[{slot}]=C,i[4]={m[1]}')
    else:
        raise ValueError(f'Unknown renderer adapter {role}')
    if role in ('row','project','projectmenu','chatmenu','cloudmenu','linkedmenu'):
        extra = f'const csc={require}("kpIY").sidebarColours();\n' + extra
    return header + '{\n' + extra + '\n' + body


def section_colour_action(section):
    return ('{id:"section-colour",message:{id:"local.sidebarColours.section",defaultMessage:"Section colour…"},'
            'onSelect:()=>window.dispatchEvent(new CustomEvent("csc-open-section-colour",{detail:'+section+'.id}))},')


def add_pet_controls(data):
    """Keep the floating pet's own menu, item format and close-before-select behaviour."""
    settings = re.search(
        rb'\{id:(?P<q>["`])pet-settings(?P=q),icon:(?P<icon>[\w.$]+),'
        rb'message:(?P<message>\(0,[\w.$]+\)|[\w$]+)\(\{id:(?P=q)petOverlay\.settings(?P=q),'
        rb'[^{}]*\}\),selectAfterClose:!0,onSelect:[\w$]+\},(?=\{id:(?P=q)close-avatar(?P=q))',
        data,
    )
    if settings is None:
        return data
    item = (b'{id:"mod-controls",icon:' + settings['icon'] +
            b',message:' + settings['message'] +
            b'({id:"local.pet.modControls",defaultMessage:"Mod Controls"}),'
            b'selectAfterClose:!0,onSelect:()=>window.localChatDetails.openModControls()},')
    return data[:settings.end()] + item + data[settings.end():]



def renderer_patches(directory, read, source, pet_assets):
    catalog = json.loads((source/'renderer-modules.json').read_text(encoding='utf-8'))
    starts = defaultdict(list)
    for sha, info in catalog.items():
        starts[info['start'].encode()].append((sha, info))
    # Search actual factories. The bundler is free to rename or regroup chunks.
    pattern = re.compile(rb'(?<=[{,])(?:' + b'|'.join(re.escape(s) for s in starts) + rb')')
    found = set()
    # Validate just the native overlay listener. Changes elsewhere in the app
    # shell do not affect this integration. Its pet API is fingerprinted above.
    host_hook = re.compile(
        rb'\{selectedAvatar:\w+\}=\(0,(?P<pet>\w+)\.WU\)\(\);return '
        rb'(?:(?!\}function ).){1,1800}?"avatar-overlay-open-state-changed"'
        rb'[^}]{1,250}?\(0,(?P<react>\w+)\.useEffect\)\((?P<request>\w+),\w+\),'
        rb'(?P<slot>null)\}function (?P=request)\(\)\{\w+\.O1\.dispatchMessage\("avatar-overlay-open-state-request"')
    host_count = 0
    section_count = 0
    for name, entry in directory.items():
        if not name.endswith('.js') or entry.get('unpacked'):
            continue
        original = read(entry)
        edits = []
        if b'avatar-overlay-open-state-request' in original:
            for host in host_hook.finditer(original):
                start, end = host.span('slot')
                edits.append((start, end, host['pet'] + b'.cscLayout(' + host['react'] + b')'))
                host_count += 1
        for match in pattern.finditer(original):
            for sha, info in starts[match[0]]:
                end = match.start() + info['size']
                factory = original[match.start():end]
                if hashlib.sha256(factory).hexdigest() == sha:
                    found.add((info['role'], info['id'] if info['role']=='dependency' else ''))
                    if info['role'] != 'dependency':
                        code = adapt(info['role'], factory.decode(), lambda s: (source/s).read_text(encoding='utf-8'), pet_assets)
                        edits.append((match.start(),end,code.encode()))
                    break
            else:
                raise ValueError('Unrecognised desktop renderer module '+match[0].decode().split('(')[0])
        for start,end,code in sorted(edits, reverse=True):
            original = original[:start]+code+original[end:]
        section_menu = b'id:"edit-section"' in original
        if section_menu:
            menu = rb'(\{id:"edit-section",message:\w+\.edit,icon:[\w.]+,onSelect:\(\)=>[^{}]{1,90}\{section:(\w+)\}\)\},)(?=\{id:"section-chat-actions-separator")'
            original, hits = re.subn(menu,lambda m:m[1]+section_colour_action(m[2].decode()).encode(),original)
            section_count += hits
        patched = add_pet_controls(original) if b'{id:"pet-settings"' in original else original
        if edits or section_menu or patched is not original:
            yield entry,patched
    if section_count != 1:
        raise ValueError('Unrecognised desktop section menu')
    if host_count != 1:
        raise ValueError('Unrecognised desktop pet window listener')
    required = {(i['role'], i['id'] if i['role']=='dependency' else '') for i in catalog.values()}
    if required != found:
        raise ValueError('Missing desktop renderer modules: '+', '.join(id or role for role,id in sorted(required-found)))
