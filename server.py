"""Dependency-free MCP stdio server. Each input and output line is one JSON-RPC message."""
import json
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST = json.loads((ROOT / 'plugin.json').read_text(encoding='utf-8'))
VERSIONS = ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05')


def tool(name, title, description, read_only=False, destructive=False):
    return {
        'name': name, 'title': title, 'description': description,
        'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'annotations': {'readOnlyHint': read_only, 'destructiveHint': destructive,
                        'idempotentHint': True, 'openWorldHint': False},
    }


TOOLS = [
    tool('desktop_mods_status', 'Check desktop mods',
         'Read the installed desktop build, menu launcher and mod state.', read_only=True),
    tool('apply_desktop_mods', 'Apply desktop mods',
         'Apply or update the sidebar and message mods on the user’s cached desktop app.'),
    tool('remove_desktop_mods', 'Remove desktop mods',
         'Disable the patched launcher and remove unused patch files. Keeps profile preferences.',
         destructive=True),
]


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def dispatch(method, params):
    if method == 'initialize':
        version = params.get('protocolVersion')
        return {'protocolVersion': version if version in VERSIONS else VERSIONS[0],
                'capabilities': {'tools': {}},
                'serverInfo': {'name': MANIFEST['name'], 'version': MANIFEST['version']}}
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return {'tools': TOOLS}
    if method != 'tools/call':
        raise RpcError(-32601, 'Method not found')
    name = params.get('name')
    if name not in [entry['name'] for entry in TOOLS]:
        raise RpcError(-32602, 'Unknown tool')
    if params.get('arguments', {}) != {}:
        raise RpcError(-32602, 'This tool accepts no arguments')
    try:
        if sys.platform == 'win32':
            import windows_mods as manager
        elif sys.platform.startswith('linux'):
            import manager
        else:
            raise ValueError('Codex Desktop Mods supports Windows and Linux.')
        if name == 'desktop_mods_status':
            result = json.dumps(manager.status())
        elif name == 'apply_desktop_mods':
            result = manager.apply()
        else:
            result = manager.remove()
        return {'content': [{'type': 'text', 'text': result}]}
    except (OSError, ValueError, KeyError, struct.error, subprocess.SubprocessError) as error:
        return {'isError': True, 'content': [{'type': 'text', 'text': str(error)}]}


def respond(line):
    message_id = None
    try:
        message = json.loads(line)
        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
            raise RpcError(-32600, 'Invalid request')
        method = message.get('method')
        if not isinstance(method, str):
            raise RpcError(-32600, 'Invalid method')
        if 'id' not in message:
            return None
        message_id = message['id']
        params = message.get('params', {})
        if not isinstance(params, dict):
            raise RpcError(-32602, 'Expected an object for params')
        body = {'result': dispatch(method, params)}
    except (UnicodeError, json.JSONDecodeError):
        body = {'error': {'code': -32700, 'message': 'Invalid JSON'}}
    except RpcError as error:
        body = {'error': {'code': error.code, 'message': str(error)}}
    return {'jsonrpc': '2.0', 'id': message_id, **body}


if __name__ == '__main__':
    for line in sys.stdin.buffer:
        response = respond(line)
        if response is not None:
            print(json.dumps(response), flush=True)
