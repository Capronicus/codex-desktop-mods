"""Dependency-free MCP stdio server. Each input and output line is one JSON-RPC message."""
import json
import sys
from pathlib import Path

import activation

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
    tool('desktop_mods_compatibility', 'Check desktop compatibility',
         'List the renderer modules of the installed desktop build that the mods no longer recognise, '
         'and the first hook that does not fit.', read_only=True),
    tool('remove_desktop_mods', 'Remove desktop mods',
         'Disable the patched launcher and remove unused patch files. Keeps profile preferences.',
         destructive=True),
] + activation.TOOLS


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
    args = params.get('arguments', {})
    if name == 'report_activation':
        if not isinstance(args, dict) or set(args) != {'destination'} or not isinstance(args['destination'], str):
            raise RpcError(-32602, 'Expected the confirmed counter destination')
    elif args != {}:
        raise RpcError(-32602, 'This tool accepts no arguments')
    try:
        if name in ('activation_status', 'report_activation'):
            choice = activation.status() if name == 'activation_status' else activation.report(args['destination'])
            return {'content': [{'type': 'text', 'text': json.dumps(choice)}]}
        if name == 'desktop_mods_compatibility':
            import compatibility
            return {'content': [{'type': 'text', 'text': json.dumps(compatibility.report())}]}
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
        content = [{'type': 'text', 'text': result}]
        if name == 'apply_desktop_mods':
            invitation = activation.offer()
            if invitation:
                content.append({'type': 'text', 'text': json.dumps({'activation_offer': invitation})})
        return {'content': content}
    except Exception as error:  # A failing tool must not end the server process.
        return {'isError': True, 'content': [{'type': 'text', 'text': str(error) or type(error).__name__}]}


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
    except Exception as error:
        body = {'error': {'code': -32603, 'message': str(error) or type(error).__name__}}
    return {'jsonrpc': '2.0', 'id': message_id, **body}


if __name__ == '__main__':
    for line in sys.stdin.buffer:
        response = respond(line)
        if response is not None:
            print(json.dumps(response), flush=True)
