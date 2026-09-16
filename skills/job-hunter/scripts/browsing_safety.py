"""Durable, one-candidate browsing workflow. No network or browser operations here."""
from __future__ import annotations

from pathlib import Path
from datetime import datetime
import re
import uuid

import store
from policy_rules import check_target

TERMINAL = {'skipped', 'deferred', 'succeeded', 'failed', 'unknown'}
ACCESS_REASONS = ('access-restrict', 'verification', 'captcha', 'security-check',
                  'account-abnormal', 'ip-abnormal', 'login-required')


def access_block(state: dict, platform: str) -> dict | None:
    """A send quota is not an access ban; restriction scope is context-aware."""
    for scope, block in store.applicable_blocks(state, platform):
        reason = block.get('reason', '').lower()
        if block and block.get('active', True) and (
                block.get('scope') == 'access' or block.get('blocksAccess') is True
                or any(word in reason for word in ACCESS_REASONS)):
            return {'platform': scope, **block}
    return None


def require_access(state: dict, platform: str) -> None:
    block = access_block(state, platform)
    if block:
        # Expiry is an earliest review time, never an automatic clearance.
        raise store.StoreError('platform-access-blocked:' + str(block.get('retryNotBefore', 'review-required')))


def classify(page: dict) -> str:
    """Classify rendered content, not HTTP 200 or a successful browser RPC."""
    body, url = page.get('body', ''), page.get('url', '')
    signals = body
    # A job describing security software is not a site challenge. Alerts outside
    # the job content still win even when old cards remain rendered underneath.
    for content in [(page.get('detail') or {}).get('text', ''), *[c.get('text', '') for c in page.get('cards', [])]]:
        if content:
            signals = signals.replace(content, '')
    if any(text in signals for text in ('访问受限', 'IP存在异常行为', '账户存在异常行为',
                                      '账号存在异常行为', '请完成安全验证', '请完成验证', '滑动验证')):
        return 'access-restricted'
    if '/passport/zp/403' in url:
        return 'access-restricted'
    normal = bool(page.get('cards') or page.get('detail') or page.get('controls'))
    if ('请稍候' in body or '安全检查' in body) and not normal:
        return 'security-check'
    if ('登录' in body and not page.get('accountLabel') and not normal) or '登录查看完整内容' in body:
        return 'login-required'
    if page.get('loading') or not normal:
        return 'loading-or-unsupported'
    return 'normal'


def new_flow() -> dict:
    return {'version': 1, 'query': None, 'batch': None, 'candidates': {}, 'seen': [],
            'activeKey': None, 'pending': None, 'phase': 'idle', 'events': []}


