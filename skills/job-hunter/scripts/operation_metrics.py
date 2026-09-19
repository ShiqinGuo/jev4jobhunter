"""Local per-operation timings; raw evidence remains in the personal data directory."""
from __future__ import annotations

import json
import time
import uuid

import store


class Measurement:
    def __init__(self, operation, session):
        self.operation, self.session = operation, session
        self.started = time.perf_counter()
        self.calls = []
        self.wait_ms = 0.0
        self.recoveries = []

    def transport(self, send, action, args):
        started = time.perf_counter()
        outcome = 'exception'
        detail = None
        try:
            result = send(action, args)
            outcome = result.get('outcome', 'unavailable')
            detail = result.get('error') or result.get('result',{}).get('error')
            return result
        finally:
            self.calls.append({'action': action, 'method': args.get('method') if action == 'cdp' else None,
                               'elapsedMs': round((time.perf_counter()-started)*1000, 3), 'outcome': outcome,'error':detail})

    def save(self, root, result, error=None):
        record = {'schema': 1, 'operation': self.operation, 'session': self.session, 'at': store.stamp(),
                  'elapsedMs': round((time.perf_counter()-self.started)*1000, 3),
                  'browserCalls': len(self.calls), 'browserMs': round(sum(c['elapsedMs'] for c in self.calls), 3),
                  'waitMs': round(self.wait_ms, 3), 'recoveryCount': len(self.recoveries),
                  'recoveries': self.recoveries, 'calls': self.calls,
                  'status': 'error' if error else result.get('status', result.get('classification', 'returned')),
                  'error': str(error) if error else None, 'result': result}
        relative = 'logs/operations/' + uuid.uuid4().hex + '.json'
        (root/'logs'/'operations').mkdir(parents=True, exist_ok=True)
        store.write_json(root/relative, record)
        return {k: record[k] for k in ('elapsedMs','browserCalls','browserMs','waitMs','recoveryCount','status')} | {'evidence':relative}


def compact(result):
    """Keep decision inputs, omit repeated list/body data; full result is persisted."""
    if not isinstance(result, dict):
        return result
    value = {k:v for k,v in result.items() if k not in ('page','observation','candidates')}
    observed = result.get('observation') or (result if 'page' in result else None)
    if observed:
        page = observed['page']
        value.update(evidence=observed.get('evidence'), classification=observed.get('classification'))
        value['page'] = {k:page[k] for k in ('url','accountLabel','visibility','filters','controls','detail',
                                          'loading','endOfList','scrollRemaining') if k in page}
        if 'cards' in page:
            value['page']['cardCount'] = len(page['cards'])
            value['page']['cards'] = page['cards']
        if 'chat' in page:
            value['page']['chat'] = dict(page['chat'])
            if 'observation' in result:
                value['page']['chat'].pop('rows', None)
            value['page']['conversationCount'] = len(page['chat'].get('rows',[]))
    if 'candidates' in result:
        value['candidates'] = {k:{'decision':c['decision'],'card':c.get('card')} for k,c in result['candidates'].items()}
    return value
