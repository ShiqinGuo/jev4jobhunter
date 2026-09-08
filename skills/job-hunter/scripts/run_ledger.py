"""Durable scheduler run records. Does not register tasks or perform browser actions."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sys

import store

MODES = ('search-apply', 'reply-check', 'daily-report')


def run_key(mode: str, day: str, slot: str) -> str:
    if mode not in MODES or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
        raise store.StoreError('invalid-run-mode-or-date')
    datetime.strptime(day, '%Y-%m-%d')
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', slot):
        raise store.StoreError('invalid-run-slot')
    return f'run:{mode}:{day}:{slot}'


def start(root: Path, token: str, mode: str, day: str, slot: str) -> dict:
    key = run_key(mode, day, slot)
    with store.transaction(root):
        state = store.load_state(root)
        store.require_token(state, token)
        old = state['scheduler'].get(key, {})
        if old.get('status') == 'completed':
            return {'key': key, 'alreadyCompleted': True,
                    'deliveryPending': mode == 'daily-report' and not old.get('deliveredAt')}
        if old.get('status') == 'running' and old.get('ownerToken') == token:
            return {'key': key, 'resumed': True, 'attempt': old['attempt']}
        policy = store.load_policy(root)
        entry = {**old, 'mode': mode, 'date': day, 'slot': slot, 'status': 'running',
                 'ownerToken': token, 'attempt': old.get('attempt', 0) + 1,
                 'startedAt': store.stamp(), 'policyFingerprint': store.fingerprint(policy),
                 'events': [*old.get('events', []), {'at': store.stamp(), 'status': 'running'}]}
        state['scheduler'][key] = entry
        state['runLock']['heartbeatAt'] = store.stamp()
        store.write_json(root / 'state.json', state)
        return {'key': key, 'resumed': bool(old), 'attempt': entry['attempt'],
                'unresolved': [a['id'] for a in state['actions'].values()
                               if a['status'] in ('pending', 'unknown')]}


def artifact_info(root: Path, artifact: str) -> dict:
    path = (root / artifact).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file() or path.stat().st_size == 0:
        raise store.StoreError('report-artifact-must-exist-inside-data-dir')
    return {'path': path.relative_to(root.resolve()).as_posix(),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def finish(root: Path, token: str, key: str, status: str, evidence: str, artifact: str = '') -> dict:
    if status not in ('completed', 'blocked', 'failed') or not evidence.strip():
        raise store.StoreError('run-status-and-evidence-required')
    with store.transaction(root):
        state = store.load_state(root)
        store.require_token(state, token)
        entry = state['scheduler'].get(key, {})
        if not key.startswith('run:') or entry.get('status') != 'running' or entry.get('ownerToken') != token:
            raise store.StoreError('run-not-owned-or-not-running')
        if entry['mode'] == 'daily-report' and status == 'completed':
            entry['artifact'] = artifact_info(root, artifact)
        entry.update(status=status, finishedAt=store.stamp(), evidence=evidence)
        entry['events'].append({'at': store.stamp(), 'status': status, 'evidence': evidence})
        state['runLock']['heartbeatAt'] = store.stamp()
        store.write_json(root / 'state.json', state)
        return {'key': key, 'status': status}


def delivered(root: Path, token: str, key: str, receipt: str) -> dict:
    if not receipt.strip():
        raise store.StoreError('delivery-receipt-required')
    with store.transaction(root):
        state = store.load_state(root)
        store.require_token(state, token)
        entry = state['scheduler'].get(key, {})
        if entry.get('mode') != 'daily-report' or entry.get('status') != 'completed':
            raise store.StoreError('report-not-completed')
        if entry.get('deliveredAt'):
            return {'key': key, 'unchanged': True}
        if artifact_info(root, entry['artifact']['path']) != entry['artifact']:
            raise store.StoreError('report-artifact-changed')
        entry.update(deliveredAt=store.stamp(), deliveryReceipt=receipt)
        state['runLock']['heartbeatAt'] = store.stamp()
        store.write_json(root / 'state.json', state)
        return {'key': key, 'delivered': True}


def due(root: Path) -> dict:
    """Coalesce overdue replies; preserve incomplete searches and undelivered reports."""
    policy = store.load_policy(root)
    state = store.load_state(root)
    moment = store.local_now(policy)
    schedule = policy.get('schedule', {})
    result = {}
    if not schedule.get('enabled'):
        return {'enabled': False, 'due': []}
    for window in schedule.get('windows', []):
        name = window.get('name')
        if name not in MODES:
            raise store.StoreError('unsupported-schedule-window')
        if moment.isoweekday() not in window.get('weekdays', policy.get('weekdays', range(1, 8))):
            continue
        slot = window.get('time', window.get('start', ''))
        key = run_key(name, moment.date().isoformat(), slot)
        scheduled = moment.replace(hour=int(slot[:2]), minute=int(slot[3:]), second=0, microsecond=0)
        if scheduled > moment:
            continue
        if name == 'reply-check':
            interval = window.get('intervalMinutes', 30)
            if type(interval) is not int or interval < 1:
                raise store.StoreError('invalid-reply-interval')
            end = window.get('end', slot)
            run_key(name, moment.date().isoformat(), end)
            end_at = scheduled.replace(hour=int(end[:2]), minute=int(end[3:]))
            if end_at < scheduled:
                raise store.StoreError('overnight-reply-window:split-at-midnight')
            if window.get('endExclusive', False):
                end_at -= timedelta(microseconds=1)
            if moment > end_at + timedelta(minutes=interval):
                continue
            count = int((min(moment, end_at) - scheduled).total_seconds() // (60 * interval))
            if count < 0:
                continue
            scheduled += timedelta(minutes=count * interval)
            slot = scheduled.strftime('%H:%M')
            key = run_key(name, moment.date().isoformat(), slot)
        entry = state['scheduler'].get(key, {})
        if entry.get('status') == 'completed':
            if name == 'daily-report' and not entry.get('deliveredAt'):
                result[key] = {'key': key, 'mode': name, 'operation': 'deliver', 'artifact': entry.get('artifact')}
            continue
        result[key] = {'key': key, 'mode': name, 'date': moment.date().isoformat(), 'slot': slot,
                       'operation': 'resume' if entry else 'start', 'previousStatus': entry.get('status')}
    # Older reports with a ledger entry remain visible after midnight. Never replay old reply slots.
    for key, entry in state['scheduler'].items():
        if key.startswith('run:') and entry.get('mode') == 'daily-report' and not entry.get('deliveredAt'):
            result[key] = {'key': key, 'mode': 'daily-report', 'date': entry['date'], 'slot': entry['slot'],
                           'operation': 'deliver' if entry.get('status') == 'completed' else 'resume',
                           'artifact': entry.get('artifact')}
    # startDate prevents inventing runs before the user enabled the schedule.
    if schedule.get('startDate'):
        first_day = date.fromisoformat(schedule['startDate'])
        days = schedule.get('catchUpDays', 7)
        if type(days) is not int or not 1 <= days <= 30:
            raise store.StoreError('catch-up-days-must-be-1-to-30')
        day = max(first_day, moment.date() - timedelta(days=days - 1))
        while day < moment.date():
            for window in schedule.get('windows', []):
                if window.get('name') != 'daily-report' or day.isoweekday() not in window.get('weekdays', range(1, 8)):
                    continue
                slot = window.get('time', window.get('start', ''))
                key = run_key('daily-report', day.isoformat(), slot)
                entry = state['scheduler'].get(key, {})
                if not entry.get('deliveredAt'):
                    result[key] = {'key': key, 'mode': 'daily-report', 'date': day.isoformat(), 'slot': slot,
                                   'operation': 'deliver' if entry.get('status') == 'completed' else 'resume',
                                   'artifact': entry.get('artifact')}
            day += timedelta(days=1)
    return {'enabled': True, 'checkedAt': moment.isoformat(), 'due': list(result.values()),
            'runBusy': bool(state.get('runLock'))}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir', type=Path, default=Path(os.environ.get('JOB_HUNTER_HOME') or '~/.job-hunter'))
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('due')
    start_p = sub.add_parser('start')
    start_p.add_argument('--token', required=True)
    start_p.add_argument('--mode', choices=MODES, required=True)
    start_p.add_argument('--date', required=True)
    start_p.add_argument('--slot', required=True)
    finish_p = sub.add_parser('finish')
    finish_p.add_argument('--token', required=True)
    finish_p.add_argument('--key', required=True)
    finish_p.add_argument('--status', choices=('completed', 'blocked', 'failed'), required=True)
    finish_p.add_argument('--evidence', required=True)
    finish_p.add_argument('--artifact', default='')
    deliver_p = sub.add_parser('delivered')
    deliver_p.add_argument('--token', required=True)
    deliver_p.add_argument('--key', required=True)
    deliver_p.add_argument('--receipt', required=True)
    a = p.parse_args()
    root = a.data_dir.expanduser().resolve()
    try:
        if a.command == 'due':
            result = due(root)
        elif a.command == 'start':
            result = start(root, a.token, a.mode, a.date, a.slot)
        elif a.command == 'finish':
            result = finish(root, a.token, a.key, a.status, a.evidence, a.artifact)
        else:
            result = delivered(root, a.token, a.key, a.receipt)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
