#!/usr/bin/env python3
"""Standard-library-only client for the local Godot REPL protocol."""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import time
import uuid


class ReplError(Exception):
    pass


def instances(project, channel):
    root = Path(project).resolve() / '.godot' / 'godot_repl' / channel
    result = []
    for path in root.glob('*/instance.json'):
        try:
            data = json.loads(path.read_text())
            os.kill(int(data['pid']), 0)
            if data['protocol'] == 1 and data['channel'] == channel:
                result.append((path.parent, data))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result


def select_instance(project, channel, instance=None):
    found = instances(project, channel)
    if instance:
        found = [(p, d) for p, d in found if d['instance'] == instance]
    if len(found) != 1:
        raise ReplError(f'Expected one {channel} instance; found {len(found)}. Use list and --instance.')
    return found[0]


def atomic_json(path, data):
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
    if len(payload) > 262144:
        raise ReplError('Request exceeds 256 KiB')
    temp = path.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def execute(project, channel='runtime', code='', mode='block', session='default',
            timeout=10.0, instance=None, op='eval'):
    if not math.isfinite(timeout) or not 0.001 <= timeout <= 300:
        raise ReplError('Timeout must be between 0.001 and 300 seconds')
    directory, descriptor = select_instance(project, channel, instance)
    request_id = uuid.uuid4().hex
    request = directory / 'requests' / f'{request_id}.json'
    response = directory / 'responses' / f'{request_id}.json'
    cancel = directory / 'requests' / f'{request_id}.cancel'
    atomic_json(request, {'protocol': 1, 'id': request_id, 'op': op, 'code': code,
                         'mode': mode, 'session': session, 'timeout_ms': int(timeout * 1000),
                         'expires_at': time.time() + timeout + 3})
    deadline = time.monotonic() + timeout + 3
    try:
        while time.monotonic() < deadline:
            if response.exists():
                data = json.loads(response.read_text())
                response.unlink()
                if data.get('id') != request_id or data.get('instance') != descriptor['instance']:
                    raise ReplError('Response identity mismatch')
                return data
            try:
                os.kill(int(descriptor['pid']), 0)
            except OSError as error:
                raise ReplError('Godot process exited before responding') from error
            time.sleep(0.02)
        cancel.touch(mode=0o600)
        raise ReplError('No response; cancellation requested. Godot may be blocked or paused. No hard termination occurred.')
    except KeyboardInterrupt:
        cancel.touch(mode=0o600)
        raise ReplError('Cancellation requested; running code may still need to yield')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', default='.')
    parser.add_argument('--channel', choices=['editor', 'runtime'], default='runtime')
    parser.add_argument('--instance')
    parser.add_argument('--session', default='default')
    parser.add_argument('--timeout', type=float, default=10)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    sub.add_parser('reset')
    sub.add_parser('shell', help='Interactive expressions; :paste for code blocks')
    evaluation = sub.add_parser('eval')
    source = evaluation.add_mutually_exclusive_group(required=True)
    source.add_argument('-e', '--expression')
    source.add_argument('-c', '--code')
    source.add_argument('-f', '--file', help='Code-block file; - reads stdin')
    source.add_argument('-s', '--script-file', help='Full @tool RefCounted script with run(ctx)')
    args = parser.parse_args()
    try:
        if args.command == 'list':
            print(json.dumps([d for _, d in instances(args.project, args.channel)], ensure_ascii=False, indent=2))
            return 0
        if args.command == 'shell':
            while True:
                try:
                    text = input('godot> ')
                except (EOFError, KeyboardInterrupt):
                    print()
                    return 0
                if text == ':quit':
                    return 0
                operation, mode = 'eval', 'expr'
                if text == ':reset':
                    operation = 'reset'
                elif text == ':paste':
                    print('Enter a code block; finish with a line containing :end')
                    lines = []
                    try:
                        while True:
                            line = input('... ')
                            if line == ':end':
                                break
                            lines.append(line)
                    except (EOFError, KeyboardInterrupt):
                        print()
                        continue
                    text, mode = '\n'.join(lines), 'block'
                if not text.strip():
                    continue
                try:
                    result = execute(args.project, args.channel, text, mode, args.session,
                                     args.timeout, args.instance, operation)
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                except ReplError as error:
                    print(str(error), file=sys.stderr)
            return 0
        code, mode = '', 'block'
        if args.command == 'eval':
            if args.expression is not None:
                code, mode = args.expression, 'expr'
            elif args.code is not None:
                code = args.code
            elif args.script_file is not None:
                code, mode = Path(args.script_file).read_text(), 'script'
            else:
                code = sys.stdin.read() if args.file == '-' else Path(args.file).read_text()
        result = execute(args.project, args.channel, code, mode, args.session,
                         args.timeout, args.instance, 'reset' if args.command == 'reset' else 'eval')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['status'] == 'OK' else 1
    except (ReplError, OSError, ValueError) as error:
        print(json.dumps({'status': 'CLIENT_ERROR', 'message': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
