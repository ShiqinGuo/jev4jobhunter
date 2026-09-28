"""Local per-operation timings; raw evidence remains in the personal data directory."""
from __future__ import annotations

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
                  'error': str(error) if error else None,
                  'result': {k:result[k] for k in ('id','groupId','batchId','status','classification','nextAction') if k in result},
                  'evidence': evidence_refs(result)}
        relative = 'logs/operations/' + uuid.uuid4().hex + '.json'
        (root/'logs'/'operations').mkdir(parents=True, exist_ok=True)
        store.write_json(root/relative, record)
        return {k: record[k] for k in ('elapsedMs','browserCalls','browserMs','waitMs','recoveryCount','status')} | {'evidence':relative}


def evidence_refs(value):
    """Telemetry points to stored material; it never keeps another result copy."""
    if isinstance(value, dict):
        return sorted({ref for k,v in value.items() for ref in
                       ([v.split(': ',1)[0]] if k in ('evidence','path') and isinstance(v,str) and v.startswith('logs/')
                        else evidence_refs(v))})
    if isinstance(value, list):
        return sorted({ref for item in value for ref in evidence_refs(item)})
    return []


def group_summary(group):
    """Resume by ID and evidence without reprinting every previously read JD."""
    return {k:group[k] for k in ('id','batchId','keys') if k in group} | {
        'decisions': group.get('decisions', {key:review['decision'] for key,review in group.get('reviews',{}).items()}),
        'details': {key:{k:v for k,v in detail.items() if k in ('key','evidence')}
                    for key,detail in group.get('details',{}).items()}}


def compact(result, operation=None):
    """Project a result for its consumer. Full observations already have evidence files."""
    if not isinstance(result, dict):
        return result
    value = {k:v for k,v in result.items() if k not in ('page','observation','candidates','group','flow')}
    if isinstance(value.get('step'),dict):
        value['step']=compact(value['step'],operation)
    if 'group' in result:
        value['group'] = result['group'] if operation=='collect-details' else group_summary(result['group'])
    if 'flow' in result:
        flow=result['flow']
        value['flow']={k:v for k,v in flow.items() if k not in
                      ('candidates','events','retainedBatches','retainedDetailGroups','archives','detailGroup','seen')}
        if 'candidates' in flow:
            value['flow']['candidateCount']=len(flow['candidates'])
        if 'seen' in flow:
            value['flow']['seenCount']=len(flow['seen'])
        if flow.get('detailGroup'):
            value['flow']['detailGroup']=group_summary(flow['detailGroup'])
    observed = result.get('observation') or (result if 'page' in result else None)
    if observed:
        page = observed['page']
        value.update(evidence=observed.get('evidence'), classification=observed.get('classification'))
        fields=('url','accountLabel','visibility','filters','loading','endOfList','scrollRemaining')
        value['page'] = {k:page[k] for k in fields if k in page}
        if operation in (None,'inspect','control','focus-page','ensure-page') and 'controls' in page:
            value['page']['controls']=page['controls']
        if operation in (None,'inspect','open-detail','open-detail-and-wait','submit-reviewed-detail') and 'detail' in page:
            value['page']['detail']=page['detail']
        if 'cards' in page:
            value['page']['cardCount'] = len(page['cards'])
            if operation in (None,'inspect'):
                value['page']['cards'] = page['cards']
        if 'chat' in page:
            value['page']['conversationCount'] = len(page['chat'].get('rows',[]))
            if operation in (None,'inspect-chat','open-conversation-and-wait','chat-job-detail'):
                value['page']['chat'] = dict(page['chat'])
                if 'observation' in result:
                    value['page']['chat'].pop('rows', None)
            else:
                value['page']['chat']={k:v for k,v in page['chat'].items() if k in ('recipient','job','resumeConfirmation','resumeConfirmationStage','resumeWaiting')}
    if 'candidates' in result:
        value['candidates'] = {k:{'decision':c['decision'],'evidence':c.get('evidence'),
            **({'card':c.get('card')} if c['decision']=='unreviewed' else {})}
            for k,c in result['candidates'].items()}
    return value
