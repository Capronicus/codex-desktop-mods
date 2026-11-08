#!/usr/bin/env python3
"""Install, launch or remove the local Codex sidebar colour mod for Linux."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile

SYSTEM = Path('/usr/lib/chatgpt')
ARCHIVE = SYSTEM / 'resources/app.asar'
SOURCE = Path(__file__).resolve().parent
DATA = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
INSTALL = DATA / 'codex-sidebar-colours'
CACHE = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'codex-sidebar-colours'
DESKTOP = DATA / 'applications/chatgpt.desktop'
STOCK_DESKTOP = Path('/usr/share/applications/chatgpt.desktop')
MARKER = 'X-Codex-Sidebar-Colours=true'
ASSET = 'app-primary-f921a74f3eba.js'
ORIGINAL_HASH = '43c553113064956188f435bd0ce433aa46dad054aeb8e9919c2495b0482182a0'
VERSION = '26.1027.51404'
MOD_FILES = ('sidebar-colours.py', 'sidebar-colours.js', 'chat-details-main.cjs',
             'chat-details-preload.cjs', 'chat-details.js', 'chat-details-cloud.js')
TRANSCRIPT = 'virtualized-turn-list-849fe48ea6b5.js'
CLOUD_TRANSCRIPT = 'chatgpt-conversation-turn-content-48014b7b6a96.js'


def subst(source, old, new, count=1):
    if source.count(old) != count:
        raise ValueError('Unrecognised sidebar renderer. Install a compatible mod for this app version.')
    return source.replace(old, new)


def modify(js):
    # The renderer's own selector resolves the membership of a keyed thread, even
    # when it appears in Recents/Pinned and its project hasn't been expanded.
    js = subst(js, 'nt=$e===`sidebar`||tt||et,rt=Ke()',
               'nt=$e===`sidebar`||tt||et,cscKey=nt?Se?.[Q5e.sidebarThreadId]:null,'
               'cscOwner=Z(cscKey?Mme:null,cscKey),cscProjects=Y(cscOwner?sidebarColourLocalProjects:null),'
               'cscAccount=Y(cscOwner?Zh:null),cscDisplayOwner=cscOwner?tx(cscOwner,cscProjects,cscAccount)??cscOwner:e.sidebarColourOwner,'
               'cscId=cscKey?qg(cscKey)?.threadId:null,'
               'cscServer=Z(cscId?HT:null,cscId),cscStable=cscServer?Mm(cscServer):cscKey??e.sidebarColourId,'
               'cscTemporary=cscKey??e.sidebarColourPending,rt=Ke()')
    js = subst(js, 'ft=`${ot}-recording`,pt;t[0]',
               'ft=`${ot}-recording`,pt;(0,aK.useEffect)(()=>{sidebarColours.migrate(cscTemporary,cscStable)},[cscTemporary,cscStable]);t[0]')
    js = subst(js, '(Zat,{...Se,ref:n,className:Bn,',
               '(Zat,{...Se,"data-csc-chat-key":nt?cscStable:void 0,"data-csc-owner":nt?cscDisplayOwner:void 0,'
               '"data-csc-activity-day":nt?sidebarColours.activityDay(Se?.["data-csc-updated"]??e.sidebarColourUpdated):void 0,'
               '"data-csc-cloud-pinned":nt&&e.sidebarColourPinned?"true":void 0,ref:n,className:Bn,')
    js = subst(js, 'className:Kn,dir:`auto`,children:qn',
               'className:Kn,dir:`auto`,"data-csc-chat-label":"",children:qn')
    js = subst(js, 'y=i==null?c:(0,RQ.jsx)(`span`,{className:i,children:c})',
               'y=(0,RQ.jsx)(`span`,{className:i,"data-csc-project-label":"",children:c})')
    js = subst(js, 'return T6n({archiveEnabled:', 'return T6n({cscProject:r.projectId,archiveEnabled:', 2)
    js = subst(js, 'function T6n({archiveEnabled:e', 'function T6n({cscProject,archiveEnabled:e')
    js = subst(js, 'icon:d0.edit,message:f0.edit,onSelect:a},...m.length',
               'icon:d0.edit,message:f0.edit,onSelect:a},sidebarColours.menuItem("project",cscProject),...m.length')
    js = subst(js, 'selectedThreadKeys:i,threadKey:a}){if(i.length<2)return t;',
               'selectedThreadKeys:i,threadKey:a}){if(i.length<2)return [sidebarColours.menuItem("chat",a),...t];')
    # Carry the native last-update timestamps through the local, remote task and
    # pending row adapters. Updating the colour never writes to a chat's history.
    for fields, timestamp in [
        ('hostId:y,id:m,kind:`local`,pinned:o,selected:s,title:void 0', 'Ie?.updatedAtMs??p?.updatedAt??g??e.at'),
        ('hostId:null,id:t,kind:`remote`,pinned:o,selected:s,title:e.task.title??``', 'e.task.updated_at??e.task.created_at'),
        ('hostId:n.hostId,id:e.key,kind:`local`,pinned:o,selected:s,title:n.title', 'e.at??n.task?.createdAt'),
        ('hostId:n.hostId,id:r,kind:`local`,pinned:o,selected:s,title:n.label', 'n.createdAt'),
    ]:
        attributes = 'Zw.sidebarThreadRow({active:d,' + fields + '})'
        js = subst(js, 'dataAttributes:' + attributes,
                   'dataAttributes:{...' + attributes + ',"data-csc-updated":' + timestamp + '}')
    # ChatGPT conversations use a different row adapter. Use its server ID and
    # project ID both inside the project and when the same row appears in Recents.
    js = subst(js, '(aY,{ref:R,floatStatusIconsEnd:!0,revealActionsOnStatusHover:',
               '(aY,{sidebarColourId:(i??S)?z_(i??S):void 0,sidebarColourPending:S?z_(S):void 0,'
               'sidebarColourOwner:g??r?.gizmo_id,sidebarColourPinned:fe,'
               'sidebarColourUpdated:r?.update_time??r?.create_time??te,ref:R,floatStatusIconsEnd:!0,revealActionsOnStatusHover:')
    js = subst(js, 'pn=(0,y$.jsx)(`div`,{className:`flex flex-col gap-px`,children:(0,y$.jsx)(Wt,',
               'pn=(0,y$.jsx)(`div`,{"data-csc-project-group":C,className:`flex flex-col gap-px`,children:(0,y$.jsx)(Wt,')
    js = subst(js, '{id:`organization`,items:i},{id:`continue`,items:r?[]:m}',
               '{id:`organization`,items:t.surface===`sidebar`&&t.conversationId?'
               '[sidebarColours.menuItem("chat",z_(t.conversationId)),...i]:i},{id:`continue`,items:r?[]:m}')
    js = subst(js, '(qJn,{ref:ne,linkedLocalProjectId:T,', '(qJn,{ref:ne,projectId:C,linkedLocalProjectId:T,')
    # Read the prop at the top: the block patched below declares its own `let e`,
    # so `e.projectId` inside it throws "Cannot access 'e' before initialization".
    js = subst(js, 'function qJn(e){"use forget";let t=(0,XJn.c)(59)',
               'function qJn(e){"use forget";let cscProjectId=e.projectId,t=(0,XJn.c)(60)')
    js = subst(js, 't[16]!==E){if(D=[{id:C,message:w,icon:T,onSelect:E},',
               't[16]!==E||t[59]!==cscProjectId){t[59]=cscProjectId;if(D=[sidebarColours.menuItem("project",cscProjectId),'
               '{id:C,message:w,icon:T,onSelect:E},')
    # Module declarations/imports remain at top level. The code runs before the
    # first call of an exported lazy component, independently of load order.
    return (SOURCE / 'sidebar-colours.js').read_text() + '\n' + js


def signature():
    values = []
    for source in (ARCHIVE, SYSTEM / 'ChatGPT', SYSTEM / 'resources/linux-package-metadata.json'):
        info = source.stat()
        values.append([info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns])
    return values


def digest(data):
    return hashlib.sha256(data).hexdigest()


def patch_archive(destination):
    with ARCHIVE.open('rb') as src:
        size, pickle_size, _, json_size = struct.unpack('<IIII', src.read(16))
        if size != 4:
            raise ValueError('Unknown app archive format')
        tree = json.loads(src.read(json_size))
        assets = tree['files']['webview']['files']['assets']['files']
        desktop = tree['files']['.vite']['files']['build']['files']
        data_start = 8 + pickle_size
        end = src.seek(0, os.SEEK_END)
        additions = []
        for name, directory, expected, extension in [
            (ASSET, assets, ORIGINAL_HASH, None),
            (TRANSCRIPT, assets, 'c3d825dfdf4b36bd83dd56a7948ef4f0807f325b0de60763acca9af8eafe4426', 'chat-details.js'),
            (CLOUD_TRANSCRIPT, assets, 'fbe067e82f77aa9026e2badb1992111faebda490bbe4beb867096b0370ca5fee', 'chat-details-cloud.js'),
            ('early-bootstrap.js', desktop, '466efbe496786705721ae93dd7cd2be7c08d0f760d53adf984dfde77af93fecf', 'chat-details-main.cjs'),
            ('preload.js', desktop, 'aa8be920fd14a9b98eca16c91d97a55105b196698fda36d5063e966620a0c54a', 'chat-details-preload.cjs'),
        ]:
            entry = directory[name]
            src.seek(data_start + int(entry['offset']))
            original = src.read(entry['size'])
            if digest(original) != expected:
                raise ValueError(f'Incompatible desktop asset: {name}')
            js = original.decode()
            if name == ASSET:
                js = modify(js)
            elif name == TRANSCRIPT:
                js = subst(js, '"data-turn-key":p,children:x', '"data-turn-key":p,children:withChatDetails(i,x,L,ke)')
            elif name == CLOUD_TRANSCRIPT:
                js = subst(js, 'function aP(e){let t=(0,wP.c)(496)', 'function aP(e){let t=(0,wP.c)(497)')
                js = subst(js, 't[4]!==y||t[5]!==k?(we=e=>(0,$.jsx)(yC,{item:k,index:y,children:e}),t[4]=y',
                           't[496]!==f||t[4]!==y||t[5]!==k?(we=e=>(0,$.jsx)(yC,{item:k,index:y,children:withCloudChatTime(e,k,f)}),t[496]=f,t[4]=y')
            if extension:
                js += '\n' + (SOURCE / extension).read_text()
            patched = js.encode()
            block = entry['integrity']['blockSize']
            entry.update(offset=str(end - data_start), size=len(patched))
            entry['integrity'].update(hash=digest(patched), blocks=[
                digest(patched[i:i+block]) for i in range(0, len(patched), block)
            ])
            additions.append(patched)
            end += len(patched)
        encoded = json.dumps(tree, ensure_ascii=False, separators=(',', ':')).encode()
        payload = (4 + len(encoded) + 3) & ~3
        with destination.open('wb') as dst:
            dst.write(struct.pack('<IIII', 4, payload + 4, payload, len(encoded)))
            dst.write(encoded)
            dst.write(bytes(payload - 4 - len(encoded)))
            src.seek(data_start)
            shutil.copyfileobj(src, dst, 4 * 1024 * 1024)
            for patched in additions:
                dst.write(patched)


def qt_arg(arg):
    # Desktop Entry Exec has two escaping layers: the value, then the argument.
    escaped = ''.join('\\' + c if c in '\\"$`' else c for c in str(arg))
    return '"' + escaped.replace('\\', '\\\\').replace('%', '%%') + '"'


def desktop_text():
    text = STOCK_DESKTOP.read_text()
    old = 'Exec=chatgpt %U'
    return subst(text, old, f'Exec=/usr/bin/python3 {qt_arg(INSTALL / "sidebar-colours.py")} launch %U\n{MARKER}')


def app_build():
    return json.loads((SYSTEM / 'resources/linux-package-metadata.json').read_text()).get('version')


def install():
    desktop = desktop_text()
    if DESKTOP.exists() and MARKER not in DESKTOP.read_text().splitlines():
        raise ValueError(f'An existing personal launcher already uses {DESKTOP}. It has been preserved.')
    if app_build() != VERSION:
        raise ValueError(f'This mod supports desktop {VERSION}. Installed version is {app_build()}.')
    before = signature()
    CACHE.mkdir(mode=0o700, parents=True, exist_ok=True)
    revision = digest(b''.join((SOURCE / name).read_bytes() for name in MOD_FILES)
                      + repr(before).encode())[:16]
    target = CACHE / ('runtime-' + revision)
    if not target.is_dir():
        with tempfile.TemporaryDirectory(dir=CACHE) as tmp:
            stage = Path(tmp) / 'app'
            stage.mkdir()
            resources = stage / 'resources'
            resources.mkdir()
            for item in SYSTEM.iterdir():
                dest = stage / item.name
                if item.name == 'ChatGPT':
                    shutil.copy2(item, dest)
                elif item.name != 'resources':
                    dest.symlink_to(item)
            for item in (SYSTEM / 'resources').iterdir():
                if item.name != 'app.asar':
                    (resources / item.name).symlink_to(item)
            patch_archive(resources / 'app.asar')
            if before != signature():
                raise ValueError('The app was updated during installation. Run the installer again.')
            stage.rename(target)
    INSTALL.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in MOD_FILES:
        if (SOURCE / name).resolve() != (INSTALL / name).resolve():
            atomic_write(INSTALL / name, (SOURCE / name).read_bytes())
    atomic_write(INSTALL / 'runtime.json', json.dumps({'path': str(target), 'system': before}).encode())
    DESKTOP.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(DESKTOP, desktop.encode())
    print('Installed. Fully quit ChatGPT, then open ChatGPT from the applications menu.')


def atomic_write(path, content):
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(content)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def launch(args):
    meta = json.loads((INSTALL / 'runtime.json').read_text())
    runtime = Path(meta['path'])
    exe = runtime / 'ChatGPT'
    if signature() != meta['system'] or not exe.is_file():
        warn = 'The desktop app changed or the colour mod cache was removed. Opening the installed app. Reinstall a compatible sidebar colour mod to restore colours.'
        if shutil.which('notify-send'):
            subprocess.run(['notify-send', 'Codex sidebar colours', warn], check=False)
        print(warn, file=sys.stderr)
        os.execv(SYSTEM / 'ChatGPT', ['ChatGPT', *args])
    # Ubuntu's AppArmor grants user namespaces by executable path. On hosts where
    # a copied executable lacks that grant, Chromium can use a system-owned SUID
    # sandbox helper. This is a standard Chromium sandbox, never a sandbox bypass.
    for path in [SYSTEM / 'chrome-sandbox', Path('/opt/google/chrome/chrome-sandbox'),
                 Path('/usr/lib/chromium/chrome-sandbox')]:
        if path.is_file():
            info = path.stat()
            if info.st_uid == 0 and info.st_mode & stat.S_ISUID and not info.st_mode & 0o022:
                os.environ['CHROME_DEVEL_SANDBOX'] = str(path)
                break
    os.execv(exe, [str(exe), *args])


def uninstall():
    if DESKTOP.is_file() and MARKER in DESKTOP.read_text().splitlines():
        DESKTOP.unlink()
    for p in (CACHE, INSTALL):
        if p.is_dir():
            shutil.rmtree(p)
    print('Removed the colour mod. Reopen ChatGPT from the applications menu.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['install', 'launch', 'uninstall'])
    args, extra = parser.parse_known_args()
    if args.operation == 'launch':
        launch(extra)
    else:
        if extra:
            parser.error('Unexpected arguments')
        lock_dir = Path(os.environ.get('XDG_RUNTIME_DIR') or '/tmp')
        with (lock_dir / f'codex-sidebar-colours-{os.getuid()}.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            (install if args.operation == 'install' else uninstall)()


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, struct.error) as error:
        sys.exit(str(error))
