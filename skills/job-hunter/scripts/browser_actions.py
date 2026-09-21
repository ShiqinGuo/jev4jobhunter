"""Guarded BOSS single-step browser entry. No bulk loads, arbitrary JS or API calls."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

import store
import boss_page
import boss_chat
import browser_workflows
import detail_groups
import condition_wait
from browsing_safety import Safety
import webbridge_client
from operation_metrics import Measurement, compact
from policy_rules import browser_route

OPERATIONS = ('status', 'resume-context', 'recover-page', 'inspect', 'start-query', 'control', 'capture-list', 'screen',
              'open-detail', 'review-detail', 'submit', 'reconcile', 'scroll',
              'dismiss-receipt', 'ack-quota-notice', 'clear-access-block', 'defer-detail', 'select-account-context', 'finish-list-read',
              'focus-page', 'release-focus', 'open-page', 'select-page', 'restore-filters', 'revisit-candidate', 'inspect-session', 'screenshot', *boss_chat.OPERATIONS, *browser_workflows.OPERATIONS, *detail_groups.OPERATIONS)


class BrowserError(store.StoreError):
    def __init__(self, result: dict, *, read_only: bool):
        self.detail = (result.get('result') or {}).get('error') or {'message': result.get('error', 'response-unavailable')}
        if not isinstance(self.detail, dict):
            self.detail = {'message': str(self.detail)}
        message = str(self.detail.get('message', ''))
        closed = ('tab' in message and 'was closed' in message) or 'navigate first' in message
        self.next_action = 'recover-page' if read_only and closed else ('inspect-connection' if read_only else 'reconcile-do-not-resend')
        super().__init__('browser-read-failed' if read_only else 'browser-outcome-unknown:inspect-do-not-repeat')


class Engine:
    def __init__(self, root: Path, token: str, platform: str, session: str, transport=None):
        if platform != 'boss':
            raise store.StoreError('platform-adapter-unsupported')
        self.safety = Safety(root, token, platform, session)
        self._send = transport or (lambda action, args: webbridge_client.command_file(session, action, args))
        self.measurement = None

    def transport(self, action, args):
        result = self.measurement.transport(self._send, action, args) if self.measurement else self._send(action, args)
        if action == 'list_tabs' and result.get('outcome') == 'returned':
            data=result.get('result',{}).get('data',{})
            if data.get('success') is True and isinstance(data.get('tabs'),list):
                with store.transaction(self.safety.root):
                    state,flow=self.safety._state()
                    registry=flow.setdefault('pageRegistry',{}).setdefault(self.safety.session,{})
                    registry.update(tabs=data['tabs'],inventoryAt=store.stamp())
                    self.safety._save(state)
        return result

    def execute(self, operation: str, data: dict | None = None) -> dict:
        if self.measurement or operation in ('status','resume-context'):
            return self._execute(operation, data)
        # Require the existing run lock before creating operation evidence.
        self.safety._state()
        measurement = self.measurement = Measurement(operation, self.safety.session)
        result, error = {}, None
        try:
            result = self._execute(operation, data)
            return result
        except (ValueError, OSError, KeyError) as failure:
            error = failure
            raise
        finally:
            self.measurement = None
            try:
                metrics = measurement.save(self.safety.root, result, error)
                result['metrics'] = metrics
                if error is not None:
                    error.operation_metrics = metrics
            except OSError:
                # Never make a completed external action look retryable due to telemetry.
                result['metricsWarning'] = 'metrics-unavailable:inspect-outbox-do-not-resend'

    def _call(self, script: str, *, read_only: bool = False) -> dict:
        result = self.transport('evaluate', {'code': script})
        if result.get('outcome') != 'returned':
            raise BrowserError(result, read_only=read_only)
        raw = result.get('result', {}).get('data', {})
        value = raw.get('value')
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, dict):
            raise store.StoreError('unsupported-browser-response')
        return value

    def _inspect(self) -> dict:
        # Fixed DOM-only script; does not scroll, click, navigate, fetch or preload.
        self.safety._state()
        return self.safety.observe(self._call(boss_page.observation_script(), read_only=True))

    def _recover_page(self, data: dict) -> dict:
        """Restore only this Kimi session's BOSS job page; never send or clear history."""
        if data:
            raise store.StoreError('recover-page-takes-no-url-or-other-arguments')
        # Re-selecting an existing task tab is passive recovery, not navigation.
        # Keep access blocks intact until a fresh account observation clears them.
        self.safety._state()
        flow = self.safety.status()['flow']
        if flow.get('focusEmulated'):
            self.execute('release-focus')
        if flow.get('session') and flow['session'] != self.safety.session:
            raise store.StoreError('recovery-must-use-saved-kimi-session')
        if (flow.get('pending') or {}).get('operation') == 'submit':
            raise store.StoreError('reconcile-pending-submit-before-page-recovery')
        listed = self.transport('list_tabs', {})
        if listed.get('outcome') != 'returned':
            raise BrowserError(listed, read_only=True)
        body = (listed.get('result') or {}).get('data') or {}
        tabs = body.get('tabs')
        if body.get('success') is not True or not isinstance(tabs, list):
            raise store.StoreError('invalid-session-tab-inventory')
        urls = []
        for tab in tabs:
            url = tab.get('url', '')
            parts = urlsplit(url)
            if parts.scheme == 'https' and parts.netloc == 'www.zhipin.com' and parts.path.rstrip('/') == '/web/geek/jobs':
                if url not in urls:
                    urls.append(url)
        if len(urls) > 1:
            raise store.StoreError('multiple-job-pages:identify-task-page-before-recovery')
        previous = flow.get('pageRecovery') or {}
        if not urls and previous.get('operation') == 'navigate' and previous.get('status') in ('started', 'unknown'):
            raise store.StoreError('page-recovery-outcome-unknown:inspect-session-before-retrying')
        operation = 'find_tab' if urls else 'navigate'
        if operation == 'navigate':
            self.safety.preflight()
        args = {'url': urls[0]} if urls else {'url': 'https://www.zhipin.com/web/geek/jobs', 'newTab': True, 'group_title': '求职投递'}
        with store.transaction(self.safety.root):
            state, flow = self.safety._state(active=operation == 'navigate')
            flow['pageRecovery'] = {'status': 'started', 'operation': operation,
                                    'session': self.safety.session, 'at': store.stamp()}
            self.safety._save(state)
        result = self.transport(operation, args)
        returned = result.get('outcome') == 'returned' and (result.get('result', {}).get('data') or {}).get('success') is True
        actual_url = (result.get('result', {}).get('data') or {}).get('url')
        if not actual_url or (urlsplit(actual_url).scheme, urlsplit(actual_url).hostname,
                              urlsplit(actual_url).path.rstrip('/')) != ('https', 'www.zhipin.com', '/web/geek/jobs'):
            returned = False
        with store.transaction(self.safety.root):
            state, flow = self.safety._state()
            flow['pageRecovery'].update(status='returned' if returned else 'unknown')
            self.safety._save(state)
        if not returned:
            error = BrowserError(result, read_only=False)
            error.next_action = 'inspect-session-before-retrying-page-recovery'
            raise error
        return {'operation': 'recover-page', 'browser': 'kimi-webbridge', 'session': self.safety.session,
                'step': operation, 'url': args['url'], 'nextAction': 'inspect-and-revalidate-account-and-filters',
                'submissionVerified': False}

    def _execute(self, operation: str, data: dict | None = None) -> dict:
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
            # Do not bury the required route behind thousands of historical candidates.
            checkpoint = {key: flow.get(key) for key in ('query', 'batch', 'phase', 'activeKey',
                          'pending', 'pageRecovery', 'lastObservation', 'backlog', 'retainedBatches', 'focusEmulated', 'detailGroup')}
            return {'browser': route, 'policyFingerprint': store.fingerprint(policy),
                    'session': context.get('session') or flow.get('session'),
                    'platform': status['platform'], 'accessBlock': status['accessBlock'],
                    'accountContext': context, 'flow': checkpoint,
                    'requiredReads': ['SKILL.md', 'references/drivers.md', 'kimi-webbridge/SKILL.md'],
                    'networkCalls': 0}
        if operation == 'recover-page':
            return self._recover_page(data)
        if operation in detail_groups.OPERATIONS:
            return detail_groups.execute(self, operation, data)
        if operation in browser_workflows.OPERATIONS:
            return browser_workflows.execute(self,operation,data)
        if operation in ('inspect-session', 'screenshot'):
            self.safety._state()
            result = self.transport('list_tabs' if operation == 'inspect-session' else 'screenshot', {})
            if result.get('outcome') != 'returned':
                raise BrowserError(result, read_only=True)
            return {'operation':operation, 'session':self.safety.session, 'inventory':result['result'].get('data')}
        if operation in ('open-page', 'select-page'):
            self.safety.preflight()
            if self.safety.status()['flow'].get('focusEmulated'):
                self.execute('release-focus')
            paths = {'jobs':'jobs', 'chat':'chat', 'resume':'resume'}
            detail = operation == 'select-page' and data.get('page') == 'detail'
            if detail:
                import re
                if set(data) != {'page','key'} or not re.fullmatch(r'boss:[A-Za-z0-9_-]+',str(data.get('key',''))):
                    raise store.StoreError('known-boss-detail-key-required')
                url = 'https://www.zhipin.com/job_detail/' + data['key'][5:] + '.html'
            elif set(data) != {'page'} or data['page'] not in paths:
                raise store.StoreError('known-boss-page-required')
            else:
                url = 'https://www.zhipin.com/web/geek/' + paths[data['page']]
            selected_url = url
            select_args = {}
            if operation == 'open-page' and data['page'] == 'jobs':
                listed = self.transport('list_tabs', {})
                if listed.get('outcome') != 'returned':
                    raise BrowserError(listed, read_only=True)
                urls = {t['url'] for t in listed.get('result',{}).get('data',{}).get('tabs',[])
                        if urlsplit(t.get('url','')).scheme == 'https' and
                        urlsplit(t.get('url','')).netloc == 'www.zhipin.com' and
                        urlsplit(t.get('url','')).path.rstrip('/') == '/web/geek/jobs'}
                if len(urls) > 1:
                    raise store.StoreError('multiple-job-queries:inspect-session')
                if urls:
                    selected_url = urls.pop()
            if operation == 'select-page':
                listed = self.transport('list_tabs', {})
                if listed.get('outcome') != 'returned':
                    raise BrowserError(listed, read_only=True)
                matches = [t for t in listed.get('result',{}).get('data',{}).get('tabs',[])
                           if urlsplit(t.get('url','')).scheme == 'https' and
                           urlsplit(t.get('url','')).netloc == 'www.zhipin.com' and
                           urlsplit(t.get('url','')).path.rstrip('/') == urlsplit(url).path]
                if len(matches) != 1:
                    raise store.StoreError('unique-session-page-required:inspect-session')
                selected_url = matches[0]['url']
                if matches[0].get('borrowed'):
                    if not matches[0].get('active'):
                        raise store.StoreError('borrowed-task-page-not-active:inspect-session')
                    select_args['active'] = True
            result = self.transport('find_tab' if operation == 'select-page' else 'navigate', {'url':selected_url, **select_args})
            if result.get('outcome') != 'returned' or result.get('result',{}).get('data',{}).get('success') is not True:
                raise BrowserError(result,read_only=False)
            actual = result.get('result',{}).get('data',{}).get('url')
            if actual and (urlsplit(actual).scheme, urlsplit(actual).netloc, urlsplit(actual).path.rstrip('/')) != ('https','www.zhipin.com',urlsplit(url).path):
                raise store.StoreError('navigation-returned-wrong-page:inspect-session:' + str(actual))
            return {'operation':operation,'url':url,'returnedUrl':actual,'nextAction':'inspect-and-verify-account'}
        if operation == 'release-focus':
            self.safety._state()
            result = self.transport('cdp', {'method':'Emulation.setFocusEmulationEnabled','params':{'enabled':False}})
            if result.get('outcome') != 'returned':
                raise BrowserError(result,read_only=True)
            with store.transaction(self.safety.root):
                state,flow = self.safety._state()
                flow['focusEmulated']=False
                self.safety._save(state)
            return {'operation':operation,'focusEmulated':False}
        if operation == 'focus-page':
            self.safety.preflight()
            if self.measurement:
                self.measurement.recoveries.append({'kind':'focus-page'})
            emulated = self.transport('cdp', {'method':'Emulation.setFocusEmulationEnabled','params':{'enabled':True}})
            if emulated.get('outcome') != 'returned':
                raise BrowserError(emulated,read_only=True)
            with store.transaction(self.safety.root):
                state,flow = self.safety._state()
                flow['focusEmulated']=True
                self.safety._save(state)
            result = self.transport('cdp', {'method': 'Page.bringToFront', 'params': {}})
            if result.get('outcome') != 'returned' or result.get('result', {}).get('ok') is False:
                raise BrowserError(result, read_only=True)
            return {'operation': operation, 'focusEmulated':True, 'observation': self._inspect()}
        if operation in boss_chat.OPERATIONS:
            return boss_chat.execute(self, operation, data)
        if operation == 'inspect':
            return self._inspect()
        if operation in ('start-query', 'restore-filters', 'revisit-candidate', 'screen', 'review-detail', 'clear-access-block', 'defer-detail', 'select-account-context', 'finish-list-read'):
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
        prior_controls = []
        flow = self.safety.status()['flow']
        if operation == 'control' and flow.get('lastObservation'):
            prior_controls = self.safety._last_page(flow).get('controls', [])
        before = self._inspect()
        prior = next((c for c in prior_controls if c.get('id') == data.get('id')), {})
        if (operation == 'control' and prior.get('kind') == 'filter-option' and
                data.get('id') not in {c['id'] for c in before['page'].get('controls', [])}):
            menus = [c for c in before['page'].get('controls', []) if c.get('kind') == 'filter-menu'
                     and c.get('filter') == prior.get('filter')]
            if len(menus) == 1:
                # One bounded re-expansion of the same observed menu. Never retry a send.
                if self.measurement:
                    self.measurement.recoveries.append({'kind':'reopen-menu','filter':prior.get('filter')})
                # Re-open the same menu the way that menu actually opens.
                reopen = {'id':menus[0]['id']}
                if menus[0].get('interaction') != 'click':
                    reopen['interaction'] = 'hover'
                recovered = self.execute('control', reopen)
                if 'observation' not in recovered:
                    return {**recovered, 'nextAction':'observe-and-reopen-current-menu'}
                before = recovered['observation']
        if (operation in ('open-detail','scroll') or (operation == 'control' and data.get('interaction') == 'hover')) and before['page'].get('visibility') == 'hidden':
            self.execute('focus-page')
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
            open_filters = {c.get('filter') for c in before['page'].get('controls', [])
                            if c.get('kind') == 'filter-option' and c.get('filter')}
            target_filter = next((c.get('filter') for c in before['page'].get('controls', [])
                                  if c.get('id') == data.get('id')), None)
            if open_filters and target_filter not in open_filters:
                # A menu that is already open swallows the next trigger's hover,
                # so leave it before moving onto the target. One bounded move,
                # no click: closing a menu must never select an option.
                if self.measurement:
                    self.measurement.recoveries.append({'kind': 'clear-open-menu',
                                                        'filters': sorted(open_filters)})
                self.transport('cdp', {'method': 'Input.dispatchMouseEvent', 'params': {
                    'type': 'mouseMoved', 'x': 1, 'y': 1}})
            moved = self.transport('cdp', {'method': 'Input.dispatchMouseEvent', 'params': {
                'type': 'mouseMoved', 'x': result['x'], 'y': result['y']}})
            if moved.get('outcome') != 'returned' or moved.get('result', {}).get('ok') is False:
                raise store.StoreError('browser-outcome-unknown:inspect-do-not-repeat')
            result = {**result, 'status': 'hovered'}
        observation = self._inspect()
        if result.get('status') == 'hovered':
            owner = next((c.get('filter') for c in observation['page'].get('controls', []) if c.get('id') == data.get('id')), None)
            result['menuOpened'] = bool(owner) and any(c.get('kind') == 'filter-option' and c.get('filter') == owner
                                      for c in observation['page'].get('controls', []))
            if not result['menuOpened']:
                # Say what to do next instead of leaving a bare false behind.
                result['nextAction'] = 'observe-and-reopen-current-menu'
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
            # An ambiguous click is never retried. Only look for its receipt.
            return self._reconcile({'actionId': action_id})
        if result.get('status') == 'unsupported':
            store.resolve(self.safety.root, self.safety.token, action_id, 'failed', 'Driver reported no click: ' + result.get('reason', 'unsupported'))
            self.safety.settled_action(action_id)
            return {'id': action_id, 'status': 'failed', 'step': result}
        return self._reconcile({'actionId': action_id})

    def _reconcile(self, data: dict) -> dict:
        timeout = data.get('waitMs', 4000)
        if type(timeout) is not int or not 0 <= timeout <= 10000:
            raise store.StoreError('waitMs-must-be-integer-between-0-and-10000')
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
            waited = condition_wait.observe(self, boss_page.observation_script(), '''
return page.accountLabel===args.accountLabel && page.detail?.key===args.targetKey &&
  page.body.includes('已向BOSS发送消息');
''', {'accountLabel': action.get('accountLabel'), 'targetKey': action['targetKey']},
                timeout, stable_samples=1)
            observed = waited['observation']
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
            if (observed['classification'] == 'normal' and context_matches and page.get('accountLabel') == action.get('accountLabel')
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
        return {'id': action_id, 'status': status, 'evidence': evidence,
                'nextAction': 'none' if status == 'succeeded' else 'inspect-chat-and-reconcile-no-resend'}

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
    parser.add_argument('--output', choices=('compact','full'), default='compact')
    args = parser.parse_args()
    try:
        if args.operation not in ('status', 'resume-context') and (not args.token or not args.session):
            raise store.StoreError('run-token-and-browser-session-required')
        engine = Engine(args.data_dir.expanduser(), args.token, args.platform, args.session)
        result = engine.execute(args.operation, store.read_json(args.file) if args.file else {})
        print(json.dumps(compact(result) if args.output == 'compact' else result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError) as error:
        details = {'cause': error.detail, 'nextAction': error.next_action} if isinstance(error, BrowserError) else {}
        if hasattr(error,'operation_metrics'):
            details['metrics']=error.operation_metrics
        print(json.dumps({'error': str(error), **details, 'noAutomaticRetry': True}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
