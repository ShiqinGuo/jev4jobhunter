"""Mobile CLI composition. UI, persistence and delivery use separate dependencies."""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import traceback
import uuid
from android_application import MobileApplication
from android_device import Device
from android_domain import Binding, MobileError, digest
from android_receipts import ProxySession, ReceiptFeed
from android_repository import MobileRepository, migrate_mobile
from android_ui import NativeUI
import store

OPERATIONS = ('migrate', 'inspect', 'start', 'stop', 'preview', 'run', 'evaluate-batch',
              'apply-batch', 'reconcile-batch', 'message-check', 'open-conversation', 'conversation', 'reply-current', 'report')

def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--data-dir', type=Path, default=Path(os.environ.get('JOB_HUNTER_HOME', '~/.job-hunter')))
    result.add_argument('--config', type=Path)
    result.add_argument('--output-dir', type=Path, help='Session evidence outside the repository.')
    result.add_argument('--source', choices=('search', 'recommendation'))
    result.add_argument('--city')
    result.add_argument('--query')
    result.add_argument('--count', type=int, default=20)
    result.add_argument('--send', action='store_true')
    result.add_argument('--max-send', type=int)
    result.add_argument('--fresh', action='store_true', help='Archive this batch and acquire a new natural batch.')
    result.add_argument('--exclude-file', type=Path)
    result.add_argument('--request-file', type=Path)
    result.add_argument('--target-key')
    result.add_argument('operation', choices=OPERATIONS)
    return result

def execute(args):
    root = args.data_dir.expanduser().resolve()
    if args.operation == 'report':
        return store.report(root)
    if args.operation != 'migrate' and not args.config:
        raise MobileError('--config-required')
    if args.operation in ('run', 'preview'):
        if not all((args.source, args.city, args.query)) or not 1 <= args.count <= 100:
            raise MobileError('run-plan-required:source-city-query-count1..100')
        if args.operation == 'preview' and args.send:
            raise MobileError('preview-cannot-send')
        if args.operation == 'run' and (not args.send or args.max_send is None or args.max_send < 1):
            raise MobileError('run-requires-send-and-positive-max-send')
    if args.max_send is not None and (args.max_send < 1 or not args.send or args.operation not in ('run', 'apply-batch')):
        raise MobileError('max-send-requires-submission-operation')
    if args.operation == 'reply-current' and not args.request_file:
        raise MobileError('reply-request-file-required')
    output = args.output_dir or Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'outputs' / 'job-hunter-mobile'
    output.mkdir(parents=True, exist_ok=True)
    lock = store.run_lock(root, 'acquire')
    try:
        if args.operation == 'migrate':
            return migrate_mobile(root, lock['token'])
        repository = MobileRepository(root, lock['token'])
        device = Device(store.read_json(args.config))
        feed = ReceiptFeed(root / 'android' / 'responses')
        proxy = ProxySession(device, feed.directory)
        navigator = NativeUI(device, feed, output / ('ui-' + uuid.uuid4().hex))
        if args.operation == 'stop':
            return proxy.stop()
        if args.operation == 'start':
            proxy.start(watch_owner=False)
            return {'status': 'capturing'}
        if args.operation == 'inspect':
            runtime = device.observe_runtime()
            return {'runtime': asdict(runtime), 'labels': [n.get('text') for n in device.ui().iter('node') if n.get('text')]}
        application = MobileApplication(repository, device, navigator, feed)
        if args.operation == 'evaluate-batch':
            return application.evaluate()
        if args.operation == 'message-check':
            return application.check_messages()
        if args.operation == 'open-conversation':
            if not args.target_key:
                raise MobileError('open-conversation-requires-target-key')
            return application.open_conversation(args.target_key)
        if args.operation in ('conversation', 'reply-current'):
            snapshot = navigator.conversation()
            if args.operation == 'conversation':
                return {**snapshot, 'digest': digest(snapshot)}
            return application.reply_current(store.read_json(args.request_file), args.send)
        with proxy.opened():
            device.restart()
            if args.operation in ('run', 'preview'):
                plan = {'source': args.source, 'city': args.city, 'query': args.query, 'count': args.count}
                excluded = store.read_json(args.exclude_file)['keys'] if args.exclude_file else []
                return application.run(plan, args.operation == 'run', args.max_send, args.fresh, excluded)
            application.prepare_search(application.batch['plan'])
            application.filter_pool(store.load_policy(root), None)
            if args.operation == 'apply-batch':
                return application.apply(args.send, args.max_send)
            if args.operation == 'reconcile-batch':
                actions = repository.actions(Binding(**application.batch['binding']))
                # Receipt reconciliation does not navigate to or click a send button.
                results = [application.reconcile(a) for a in actions if a['status'] in ('pending', 'unknown')]
                return {'status': 'reconciled', 'actions': [{'id': a['id'], 'status': a['status']} for a in results]}
            raise MobileError('unsupported-operation')
    finally:
        store.run_lock(root, 'release', lock['token'])

def main():
    args = parser().parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        result = execute(args)
    except (MobileError, store.StoreError, OSError) as exc:
        # Emit actionable classification and retain the full cause in stderr.
        traceback.print_exc()
        print(json.dumps({'status': 'blocked', 'reason': str(exc), 'type': type(exc).__name__}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
