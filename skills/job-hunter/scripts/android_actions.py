"""UI-driven Android batches: collect JDs, one Jev decision call, serial outbox submission."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
import uuid

from android_device import Device, PACKAGE
from browsing_safety import require_access
import jev
import store
from policy_rules import check_target


class Workflow:
    def __init__(self, root, device, token):
        self.root, self.device, self.token = Path(root), device, token
        self.path = self.root / 'android' / 'batch.json'
        self.batch = store.read_json(self.path) if self.path.exists() else {}

    def save(self):
        store.require_token(store.load_state(self.root), self.token)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        store.write_json(self.path, self.batch)

    def check(self):
        state = store.load_state(self.root)
        store.require_token(state, self.token)
        require_access(state, 'boss')
        context = store.active_account_context(state, 'boss')
        if context.get('id') != self.batch.get('accountContextId') or (context and context['accountLabel'] != self.batch['accountLabel']):
            raise store.StoreError('android-account-context-changed')
        if self.batch.get('signature') != self.device.signature():
            raise store.StoreError('android-device-or-layout-changed:bind-account-again')
        if self.batch.get('policyFingerprint') != store.fingerprint(store.load_policy(self.root)) or self.batch.get('profileFingerprint') != store.profile_fingerprint(self.root):
            raise store.StoreError('android-batch-context-changed')
        return state

    def bind(self):
        """Run on BOSS's My page, then return to jobs using the UI."""
        if any(a.get('status') == 'pending' for a in store.load_state(self.root)['actions'].values()):
            raise store.StoreError('pending-outbox-must-be-reconciled-first')
        policy = store.load_policy(self.root)
        account = next(p.get('accountLabel') for p in policy['platforms'] if p['id'] == 'boss')
        nodes = self.device.ui()
        if not account or not any(node.get('text') == account for node in nodes.iter('node')):
            raise store.StoreError('open-my-page-to-verify-account')
        self.batch = {'contextId': uuid.uuid4().hex, 'accountLabel': account,
                      'accountContextId': store.active_account_context(store.load_state(self.root), 'boss').get('id'),
                      'signature': self.device.signature(), 'policyFingerprint': store.fingerprint(policy),
                      'profileFingerprint': store.profile_fingerprint(self.root), 'jobs': {}, 'decisions': {}, 'results': {}}
        self.save()
        return {'status': 'bound', 'accountLabel': account}

    def arm_search(self):
        self.check()
        marker = self.device.mark(self.batch['contextId'], 'search')
        self.batch['searchAction'] = marker
        self.save()
        return {'status': 'armed', 'nextAction': 'use-search-ui-then-read-batch'}

    def accept_list(self, event):
        self.check()
        if event['contextId'] != self.batch['contextId'] or event['type'] != 'list':
            raise store.StoreError('matching-list-response-required')
        added = []
        for job in event['jobs']:
            if job['key'] not in self.batch['jobs']:
                self.batch['jobs'][job['key']] = {**job, 'listEvidence': event['id']}
                added.append(job['key'])
        self.batch.update(lastList=event['id'], hasMore=event['hasMore'])
        self.save()
        return {'status': 'captured', 'added': len(added), 'total': len(self.batch['jobs']), 'keys': added}

    def read_batch(self):
        event = self.device.wait(self.batch['searchAction'], 'list')
        if not event:
            raise store.StoreError('list-response-missing:not-an-empty-list')
        return self.accept_list(event)

    def scroll_profile(self):
        path = self.root / 'android' / 'scroll.json'
        profile = store.read_json(path) if path.exists() else None
        if not profile or profile['signature'] != self.device.signature():
            raise store.StoreError('calibrate-scroll-for-current-device-first')
        return profile

    def scroll_next(self, profile=None):
        self.check()
        profile = profile or self.scroll_profile()
        if self.batch.get('hasMore') is False:
            return {'status': 'end', 'swipes': 0}
        for count in range(1, profile['maxSwipes'] + 1):
            tree = self.device.ui()
            if not self.device.cards(tree):
                raise store.StoreError('search-list-required-before-scroll')
            marker = self.device.mark(self.batch['contextId'], 'scroll')
            self.device.swipe(profile)
            event = self.device.wait(marker, 'list', seconds=2)
            if event:
                return {**self.accept_list(event), 'swipes': count}
        return {'status': 'no-new-response', 'swipes': profile['maxSwipes'], 'nextAction': 'inspect-list-not-exhausted'}

    def calibrate(self):
        signature = self.device.signature()
        width, height = signature['size']
        profile = {'signature': signature, 'x': round(width * .5), 'startY': round(height * .8875),
                   'endY': round(height * .26875), 'durationMs': 650, 'maxSwipes': 8}
        measurements = []
        for _ in range(2):
            result = self.scroll_next(profile)
            if result['status'] != 'captured' or result['added'] == 0:
                raise store.StoreError('scroll-calibration-needs-two-new-response-batches')
            measurements.append(result['swipes'])
        profile.update(maxSwipes=max(measurements) + 2, measuredSwipes=measurements)
        store.write_json(self.root / 'android' / 'scroll.json', profile)
        return {'status': 'calibrated', **profile}

    def open_job(self, job):
        self.check()
        profile = dict(self.scroll_profile())
        # Overlap cards while locating a target; loading keeps the calibrated full gesture.
        profile['endY'] = (profile['startY'] + profile['endY']) // 2
        # Bound searches within the accumulated list, first upwards then downwards.
        budget = max(4, len(self.batch['jobs']) // 2 + 2)
        first_direction = getattr(self, 'reverse', True)
        for reverse in (first_direction, not first_direction):
            previous = None
            for _ in range(budget):
                cards = self.device.cards()
                matches = [card for card in cards if card['title'] == job['title'] and card['company'] == job['company']]
                if len(matches) == 1:
                    marker = self.device.mark(self.batch['contextId'], 'open-detail', job['key'])
                    self.device.tap(matches[0]['node'])
                    event = self.device.wait(marker, 'detail')
                    if not event or event['job']['key'] != job['key'] or not event['job']['complete']:
                        raise store.StoreError('matching-full-jd-response-required')
                    self.reverse = False
                    return {**event['job'], 'detailEvidence': event['id']}
                if len(matches) > 1:
                    raise store.StoreError('ambiguous-visible-job')
                visible = [(row['title'], row['company'], row['node'].get('bounds')) for row in cards]
                if visible == previous:
                    break
                previous = visible
                self.device.mark(self.batch['contextId'], 'locate-job', job['key'])
                self.device.swipe(profile, reverse=reverse)
        raise store.StoreError('job-not-found-in-current-list')

    def collect(self):
        state = self.check()
        policy = store.load_policy(self.root)
        for key, job in self.batch['jobs'].items():
            if job.get('complete') or key in self.batch['decisions']:
                continue
            if any(a.get('targetKey') == key and a.get('status') in store.HELD for a in state['actions'].values()):
                self.batch['decisions'][key] = {'key': key, 'apply': False, 'reason': 'already-held'}
                self.save()
                continue
            try:
                check_target(policy, state, {'kind': 'greet', 'targetKey': key, 'targetFacts': job}, require_complete=False)
            except store.StoreError as error:
                self.batch['decisions'][key] = {'key': key, 'apply': False, 'reason': str(error)}
                self.save()
                continue
            detail = self.open_job(job)
            self.batch['jobs'][key].update(detail)
            self.save()
            self.device.back()
        return {'status': 'collected', 'complete': sum(job['complete'] for job in self.batch['jobs'].values()), 'nextAction': 'evaluate-batch'}

    def evaluate(self):
        self.check()
        pending = [job for key, job in self.batch['jobs'].items() if key not in self.batch['decisions']]
        if not pending:
            return {'status': 'already-evaluated', 'providerCalls': 0}
        result = jev.evaluate_jobs(self.root, pending)
        self.check()
        evidence = self.root / 'android' / ('jev-' + uuid.uuid4().hex + '.json')
        store.write_json(evidence, result)
        self.batch['decisions'].update({row['key']: {**row, 'evidence': str(evidence)} for row in result['judgments']})
        self.save()
        return {'status': 'evaluated', 'apply': sum(row['apply'] for row in result['judgments']),
                'count': len(pending), 'providerCalls': 1, 'elapsedMs': result['elapsedMs'], 'usage': result['usage']}

    def apply(self, dry=True):
        self.check()
        if any(value == 'unknown' for value in self.batch['results'].values()):
            return {'status': 'unknown', 'nextAction': 'reconcile-without-resend'}
        selected = [key for key, row in self.batch['decisions'].items() if row['apply'] and key not in self.batch['results']]
        if dry:
            return {'status': 'dry', 'keys': selected, 'count': len(selected)}
        for key in selected:
            state = self.check()
            job = self.batch['jobs'][key]
            if any(action.get('status') in store.HELD and action.get('targetKey') == key for action in state['actions'].values()):
                self.batch['results'][key] = 'already-held'
                self.save()
                continue
            current = self.open_job(job)
            if current['text'] != job['text']:
                self.batch['jobs'][key].update(current)
                self.batch['decisions'].pop(key)
                self.save()
                self.device.back()
                return {'status': 'jd-changed', 'nextAction': 'evaluate-batch'}
            tree = self.device.ui()
            if any(node.get('text') == '继续沟通' for node in tree.iter('node')):
                self.batch['results'][key] = 'already-contacted'
                self.save()
                self.device.back()
                continue
            button = self.device.button('立即沟通', tree)
            context = store.active_account_context(state, 'boss')
            request = {'kind': 'greet', 'platform': 'boss', 'targetKey': key,
                       'accountLabel': self.batch['accountLabel'], 'accountContextId': context.get('id'),
                       'contentMode': 'platform-default', 'targetFacts': {**job, 'evidence': current['detailEvidence']},
                       'context': 'Android full JD batch boolean decision; policy review continues in conversation.',
                       'authorizationEvidence': store.load_policy(self.root)['authorization'].get('evidence', ''),
                       'browsingContext': {'profileFingerprint': self.batch['profileFingerprint']}}
            action = store.begin(self.root, self.token, request)
            marker = self.device.mark(self.batch['contextId'], 'greet', key)
            try:
                store.check_action(self.root, self.token, action['id'])
            except (ValueError, OSError):
                store.resolve(self.root, self.token, action['id'], 'failed', 'Pre-click check rejected; no tap.')
                raise
            # Persisted pending exists before the sole click; ambiguous results are never replayed.
            try:
                self.device.tap(button)
                receipt = self.device.wait(marker, 'receipt')
                status = 'succeeded' if receipt and receipt['code'] == 0 and receipt['httpStatus'] == 200 else 'unknown'
            except (ValueError, OSError):
                receipt, status = None, 'unknown'
            store.resolve(self.root, self.token, action['id'], status,
                          str(self.device.directory / (receipt['id'] + '.json')) if receipt else 'No matching UI-triggered response; do not resend.')
            self.batch['results'][key] = status
            self.save()
            if status != 'succeeded':
                return {'status': status, 'key': key, 'nextAction': 'reconcile-without-resend'}
            self.device.return_to_list()
        return {'status': 'finished', 'results': self.batch['results']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('JOB_HUNTER_HOME', '~/.job-hunter')))
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--send', action='store_true', help='Use the existing authorization and outbox to greet selected jobs.')
    parser.add_argument('operation', choices=('start', 'stop', 'inspect', 'bind-account', 'arm-search', 'read-batch', 'calibrate-scroll', 'scroll-next', 'collect-details', 'evaluate-batch', 'apply-batch'))
    args = parser.parse_args()
    root = args.data_dir.expanduser().resolve()
    device = Device(store.read_json(args.config), root / 'android' / 'responses')
    if args.operation in ('start', 'stop'):
        return device.capture(stop=args.operation == 'stop')
    if args.operation == 'inspect':
        return {'signature': device.signature(), 'cards': [{k: row[k] for k in ('title', 'company')} for row in device.cards()]}
    lock = store.run_lock(root, 'acquire')
    try:
        workflow = Workflow(root, device, lock['token'])
        operations = {'bind-account': workflow.bind, 'arm-search': workflow.arm_search, 'read-batch': workflow.read_batch,
                      'calibrate-scroll': workflow.calibrate, 'scroll-next': workflow.scroll_next,
                      'collect-details': workflow.collect, 'evaluate-batch': workflow.evaluate,
                      'apply-batch': lambda: workflow.apply(dry=not args.send)}
        return operations[args.operation]()
    finally:
        store.run_lock(root, 'release', lock['token'])


if __name__ == '__main__':
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        print(json.dumps(main(), ensure_ascii=False))
    except (ValueError, OSError, KeyError, StopIteration) as error:
        print(json.dumps({'status': 'blocked', 'reason': str(error)}, ensure_ascii=False))
        raise SystemExit(1)