class Safety:
    def __init__(self, root: Path, token: str, platform: str, session: str):
        self.root, self.token, self.platform, self.session = root, token, platform, session

    def _flow(self, state: dict) -> dict:
        return state.setdefault('browsing', {}).setdefault(self.platform, new_flow())

    def _save(self, state: dict) -> None:
        state['runLock']['heartbeatAt'] = store.stamp()
        store.write_json(self.root / 'state.json', state)

    def _state(self, active=False) -> tuple[dict, dict]:
        state = store.load_state(self.root)
        store.require_token(state, self.token)
        if active:
            require_access(state, self.platform)
            context = store.active_account_context(state, self.platform)
            if context and context.get('session') != self.session:
                raise store.StoreError('current-account-context-session-required')
        return state, self._flow(state)

    def status(self) -> dict:
        state = store.load_state(self.root)
        return {'platform': self.platform, 'accessBlock': access_block(state, self.platform),
                'accountContext': store.active_account_context(state, self.platform),
                'flow': state.get('browsing', {}).get(self.platform, new_flow()), 'networkCalls': 0}

    def preflight(self) -> None:
        self._state(active=True)

    def select_account_context(self, data: dict) -> dict:
        """Attribute a legacy block and select an explicitly identified account locally.

        This records the user's account distinction; it never invents a site ID,
        clears the previous account's restriction, or edits policy/profile files.
        """
        context_id = store.required_string(data, 'contextId')
        account = store.required_string(data, 'accountLabel')
        evidence = store.required_string(data, 'evidence')
        with store.transaction(self.root):
            state, flow = self._state()
            page = self._last_page(flow)
            if page.get('accountLabel') != account or classify(page) != 'normal':
                raise store.StoreError('selected-account-normal-page-evidence-required')
            registry = state.setdefault('accountContexts', {}).setdefault(self.platform, {
                'activeContextId': None, 'contexts': {}, 'events': []})
            contexts = registry['contexts']
            existing = contexts.get(context_id)
            if existing and existing['accountLabel'] != account:
                raise store.StoreError('context-id-already-belongs-to-another-account')
            if not existing and data.get('userDeclaredIndependent') is not True:
                raise store.StoreError('explicit-independent-account-declaration-required')
            previous_id = registry.get('activeContextId')
            pending = flow.get('pending') or {}
            if pending.get('operation') == 'submit' or any(
                    a.get('status') == 'pending' and a.get('platform') == self.platform
                    for a in state['actions'].values()):
                raise store.StoreError('resolve-pending-outbox-before-account-switch')
            legacy = state['blocks'].get(self.platform, {})
            if legacy and not legacy.get('accountContextId') and legacy.get('appliesToContextIds') is None:
                if legacy.get('applicability') == 'all-contexts' and legacy.get('scopeEvidence'):
                    raise store.StoreError('explicit-shared-scope-restriction-applies-to-new-context')
                original_id = store.required_string(data, 'originalContextId')
                original_account = store.required_string(data, 'originalAccountLabel')
                if original_id == context_id:
                    raise store.StoreError('distinct-original-account-context-required')
                recorded = legacy.get('accountLabel') or flow.get('accountLabel')
                if recorded and recorded != original_account:
                    raise store.StoreError('original-account-attribution-mismatch')
                original = contexts.get(original_id)
                if original and original['accountLabel'] != original_account:
                    raise store.StoreError('original-context-id-account-mismatch')
                contexts.setdefault(original_id, {'id': original_id, 'accountLabel': original_account,
                    'session': legacy.get('session') or flow.get('session'), 'attributionEvidence': evidence})
                legacy.update(accountContextId=original_id, applicability='observed-context',
                              scopeAttributedAt=store.stamp(), scopeAttributionEvidence=evidence)
            if previous_id != context_id:
                relative = 'logs/browsing/account-context-' + uuid.uuid4().hex + '.json'
                (self.root / 'logs' / 'browsing').mkdir(parents=True, exist_ok=True)
                store.write_json(self.root / relative, {'at': store.stamp(), 'reason': 'explicit-account-context-selection',
                    'evidence': evidence, 'flow': flow})
                last = flow.get('lastObservation')
                archives = [*flow.get('archives', []), relative]
                flow.clear()
                flow.update(new_flow(), archives=archives, lastObservation=last)
            contexts[context_id] = {**(existing or {}), 'id': context_id, 'accountLabel': account,
                'session': self.session, 'selectedAt': store.stamp(), 'selectionEvidence': evidence,
                'observation': flow['lastObservation']['path'], 'identitySource': 'user-declared-context-and-visible-label'}
            registry['activeContextId'] = context_id
            registry['events'].append({'at': store.stamp(), 'fromContextId': previous_id,
                'toContextId': context_id, 'evidence': evidence, 'observation': flow['lastObservation']['path']})
            flow['accountContextId'] = context_id
            self._save(state)
            return {'selectedContext': contexts[context_id], 'accessBlock': access_block(state, self.platform),
                    'networkCalls': 0, 'policyAndProfileUnchanged': True}

    def clear_access_block(self, data: dict) -> dict:
        evidence = store.required_string(data, 'evidence')
        with store.transaction(self.root):
            state, flow = self._state()
            context = store.active_account_context(state, self.platform)
            block = next((block for scope, block in store.applicable_blocks(state, self.platform)
                          if scope == self.platform), {})
            if not block.get('active', True) or not block:
                return {'cleared': False, 'reason': 'no-active-platform-block'}
            if not (block.get('scope') == 'access' or block.get('blocksAccess') is True or
                    any(word in block.get('reason', '').lower() for word in ACCESS_REASONS)):
                raise store.StoreError('access-block-required:send-quota-has-separate-recovery')
            if block.get('session') and block['session'] != self.session:
                raise store.StoreError('same-session-recovery-required')
            if block.get('retryNotBefore'):
                earliest = datetime.fromisoformat(block['retryNotBefore'])
                if earliest.tzinfo is None or store.now() < earliest:
                    raise store.StoreError('platform-retry-time-not-reached')
            page = self._last_page(flow)
            policy = store.load_policy(self.root)
            accounts = [p.get('accountLabel') for p in policy.get('platforms', []) if p.get('id') == self.platform]
            expected = block.get('accountLabel') or flow.get('accountLabel') or next((a for a in accounts if a), None)
            if not expected or page.get('accountLabel') != expected or classify(page) != 'normal':
                raise store.StoreError('same-account-normal-page-evidence-required')
            observed = datetime.fromisoformat(flow['lastObservation']['at'].replace('Z', '+00:00'))
            for field in ('observedAt', 'retryNotBefore'):
                if block.get(field):
                    boundary = datetime.fromisoformat(block[field].replace('Z', '+00:00'))
                    if boundary.tzinfo is None or observed < boundary:
                        raise store.StoreError('normal-observation-must-follow-restriction-and-retry-time')
            block.update(active=False, clearedAt=store.stamp(), clearEvidence=evidence,
                         recoveryObservation=flow['lastObservation']['path'])
            self._save(state)
            return {'cleared': True, 'networkCalls': 0}

    def cancel_unsupported_step(self) -> None:
        """Only for a driver result proving no UI action occurred, never a timeout."""
        with store.transaction(self.root):
            state, flow = self._state()
            pending = flow.get('pending')
            if not pending or pending['operation'] == 'submit':
                raise store.StoreError('non-submit-step-required')
            flow.update(phase=pending['previousPhase'], activeKey=pending['previousActiveKey'], pending=None)
            flow['events'].append({'at': store.stamp(), 'operation': pending['operation'], 'status': 'unsupported-no-action'})
            self._save(state)

    def finish_list_read(self, data: dict) -> dict:
        """Close an observed scroll with no growth, without claiming search exhaustion."""
        evidence = store.required_string(data, 'evidence')
        with store.transaction(self.root):
            state, flow = self._state(active=True)
            pending = flow.get('pending') or {}
            page = self._last_page(flow)
            self._verify(flow, page)
            if (flow['phase'] != 'await-list' or pending.get('operation') != 'scroll'
                    or flow['lastObservation']['path'] == pending.get('beforeObservation')
                    or not self.batch_done({**flow, 'pending': None})
                    or page.get('loading') or page.get('listTailBelowViewport') is not False
                    or page.get('scrollRemaining', 0) > 1
                    or any(c.get('key') not in flow['seen'] for c in page.get('cards', []))):
                raise store.StoreError('completed-list-no-growth-observation-required')
            flow['events'].append({'at': store.stamp(), 'operation': 'finish-list-read',
                'status': 'observed-no-growth', 'evidence': evidence,
                'observation': flow['lastObservation']['path']})
            flow.update(pending=None, phase='batch')
            self._save(state)
            return {'completed': True, 'exhaustionVerified': False, 'networkCalls': 0}

    def defer_detail(self, data: dict) -> dict:
        """Set aside an interrupted read after new passive evidence; never retry it."""
        evidence = store.required_string(data, 'evidence')
        with store.transaction(self.root):
            state, flow = self._state()
            pending, key = flow.get('pending') or {}, data.get('key')
            if pending.get('operation') != 'open-detail' or not key or key != flow['activeKey']:
                raise store.StoreError('only-interrupted-detail-read-can-be-deferred')
            self._last_page(flow)
            if flow['lastObservation']['path'] == pending.get('beforeObservation'):
                raise store.StoreError('new-passive-observation-required-before-defer')
            flow['candidates'][key].update(decision='deferred', reviewEvidence=evidence)
            flow['events'].append({'at': store.stamp(), 'operation': 'defer-detail', 'key': key,
                                   'evidence': evidence, 'observation': flow['lastObservation']['path']})
            flow.update(pending=None, activeKey=None, phase='batch')
            self._save(state)
            return {'key': key, 'decision': 'deferred', 'networkCalls': 0}

    @staticmethod
    def batch_done(flow: dict) -> bool:
        return not flow['activeKey'] and not flow['pending'] and all(
            flow['candidates'][key]['decision'] in TERMINAL for key in (flow.get('batch') or {}).get('keys', []))

    def observe(self, page: dict) -> dict:
        """Save passive evidence; never clear an existing restriction or change batches."""
        classification = classify(page)
        with store.transaction(self.root):
            state, flow = self._state()
            log = self.root / 'logs' / 'browsing'
            log.mkdir(parents=True, exist_ok=True)
            relative = 'logs/browsing/' + uuid.uuid4().hex + '.json'
            store.write_json(self.root / relative, {'at': store.stamp(), 'session': self.session,
                                                    'classification': classification, 'page': page})
            flow['lastObservation'] = {'path': relative, 'at': store.stamp(),
                                       'classification': classification, 'session': self.session,
                                       'runTokenHash': store.fingerprint(self.token)}
            if classification in ('access-restricted', 'security-check', 'login-required'):
                context = store.active_account_context(state, self.platform)
                if context:
                    container = state.setdefault('contextBlocks', {}).setdefault(self.platform, {})
                    block_key = context['id']
                else:
                    container, block_key = state['blocks'], self.platform
                old = container.get(block_key, {})
                block = {**old, 'active': True, 'scope': 'access', 'blocksAccess': True,
                         'reason': 'platform-' + classification, 'evidence': relative, 'observedAt': store.stamp()}
                # Keep the original failure and earliest-retry evidence across accounts/runs.
                block.setdefault('firstEvidence', old.get('evidence', relative))
                account = flow.get('accountLabel') or next((p.get('accountLabel') for p in store.load_policy(self.root).get('platforms', []) if p.get('id') == self.platform), None)
                if account:
                    block.setdefault('accountLabel', account)
                if context:
                    block.update(accountContextId=context['id'], accountLabel=context['accountLabel'],
                                 applicability='observed-context')
                if re.search(r'IP\s*存在异常', page.get('body', ''), re.I):
                    block['reportedScope'] = 'ip'
                block.setdefault('session', self.session)
                match = re.search(r'(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})日?\s+(\d{1,2}):(\d{2})', page.get('body', ''))
                if match and not block.get('retryNotBefore'):
                    y, m, d, h, minute = map(int, match.groups())
                    if self.platform == 'boss':
                        block['retryNotBefore'] = f'{y:04}-{m:02}-{d:02}T{h:02}:{minute:02}:00+08:00'
                container[block_key] = block
            if classification == 'normal' and flow.get('query'):
                if self._identity_matches(flow, page):
                    pending = flow.get('pending') or {}
                    detail = page.get('detail') or {}
                    if pending.get('operation') == 'open-detail' and detail.get('key') == flow['activeKey']:
                        flow['candidates'][flow['activeKey']]['detail'] = {**detail, 'evidence': relative,
                            'profileFingerprint': flow.get('profileFingerprint'),
                            'policyFingerprint': flow.get('policyFingerprint')}
                        flow['pending'] = None
                        flow['phase'] = 'detail'
                    elif pending.get('operation') in ('control', 'dismiss-receipt', 'ack-quota-notice'):
                        # New rendered evidence is required before another individual control action.
                        flow['pending'] = None
            self._save(state)
        return {'classification': classification, 'evidence': relative, 'page': page}

    def _last_page(self, flow: dict) -> dict:
        last = flow.get('lastObservation', {})
        if last.get('session') != self.session:
            raise store.StoreError('current-session-observation-required')
        if last.get('runTokenHash') != store.fingerprint(self.token):
            raise store.StoreError('current-run-observation-required')
        return store.read_json(self.root / last['path'])['page']

    def _identity_matches(self, flow: dict, page: dict) -> bool:
        return flow.get('session') == self.session and page.get('accountLabel') == flow.get('accountLabel')

    def _verify(self, flow: dict, page: dict, filters=True) -> None:
        if not flow.get('query') or not self._identity_matches(flow, page):
            raise store.StoreError('query-session-or-account-mismatch')
        if flow['policyFingerprint'] != store.fingerprint(store.load_policy(self.root)):
            raise store.StoreError('policy-changed-revalidate-query')
        if 'profileFingerprint' not in flow or flow['profileFingerprint'] != store.profile_fingerprint(self.root):
            raise store.StoreError('profile-changed-revalidate-query')
        if classify(page) != 'normal':
            raise store.StoreError('page-not-ready-or-unsupported')
        if filters:
            expected, actual = flow['query'], page.get('filters', {})
            if any(actual.get(k) != expected[k] for k in ('city', 'keyword')) or set(
                    actual.get('experience', [])) != set(expected['experience']):
                raise store.StoreError('visible-selected-filters-do-not-match-query')
            if expected.get('salary') and set(actual.get('salary', [])) != set(expected['salary']):
                raise store.StoreError('visible-selected-filters-do-not-match-query')

    def start_query(self, data: dict) -> dict:
        query = {k: data.get(k) for k in ('city', 'keyword', 'experience')}
        query['salary'] = data.get('salary', [])
        if not all(isinstance(query[k], str) and query[k].strip() for k in ('city', 'keyword')):
            raise store.StoreError('city-and-keyword-required')
        if not isinstance(query['experience'], list) or not all(isinstance(x, str) and x for x in query['experience']):
            raise store.StoreError('experience-label-list-required')
        if not isinstance(query['salary'], list) or not all(isinstance(x, str) and x for x in query['salary']):
            raise store.StoreError('salary-label-list-required')
        account = store.required_string(data, 'accountLabel')
        with store.transaction(self.root):
            state, flow = self._state(active=True)
            context = store.active_account_context(state, self.platform)
            if context and (context['accountLabel'] != account or context['id'] != flow.get('accountContextId')):
                raise store.StoreError('query-account-context-mismatch')
            if not self.batch_done(flow):
                raise store.StoreError('finish-current-batch-before-query-change')
            policy = store.load_policy(self.root)
            search = policy.get('search', {})
            if 'queries' in search and query['keyword'] not in search['queries']:
                raise store.StoreError('keyword-outside-policy:use-search-queries')
            experience = search.get('experienceFilter', {})
            if experience.get('enabled') and (not query['experience'] or not set(query['experience']).issubset(
                    experience.get('allowedLabels', []))):
                raise store.StoreError('experience-outside-policy')
            salary = search.get('salaryFilter', {})
            if salary.get('enabled') and (not query['salary'] or not set(query['salary']).issubset(
                    salary.get('allowedLabels', []))):
                raise store.StoreError('salary-outside-policy')
            for key, rule in (('experience', experience), ('salary', salary)):
                if rule.get('enabled') and 'selectedLabels' in rule and set(query[key]) != set(rule['selectedLabels']):
                    raise store.StoreError(key + '-selection-must-match-policy')
            if search.get('singleCityPerDay'):
                plan = state['scheduler'].get('cityDailyPlan', {})
                day = store.local_now(policy).date().isoformat()
                if plan.get('date') != day or query['city'] != plan.get('lockedCity', plan.get('city')):
                    raise store.StoreError('daily-city-plan-required-or-city-mismatch')
            accounts = [p.get('accountLabel') for p in policy.get('platforms', []) if p.get('id') == self.platform]
            if any(expected and expected != account for expected in accounts):
                raise store.StoreError('policy-account-mismatch')
            profile_hash, policy_hash = store.profile_fingerprint(self.root), store.fingerprint(policy)
            if flow.get('query') and (flow.get('profileFingerprint') != profile_hash or
                    'profileFingerprint' not in flow or flow.get('policyFingerprint') != policy_hash or
                    flow.get('accountLabel') != account):
                folder = self.root / 'logs' / 'browsing'
                folder.mkdir(parents=True, exist_ok=True)
                relative = 'logs/browsing/context-' + uuid.uuid4().hex + '.json'
                store.write_json(self.root / relative, {'at': store.stamp(), 'reason': 'review-context-changed', 'flow': flow})
                flow.setdefault('archives', []).append(relative)
                flow.update(candidates={}, seen=[])
            flow.update(query=query, accountLabel=account, session=self.session,
                        profileFingerprint=profile_hash, policyFingerprint=policy_hash, batch=None,
                        endOfList=False, phase='configuring')
            flow['events'].append({'at': store.stamp(), 'operation': 'start-query', 'query': query})
            self._save(state)
            return {'query': query, 'phase': flow['phase']}

    def capture_list(self) -> dict:
        with store.transaction(self.root):
            state, flow = self._state(active=True)
            page = self._last_page(flow)
            self._verify(flow, page)
            if flow['phase'] not in ('configuring', 'await-list', 'batch'):
                raise store.StoreError('finish-current-candidate-before-list')
            if flow['phase'] == 'batch' and flow.get('batch'):
                # A passive read or spontaneous UI prefetch cannot expand the current batch.
                return {'batch': flow['batch'], 'unchanged': True}
            previous = set(flow['seen'])
            cards = [c for c in page.get('cards', []) if isinstance(c.get('key'), str)
                     and c['key'].startswith(self.platform + ':') and len(c['key']) > len(self.platform) + 1
                     and (flow['phase'] == 'configuring' or c['key'] not in previous)]
            if not cards and not page.get('endOfList'):
                pending = flow.get('pending') or {}
                if (flow['phase'] == 'await-list' and pending.get('operation') == 'scroll'
                        and flow['lastObservation']['path'] != pending.get('beforeObservation')
                        and self.batch_done({**flow, 'pending': None})
                        and (page.get('listTailBelowViewport') is True or page.get('scrollRemaining', 0) > 1)
                        and not page.get('loading')):
                    # A viewport-sized scroll may only traverse the already-reviewed
                    # batch. Permit one further visible scroll, never prefetch a new batch.
                    flow.update(phase='batch', pending=None)
                    self._save(state)
                    reason = ('processed-list-tail-not-yet-reached' if page.get('listTailBelowViewport')
                              else 'loaded-tail-visible-scroll-space-remains')
                    return {'waiting': True, 'reason': reason, 'mayScroll': True,
                            'exhaustionVerified': False, 'nextAction': 'scroll-current-query'}
                return {'waiting': True, 'reason': 'no-new-ids-yet', 'mayScroll': False,
                        'exhaustionVerified': False, 'nextAction': 'observe-current-query'}
            keys = []
            for card in cards:
                key = card['key']
                if not key.startswith(self.platform + ':') or key in keys:
                    continue
                keys.append(key)
                if key in previous:
                    # Repeated first pages after a new keyword form a settled
                    # batch. Preserve their old decisions and allow one scroll.
                    continue
                reason = self._list_exclusion(state, card)
                flow['candidates'][key] = {'card': card, 'decision': 'skipped' if reason else 'unreviewed',
                                            'evidence': reason or '', 'listEvidence': flow['lastObservation']['path'],
                                            'profileFingerprint': flow['profileFingerprint'],
                                            'policyFingerprint': flow['policyFingerprint']}
            flow['batch'] = {'id': uuid.uuid4().hex, 'keys': keys, 'query': flow['query'],
                             'filterEvidence': flow['lastObservation']['path'], 'at': store.stamp()}
            flow['seen'].extend(k for k in keys if k not in previous)
            flow.update(pending=None, phase='batch', endOfList=bool(page.get('endOfList')))
            self._save(state)
            return {'batch': flow['batch'], 'candidates': {k: flow['candidates'][k] for k in keys}}

    def _list_exclusion(self, state: dict, card: dict) -> str:
        key = card['key']
        flow = self._flow(state)
        if card.get('city') and card['city'] != flow['query']['city']:
            return 'wrong-city'
        if any(a.get('targetKey') == key and a.get('status') in store.HELD for a in state['actions'].values()):
            return 'already-contacted-or-unresolved'
        if state['jobs'].get(key, {}).get('legacyContacted'):
            return 'legacy-contact-requires-reconciliation'
        policy = store.load_policy(self.root)
        experience = policy.get('search', {}).get('experienceFilter', {})
        if experience.get('enabled') and card.get('experience') in experience.get('excludedLabels', []):
            return 'excluded-experience-label'
        try:
            check_target(policy, state, {'kind': 'greet', 'platform': self.platform, 'targetKey': key,
                                          'targetFacts': card}, require_complete=False)
        except store.StoreError as error:
            return str(error)
        return ''

    def screen(self, data: dict) -> dict:
        key, decision = store.required_string(data, 'key'), data.get('decision')
        evidence = store.required_string(data, 'evidence')
        if decision not in ('shortlisted', 'skipped', 'deferred'):
            raise store.StoreError('invalid-list-decision')
        with store.transaction(self.root):
            state, flow = self._state()
            if key not in (flow.get('batch') or {}).get('keys', []):
                raise store.StoreError('candidate-not-in-current-batch')
            candidate = flow['candidates'][key]
            can_defer_shortlist = candidate['decision'] == 'shortlisted' and decision in ('skipped', 'deferred') and key != flow['activeKey']
            if candidate['decision'] != 'unreviewed' and not can_defer_shortlist:
                raise store.StoreError('list-decision-already-recorded')
            if decision == 'shortlisted' and self._list_exclusion(state, candidate['card']):
                raise store.StoreError('candidate-excluded')
            candidate.update(decision=decision, evidence=evidence)
            self._save(state)
            return {'key': key, 'decision': decision}

    def reserve(self, operation: str, args: dict) -> dict:
        """Persist intent before exactly one UI action; no loops and no blind retry."""
        with store.transaction(self.root):
            state, flow = self._state(active=True)
            if flow['pending']:
                raise store.StoreError('browser-step-unresolved:inspect-current-page')
            page = self._last_page(flow)
            self._verify(flow, page, filters=operation != 'control')
            previous_phase, previous_active = flow['phase'], flow['activeKey']
            if operation == 'control':
                if flow['phase'] != 'configuring' or flow['activeKey']:
                    raise store.StoreError('controls-only-during-query-configuration')
                if args.get('id') not in {c['id'] for c in page.get('controls', [])}:
                    raise store.StoreError('control-not-in-current-page')
                control = next(c for c in page['controls'] if c['id'] == args['id'])
                if control['kind'] == 'keyword' and args.get('value') != flow['query']['keyword']:
                    raise store.StoreError('keyword-control-must-match-query')
                if control['kind'] == 'filter-option':
                    if control.get('filter') == 'city' and control['label'] != flow['query']['city']:
                        raise store.StoreError('city-control-must-match-query')
                    if control.get('filter') == 'experience' and control['label'] not in flow['query']['experience']:
                        if control['label'] not in page.get('filters', {}).get('experience', []):
                            raise store.StoreError('experience-control-must-match-query')
                    if control.get('filter') == 'salary' and control['label'] not in flow['query'].get('salary', []):
                        if control['label'] not in page.get('filters', {}).get('salary', []):
                            raise store.StoreError('salary-control-must-match-query')
            elif operation == 'open-detail':
                key = args.get('key')
                if flow['activeKey'] or key not in (flow.get('batch') or {}).get('keys', []):
                    raise store.StoreError('one-current-list-candidate-required')
                if flow['candidates'][key]['decision'] != 'shortlisted':
                    raise store.StoreError('list-screen-required-before-detail')
                if self._list_exclusion(state, flow['candidates'][key]['card']):
                    raise store.StoreError('candidate-excluded')
                if key not in {c['key'] for c in page.get('cards', [])}:
                    raise store.StoreError('candidate-no-longer-in-rendered-list')
                flow['activeKey'] = key
                flow['phase'] = 'detail-opening'
            elif operation == 'scroll':
                if flow['phase'] != 'batch' or not self.batch_done(flow) or flow.get('endOfList'):
                    raise store.StoreError('finish-current-batch-before-one-scroll')
                flow['phase'] = 'await-list'
            elif operation == 'submit':
                key = flow['activeKey']
                candidate = flow['candidates'].get(key, {})
                detail = page.get('detail') or {}
                if candidate.get('decision') != 'apply' or detail.get('key') != key:
                    raise store.StoreError('reviewed-current-detail-required')
                if detail.get('text') != candidate.get('detail', {}).get('text'):
                    raise store.StoreError('jd-changed-review-again')
                if args.get('key') != key or not args.get('actionId'):
                    raise store.StoreError('outbox-and-candidate-required')
                action = state['actions'].get(args['actionId'], {})
                if action.get('status') != 'pending' or action.get('targetKey') != key or action.get('platform') != self.platform:
                    raise store.StoreError('pending-matching-outbox-required')
                if '已向BOSS发送消息' in page.get('body', ''):
                    raise store.StoreError('dismiss-previous-receipt-before-submit')
            elif operation == 'ack-quota-notice':
                action = state['actions'].get(args.get('actionId'), {})
                key = (page.get('detail') or {}).get('key')
                ack_events = [e for e in flow['events'] if e.get('operation') == 'ack-quota-notice']
                proven_no_click = (len(ack_events) >= 2
                    and ack_events[-1].get('status') == 'unsupported-no-action'
                    and ack_events[-2].get('args', {}).get('actionId') == args.get('actionId'))
                if (action.get('status') != 'unknown' or action.get('kind') != 'greet'
                        or action.get('platform') != self.platform or action.get('targetKey') != key
                        or action.get('accountLabel') != flow.get('accountLabel')
                        or action.get('browsingContext', {}).get('batchId') != (flow.get('batch') or {}).get('id')
                        or (action.get('quotaNoticeAckAttempted') and not proven_no_click) or flow['activeKey']):
                    raise store.StoreError('unresolved-matching-once-only-quota-notice-required')
                if not re.search(r'您今天已与\s*\d+\s*位BOSS沟通[，,]\s*还剩\s*[1-9]\d*\s*次沟通机会哦', page.get('body', '')):
                    raise store.StoreError('positive-quota-notice-required')
                action['quotaNoticeAckAttempted'] = store.stamp()
            elif operation == 'dismiss-receipt':
                if flow['activeKey']:
                    # A prior receipt can remain rendered when the user opens
                    # the next detail before closing it.  Permit only closing
                    # that visible receipt while the new detail is reviewed
                    # for application, and never while its own outbox is
                    # pending or unresolved.
                    candidate = flow['candidates'].get(flow['activeKey'], {})
                    has_held_action = any(
                        action.get('targetKey') == flow['activeKey']
                        and action.get('status') in store.HELD
                        for action in state.get('actions', {}).values())
                    if candidate.get('decision') != 'apply' or has_held_action:
                        raise store.StoreError('reconcile-before-dismiss')
            else:
                raise store.StoreError('unsupported-browser-step')
            flow['pending'] = {'id': uuid.uuid4().hex, 'operation': operation, 'args': args,
                               'at': store.stamp(), 'status': 'prepared',
                               'previousPhase': previous_phase, 'previousActiveKey': previous_active,
                               'beforeObservation': flow['lastObservation']['path']}
            flow['events'].append(flow['pending'].copy())
            self._save(state)
            return flow['pending']

    def review_detail(self, data: dict) -> dict:
        evidence = store.required_string(data, 'evidence')
        decision, key = data.get('decision'), data.get('key')
        if decision not in ('apply', 'skipped', 'deferred'):
            raise store.StoreError('invalid-detail-decision')
        with store.transaction(self.root):
            state, flow = self._state()
            if not key or flow['activeKey'] != key or flow['pending'] or flow['phase'] != 'detail':
                raise store.StoreError('loaded-active-detail-required')
            candidate = flow['candidates'][key]
            if decision == 'apply':
                if data.get('eligibilityPassed') is not True:
                    raise store.StoreError('formal-tenure-and-jd-eligibility-review-required')
                self._verify(flow, self._last_page(flow))
            candidate.update(decision=decision, reviewEvidence=evidence,
                             eligibilityPassed=data.get('eligibilityPassed') is True,
                             reviewedProfileFingerprint=flow.get('profileFingerprint'),
                             reviewedPolicyFingerprint=flow.get('policyFingerprint'))
            if decision != 'apply':
                flow.update(activeKey=None, phase='batch')
            self._save(state)
            return {'key': key, 'decision': decision}

    def settled_action(self, action_id: str) -> dict:
        with store.transaction(self.root):
            state, flow = self._state()
            action = state['actions'][action_id]
            key = action['targetKey']
            candidate = flow['candidates'].get(key, {})
            if not candidate or action['status'] == 'pending':
                raise store.StoreError('matching-resolved-outbox-required')
            pending = flow['pending'] or {}
            bound = pending.get('operation') == 'submit' and pending.get('args', {}).get('actionId') == action_id
            context = action.get('browsingContext', {})
            orphan = not pending and flow['activeKey'] == key and context.get('batchId') == (flow.get('batch') or {}).get('id') and (
                context.get('listEvidence') == candidate.get('listEvidence') and context.get('detailEvidence') == candidate.get('detail', {}).get('evidence'))
            late = candidate.get('actionId') == action_id
            if not (bound or orphan or late):
                raise store.StoreError('matching-browser-submit-required')
            candidate.update(decision=action['status'], actionId=action_id)
            if bound or orphan:
                flow.update(activeKey=None, pending=None, phase='batch')
            # These are derived progress fields, never independent delivery receipts.
            day = action['date']
            confirmed = sorted({a['targetKey'] for a in state['actions'].values()
                                if a.get('date') == day and a.get('kind') in ('greet', 'application')
                                and a.get('status') == 'succeeded'})
            for name in ('cityDailyPlan', 'applicationBatch'):
                plan = state['scheduler'].get(name, {})
                if plan.get('date') == day:
                    plan['confirmedCount'] = len(confirmed)
                    if name == 'applicationBatch':
                        plan.update(totalConfirmedToday=len(confirmed), confirmedJobKeys=confirmed)
            self._save(state)
            return {'key': key, 'decision': action['status']}
