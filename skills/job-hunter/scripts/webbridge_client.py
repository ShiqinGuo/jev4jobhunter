"""UTF-8 transport for the selected Kimi WebBridge session. No automatic retries."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from urllib.error import URLError
from urllib.request import Request, urlopen

READ_ACTIONS = {'snapshot', 'list_tabs', 'screenshot'}
ACTIONS = READ_ACTIONS | {'navigate', 'find_tab', 'click', 'fill', 'evaluate', 'cdp',
                          'upload', 'close_tab', 'close_session', 'save_as_pdf'}


def command(session: str, action: str, args: dict, timeout: float = 35) -> dict:
    if not isinstance(session, str) or not session.strip() or action not in ACTIONS or not isinstance(args, dict):
        raise ValueError('explicit-session-known-action-and-object-args-required')
    if timeout <= 0 or timeout > 60:
        raise ValueError('timeout-must-be-between-0-and-60')
    body = json.dumps({'session': session, 'action': action, 'args': args}, ensure_ascii=False).encode('utf-8')
    request = Request('http://127.0.0.1:10086/command', data=body,
                      headers={'Content-Type': 'application/json; charset=utf-8'}, method='POST')
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode('utf-8'))
        if not isinstance(result, dict):
            raise ValueError('invalid-response')
        if result.get('ok') is not True:
            return {'outcome': 'read-error' if action in READ_ACTIONS else 'unknown', 'result': result,
                    'retrySafe': action in READ_ACTIONS}
        return {'outcome': 'returned', 'result': result, 'submissionVerified': False}
    except (OSError, URLError, ValueError):
        return {'outcome': 'read-error' if action in READ_ACTIONS else 'unknown',
                'retrySafe': action in READ_ACTIONS, 'error': 'transport-or-response-error'}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--session', required=True)
    p.add_argument('--action', choices=sorted(ACTIONS), required=True)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--args-file', type=Path)
    source.add_argument('--args-stdin', action='store_true')
    a = p.parse_args()
    try:
        args = json.loads(a.args_file.read_text(encoding='utf-8-sig') if a.args_file else sys.stdin.read())
        result = command(a.session, a.action, args)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['outcome'] == 'returned' else 1
    except (OSError, ValueError) as error:
        print(json.dumps({'error': type(error).__name__}))
        return 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
