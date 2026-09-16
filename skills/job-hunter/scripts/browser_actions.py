"""Guarded BOSS single-step browser entry. No bulk loads, arbitrary JS or API calls."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

import store
import boss_page
from browsing_safety import Safety
import webbridge_client
from policy_rules import browser_route

OPERATIONS = ('status', 'resume-context', 'inspect', 'start-query', 'control', 'capture-list', 'screen',
              'open-detail', 'review-detail', 'submit', 'reconcile', 'scroll',
              'dismiss-receipt', 'ack-quota-notice', 'clear-access-block', 'defer-detail', 'select-account-context', 'finish-list-read')


class Engine:
    def __init__(self, root: Path, token: str, platform: str, session: str, transport=None):
        if platform != 'boss':
            raise store.StoreError('platform-adapter-unsupported')
        self.safety = Safety(root, token, platform, session)
        self.transport = transport or (lambda action, args: webbridge_client.command_file(session, action, args))

    def _call(self, script: str) -> dict:
        result = self.transport('evaluate', {'code': script})
        if result.get('outcome') != 'returned':
            raise store.StoreError('browser-outcome-unknown:inspect-do-not-repeat')
        raw = result.get('result', {}).get('data', {})
        value = raw.get('value')
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, dict):
            raise store.StoreError('unsupported-browser-response')
        return value

    def _inspect(self) -> dict:
        # Fixed DOM-only script; does not scroll, click, navigate, fetch or preload.
        return self.safety.observe(self._call(boss_page.observation_script()))

    def execute(self, operation: str, data: dict | None = None) -> dict:
        data = data or {}
        if operation not in OPERATIONS or not isinstance(data, dict):
            raise store.StoreError('known-operation-and-object-required')
        if operation == 'status':
            return self.safety.status()
        policy = store.load_policy(self.safety.root)
        route = browser_route(policy)
        if operation == 'resume-context':
            status = self.safety.status()
            context = status.get('accountContext') or {}
            flow = status['flow']
            return {**status, 'browser': route, 'policyFingerprint': store.fingerprint(policy),
                    'session': context.get('session') or flow.get('session'),
                    'requiredReads': ['SKILL.md', 'references/drivers.md', 'kimi-webbridge/SKILL.md'],
                    'networkCalls': 0}
        if operation == 'inspect':
            return self._inspect()
        if operation in ('start-query', 'screen', 'review-detail', 'clear-access-block', 'defer-detail', 'select-account-context', 'finish-list-read'):
            return getattr(self.safety, operation.replace('-', '_'))(data)
        if operation == 'reconcile':
            return self._reconcile(data)
        if operation == 'submit':
            if set(data) != {'request'} or not isinstance(data['request'], dict):
                raise store.StoreError('single-outbox-request-required')
            # This adapter only clicks the platform-owned opener; it cannot
            # deliver a supplied draft. Reject before even inspecting a page.
            if data['request'].get('contentMode') != 'platform-default':
                raise store.StoreError('boss-adapter-supports-platform-default-only')
            store.validate_content_mode(data['request'])
        # This precedes even passive inspection: blocked heartbeats make zero browser calls.
        self.safety.preflight()
        self._inspect()
        self.safety.preflight()
        if operation == 'capture-list':
            return self.safety.capture_list()
        if operation == 'submit':
            return self._submit(data)
        allowed = {'control': {'id', 'value', 'interaction'}, 'open-detail': {'key'}, 'scroll': set(), 'dismiss-receipt': set(), 'ack-quota-notice': {'actionId'}}
        if set(data) - allowed[operation]:
            raise store.StoreError('unsupported-step-arguments')
        script = boss_page.action_script(operation, data)
        self.safety.reserve(operation, data)
        result = self._call(script)
        if result.get('status') == 'unsupported':
            self.safety.cancel_unsupported_step()
            return result
        if result.get('status') == 'hover-target':
            self.safety.preflight()
            moved = self.transport('cdp', {'method': 'Input.dispatchMouseEvent', 'params': {
                'type': 'mouseMoved', 'x': result['x'], 'y': result['y']}})
            if moved.get('outcome') != 'returned' or moved.get('result', {}).get('ok') is False:
                raise store.StoreError('browser-outcome-unknown:inspect-do-not-repeat')
            result = {**result, 'status': 'hovered'}
        observation = self._inspect()
        return {'step': result, 'observation': observation}

    def _submit(self, data: dict) -> dict:
        if set(data) != {'request'} or not isinstance(data['request'], dict):
            raise store.StoreError('single-outbox-request-required')
        request = data['request']
        policy = store.load_policy(self.safety.root)
        target = policy.get('search', {}).get('dailyTarget')
        if type(target) is int and target > 0:
            state = store.load_state(self.safety.root)
            day = store.local_now(policy).date().isoformat()
            confirmed = {a['targetKey'] for a in state['actions'].values() if a.get('date') == day
                         and a.get('kind') in ('greet', 'application') and a.get('status') == 'succeeded'}
            if len(confirmed) >= target:
                raise store.StoreError('daily-confirmed-target-reached')
        flow = self.safety.status()['flow']
        key = flow.get('activeKey')
        candidate = flow['candidates'].get(key, {})
        page = self.safety._last_page(flow)
        self.safety._verify(flow, page)
        if not key or candidate.get('decision') != 'apply' or flow['pending']:
            raise store.StoreError('reviewed-single-active-candidate-required')
        if candidate.get('reviewedProfileFingerprint') != flow.get('profileFingerprint') or (
                candidate.get('reviewedPolicyFingerprint') != flow.get('policyFingerprint')):
            raise store.StoreError('review-context-changed-before-submit')
        if request.get('kind') != 'greet' or request.get('platform') != 'boss' or request.get('targetKey') != key:
            raise store.StoreError('boss-adapter-supports-current-candidate-greet-only')
        if request.get('accountLabel') != flow['accountLabel']:
            raise store.StoreError('outbox-account-mismatch')
        if page.get('detail', {}).get('key') != key or page['detail'].get('text') != candidate['detail']['text']:
            raise store.StoreError('current-detail-changed-review-required')
        if '已向BOSS发送消息' in page.get('body', ''):
            raise store.StoreError('dismiss-previous-receipt-before-submit')
        request = {**request, 'accountContextId': flow.get('accountContextId'),
                   'browsingContext': {'batchId': flow['batch']['id'],
                   'listEvidence': candidate['listEvidence'], 'detailEvidence': candidate['detail']['evidence'],
                   'reviewEvidence': candidate['reviewEvidence'], 'profileFingerprint': flow['profileFingerprint'],
                   'submitObservation': flow['lastObservation']['path']}}
        action = store.begin(self.safety.root, self.safety.token, request)
        action_id = action['id']
        args = {'key': key, 'actionId': action_id}
        try:
            self.safety.reserve('submit', args)
            store.check_action(self.safety.root, self.safety.token, action_id)
        except (ValueError, OSError):
            store.resolve(self.safety.root, self.safety.token, action_id, 'failed', 'Pre-click guard rejected; browser submit was not invoked.')
            self.safety.settled_action(action_id)
            raise
        try:
            result = self._call(boss_page.action_script('submit', {'key': key}))
        except (ValueError, OSError):
            store.resolve(self.safety.root, self.safety.token, action_id, 'unknown', 'Browser submit result unavailable; do not resend.')
            self.safety.settled_action(action_id)
            return {'id': action_id, 'status': 'unknown'}
        if result.get('status') == 'unsupported':
            store.resolve(self.safety.root, self.safety.token, action_id, 'failed', 'Driver reported no click: ' + result.get('reason', 'unsupported'))
            self.safety.settled_action(action_id)
            return {'id': action_id, 'status': 'failed', 'step': result}
        return self._reconcile({'actionId': action_id})

    def _reconcile(self, data: dict) -> dict:
        action_id = store.required_string(data, 'actionId')
        state = store.load_state(self.safety.root)
        store.require_token(state, self.safety.token)
        action = state['actions'].get(action_id, {})
        if action.get('platform') != 'boss' or action.get('kind') != 'greet':
            raise store.StoreError('unsupported-reconciliation-action')
        if action.get('status') in ('succeeded', 'failed'):
            flow = self.safety.status()['flow']
            if self._action_belongs_to_flow(flow, action):
                self.safety.settled_action(action_id)
            return {'id': action_id, 'status': action['status'], 'unchanged': True}
        status, evidence = 'unknown', 'Passive receipt inspection unavailable; do not resend.'
        try:
            observed = self._inspect()
            page = observed['page']
            # A button changing to "continue chatting" alone is not a delivery receipt.
            context = store.active_account_context(store.load_state(self.safety.root), 'boss')
            context_matches = not context or context['id'] == action.get('accountContextId')
            platform_default = action.get('contentMode') == 'platform-default'
            # This adapter does not extract identity-bound message bubbles.
            # Text appearing elsewhere on the page cannot verify a legacy draft.
            content_matches = False
            if platform_default:
                # The platform receipt proves delivery, not the undisplayed opener text.
                # Require this action's saved pre-click page to exclude an old receipt.
                content_matches = self._default_receipt_baseline_matches(action, observed['evidence'])
            if (context_matches and page.get('accountLabel') == action.get('accountLabel')
                    and (page.get('detail') or {}).get('key') == action['targetKey']
                    and '已向BOSS发送消息' in page.get('body', '')
                    and content_matches):
                status = 'succeeded'
            evidence = observed['evidence'] + ': matching receipt' if status == 'succeeded' else observed['evidence'] + ': receipt unconfirmed; no resend'
            if not platform_default:
                evidence += '; custom text requires message-level verification unsupported by this adapter'
            if status == 'succeeded' and platform_default:
                evidence += '; platform-default opener text unobserved'
        except (ValueError, OSError):
            pass
        store.resolve(self.safety.root, self.safety.token, action_id, status, evidence)
        flow = self.safety.status()['flow']
        if self._action_belongs_to_flow(flow, action):
            self.safety.settled_action(action_id)
        return {'id': action_id, 'status': status, 'evidence': evidence}

    def _default_receipt_baseline_matches(self, action: dict, observed: str) -> bool:
        before = action.get('browsingContext', {}).get('submitObservation')
        if not isinstance(before, str) or before == observed:
            return False
        path = (self.safety.root / before).resolve()
        if path.parent != (self.safety.root / 'logs' / 'browsing').resolve() or path.suffix != '.json':
            return False
        saved = json.loads(path.read_text(encoding='utf-8'))
        page = saved.get('page', {})
        return (saved.get('classification') == 'normal'
                and page.get('accountLabel') == action.get('accountLabel')
                and (page.get('detail') or {}).get('key') == action['targetKey']
                and '已向BOSS发送消息' not in page.get('body', ''))

    @staticmethod
    def _action_belongs_to_flow(flow: dict, action: dict) -> bool:
        action_id, key = action['id'], action['targetKey']
        pending = flow.get('pending') or {}
        candidate = flow['candidates'].get(key, {})
        context = action.get('browsingContext', {})
        return (pending.get('args', {}).get('actionId') == action_id or candidate.get('actionId') == action_id
                or (not pending and flow['activeKey'] == key and context.get('batchId') == (flow.get('batch') or {}).get('id')
                    and context.get('listEvidence') == candidate.get('listEvidence')
                    and context.get('detailEvidence') == candidate.get('detail', {}).get('evidence')))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('JOB_HUNTER_HOME') or '~/.job-hunter'))
    parser.add_argument('--token', default='')
    parser.add_argument('--platform', default='boss')
    parser.add_argument('--session', default='')
    parser.add_argument('--operation', choices=OPERATIONS, required=True)
    parser.add_argument('--file', type=Path)
    args = parser.parse_args()
    try:
        if args.operation not in ('status', 'resume-context') and (not args.token or not args.session):
            raise store.StoreError('run-token-and-browser-session-required')
        engine = Engine(args.data_dir.expanduser(), args.token, args.platform, args.session)
        result = engine.execute(args.operation, store.read_json(args.file) if args.file else {})
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError) as error:
        print(json.dumps({'error': str(error), 'noAutomaticRetry': True}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
