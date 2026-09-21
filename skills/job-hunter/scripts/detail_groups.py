"""Bounded sequential JD collection; semantic decisions remain with the caller."""
from copy import deepcopy
import uuid

import store
from browsing_safety import TERMINAL

OPERATIONS = ('collect-details', 'review-detail-group', 'submit-reviewed-detail')


def verify_group(flow, group):
    if (group.get('batchId') != (flow.get('batch') or {}).get('id') or
            any(group.get(k) != flow.get(k) for k in ('policyFingerprint', 'profileFingerprint'))):
        raise store.StoreError('detail-group-context-changed')


def stage(engine, key):
    guard = engine.safety
    with store.transaction(guard.root):
        state, flow = guard._state(active=True)
        if flow.get('pending') or flow.get('activeKey') != key or flow['phase'] != 'detail':
            raise store.StoreError('loaded-active-detail-required')
        page = guard._last_page(flow)
        guard._verify(flow, page)
        detail = page.get('detail') or {}
        if detail.get('key') != key or not detail.get('text') or detail.get('loading'):
            raise store.StoreError('complete-matching-detail-required')
        group = flow['detailGroup']
        group['details'][key] = {**deepcopy(detail), 'evidence': flow['lastObservation']['path']}
        flow.update(activeKey=None, phase='batch')
        guard._save(state)


def execute(engine, operation, data):
    guard = engine.safety
    if operation == 'collect-details':
        keys = data.get('keys')
        if (set(data) - {'keys', 'waitMs'} or not isinstance(keys, list) or not 1 <= len(keys) <= 5
                or any(not isinstance(k, str) for k in keys) or len(set(keys)) != len(keys)):
            raise store.StoreError('one-to-five-distinct-current-candidate-keys-required')
        configured = store.load_policy(guard.root).get('search', {}).get('detailReadGroupSize', 5)
        if type(configured) is not int or not 1 <= configured <= 5 or len(keys) > configured:
            raise store.StoreError('detail-group-exceeds-policy-size')
        timeout = data.get('waitMs', 4000)
        if type(timeout) is not int or not 0 <= timeout <= 10000:
            raise store.StoreError('waitMs-must-be-integer-between-0-and-10000')
        with store.transaction(guard.root):
            state, flow = guard._state(active=True)
            guard._verify(flow, guard._last_page(flow))
            group = flow.get('detailGroup')
            if (group and group.get('batchId') != (flow.get('batch') or {}).get('id')
                    and any(b.get('id') == group.get('batchId') for b in flow.get('retainedBatches', []))):
                # A chat return can reorder the list. Retain the old evidence;
                # absent candidates remain in the backlog and require a fresh read.
                flow.setdefault('retainedDetailGroups', []).append(deepcopy(group))
                flow.pop('detailGroup', None)
                group = None
            if group and not all(flow['candidates'][k]['decision'] in TERMINAL for k in group['keys']):
                verify_group(flow, group)
                if group['keys'] != keys or group.get('reviews'):
                    raise store.StoreError('finish-current-detail-group-first')
            else:
                group = None
            if any(k not in (flow.get('batch') or {}).get('keys', []) or
                   (flow['candidates'][k]['decision'] != 'shortlisted' and
                    not (group and flow['candidates'][k]['decision'] in TERMINAL)) for k in keys):
                raise store.StoreError('screen-current-batch-before-collection')
            if flow.get('pending') or (flow.get('activeKey') and flow['activeKey'] not in keys):
                raise store.StoreError('resolve-current-detail-before-collection')
            if not group:
                flow['detailGroup'] = {'id': uuid.uuid4().hex, 'keys': keys, 'details': {},
                    'batchId': flow['batch']['id'], 'policyFingerprint': flow['policyFingerprint'],
                    'profileFingerprint': flow['profileFingerprint'], 'reviews': {}, 'at': store.stamp()}
                guard._save(state)
        for key in keys:
            flow = guard.status()['flow']
            if key in flow['detailGroup']['details'] or flow['candidates'][key]['decision'] in TERMINAL:
                continue
            if flow.get('activeKey') == key:
                result = engine.execute('inspect')
                if result['classification'] != 'normal':
                    return {'status': 'blocked', 'observation': result, 'group': guard.status()['flow']['detailGroup']}
            else:
                result = engine.execute('open-detail-and-wait', {'key': key, 'waitMs': timeout})
                if result.get('status') != 'ready':
                    return {'status': 'pending', 'step': result, 'group': guard.status()['flow']['detailGroup']}
            stage(engine, key)
        return {'status': 'ready', 'group': guard.status()['flow']['detailGroup'], 'nextAction': 'model-review-detail-group'}
    if operation == 'review-detail-group':
        with store.transaction(guard.root):
            state, flow = guard._state()
            group = flow.get('detailGroup') or {}
            reviews = data.get('reviews')
            if (set(data) != {'groupId', 'reviews'} or data['groupId'] != group.get('id')
                    or not isinstance(reviews, list) or len(reviews) != len(group.get('details', {}))
                    or any(not isinstance(r, dict) for r in reviews)
                    or {r.get('key') for r in reviews} != set(group.get('details', {}))
                    or any(k not in group.get('details', {}) and flow['candidates'][k]['decision'] not in TERMINAL for k in group.get('keys', []))
                    or group.get('reviews') or flow.get('activeKey') or flow.get('pending')):
                raise store.StoreError('complete-unreviewed-detail-group-required')
            guard._verify(flow, guard._last_page(flow))
            verify_group(flow, group)
            for review in reviews:
                store.required_string(review, 'evidence')
                if review.get('decision') not in ('apply', 'skipped', 'deferred'):
                    raise store.StoreError('invalid-detail-decision')
                if review['decision'] == 'apply' and review.get('eligibilityPassed') is not True:
                    raise store.StoreError('jd-eligibility-review-required')
            for review in reviews:
                key = review['key']
                group['reviews'][key] = deepcopy(review)
                # Apply stays shortlisted until the current page is verified again.
                if review['decision'] != 'apply':
                    flow['candidates'][key].update(decision=review['decision'], reviewEvidence=review['evidence'])
            guard._save(state)
            return {'status': 'reviewed', 'group': group}
    if operation == 'submit-reviewed-detail':
        if set(data) != {'request'} or not isinstance(data['request'], dict):
            raise store.StoreError('single-outbox-request-required')
        key = data['request'].get('targetKey')
        state, flow = guard._state(active=True)
        group = flow.get('detailGroup') or {}
        review = group.get('reviews', {}).get(key)
        if not review or review['decision'] != 'apply' or flow.get('pending') or flow.get('activeKey'):
            raise store.StoreError('reviewed-group-candidate-required')
        guard._verify(flow, guard._last_page(flow))
        if (group['policyFingerprint'] != flow['policyFingerprint'] or
                group['profileFingerprint'] != flow['profileFingerprint'] or group['batchId'] != flow['batch']['id']):
            raise store.StoreError('detail-group-context-changed')
        result = engine.execute('open-detail-and-wait', {'key': key})
        if result.get('status') != 'ready':
            return result
        current = result['observation']['page']['detail']
        if current['text'] != group['details'][key]['text']:
            return {**result, 'status': 'review-required', 'nextAction': 'model-review-current-detail',
                    'reason': 'jd-changed-since-group-review'}
        guard.review_detail(review)
        return engine.execute('submit', data)
    raise store.StoreError('unsupported-detail-group-operation')
