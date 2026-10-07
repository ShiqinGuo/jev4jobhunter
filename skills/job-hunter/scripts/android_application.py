"""Mobile use cases: acquisition, one batch judgment, submission and reconciliation."""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
import re
import time
import uuid
from android_domain import Binding, DeviceUnavailable, Evidence, IdentityChanged, LocateStatus, Marker, MobileError, Observation, SubmissionUncertain, TargetUnavailable, digest, jd_digest, judgment_context
from browsing_safety import require_access
from boss_protocol import CODEC_VERSION
from boss_protocol import GREET
from policy_rules import PolicyError, check_target
import jev
import store

class MobileApplication:
    def __init__(self, repository, device, navigator, receipts):
        self.repository, self.device, self.navigator, self.receipts = repository, device, navigator, receipts
        self.root, self.token = repository.root, repository.token
        self.batch = repository.load()
        self.runtime = None
        self.profile = None

    def save(self):
        self.repository.save(self.batch)

    def prepare_action(self, request: dict) -> dict:
        original = self.original_action(request['targetKey'], request['kind'], request.get('inboundId'))
        if original and original.get('submissionStage') == 'prepared':
            fields = ('content', 'contentMode', 'expectedGreeting', 'conversationDigest', 'targetFacts', 'oneShotAuthorization')
            if (original.get('profileFingerprint') != store.profile_fingerprint(self.root)
                    or any(original.get(field) != request.get(field) for field in fields)):
                store.cancel_prepared_mobile_action(self.root, self.token, original['id'],
                    'Unsubmitted draft changed; no click permission was issued. Superseded by a reviewed attempt.')
        return store.begin(self.root, self.token, request, mobile=True)

    def record_receipt_protocol(self, observation: Observation):
        if observation.evidence and not observation.evidence.source_id.startswith('ui-delivery-'):
            self.repository.record_protocol(self.runtime.layout.app_version, 'receipt', 'verified', observation.evidence.source_id)
        for identifier in observation.diagnostics:
            event = store.read_json(self.receipts.directory / (identifier + '.json'))
            if event.get('source') == GREET:
                status = 'unsupported' if event.get('reason') == 'unsupported-key-or-wire-format' else 'unverified-with-diagnostic'
                self.repository.record_protocol(self.runtime.layout.app_version, 'receipt', status, identifier)

    def state(self) -> dict:
        state = store.load_state(self.root)
        store.require_token(state, self.token)
        require_access(state, 'boss')
        if self.batch:
            binding = Binding(**self.batch['binding'])
            context = store.active_account_context(state, 'boss')
            platform = next(p for p in store.load_policy(self.root)['platforms'] if p['id'] == 'boss')
            if platform['accountLabel'] != binding.account_label or (context and context['id'] != binding.account_id):
                raise IdentityChanged('mobile-account-context-changed:history-retained')
        return state

    def marker(self, operation: str, target: str | None = None, action_id: str | None = None) -> Marker:
        binding = Binding(**self.batch['binding'])
        return Marker(self.batch['id'], action_id or uuid.uuid4().hex, binding.account_id,
                      binding.account_label, operation, target, time.time())

    def prepare_search(self, plan: dict, fresh=False):
        self.state()
        self.runtime = self.device.observe_runtime()
        policy = store.load_policy(self.root)
        account = next(p['accountLabel'] for p in policy['platforms'] if p['id'] == 'boss')
        context = store.active_account_context(store.load_state(self.root), 'boss')
        binding = Binding(self.runtime.serial, context.get('id') or account, account)
        if self.batch and Binding(**self.batch['binding']) != binding:
            raise IdentityChanged('device-or-account-changed:verify-explicit-binding')
        self.navigator.verify_account(account)
        plan_fields = ('source', 'city', 'query', 'count')
        existing_plan = {key: self.batch['plan'].get(key) for key in plan_fields} if self.batch else None
        if fresh or not self.batch or existing_plan != plan:
            self.batch = self.repository.create(binding, plan)
        self.navigator.source(plan['source'], plan['query'], policy, self.runtime.layout)
        self.navigator.city(plan['city'], policy)
        self.profile = self.repository.layout(self.runtime.layout.key)
        capabilities_path = self.repository.directory / 'protocol.json'
        capabilities = store.read_json(capabilities_path) if capabilities_path.exists() else {}
        capabilities.setdefault(self.runtime.layout.app_version, {'codecVersion': CODEC_VERSION,
            'list': 'unverified', 'detail': 'unverified', 'receipt': 'unverified'})
        store.write_json(capabilities_path, capabilities)
        return policy

    def accept_list(self, event: dict):
        if event['marker']['context_id'] != self.batch['id'] or event['type'] != 'list':
            raise MobileError('matching-list-event-required')
        added = 0
        for row in event['jobs']:
            key = row['key']
            if key not in self.batch['jobs']:
                self.batch['jobs'][key] = {**row, 'listTitle': row['title'], 'listCompany': row['company'], 'listEvidence': event['id']}
                added += 1
            else:
                self.batch['jobs'][key].update(listTitle=row['title'], listCompany=row['company'], listEvidence=event['id'])
            if key not in self.batch['activeKeys']:
                self.batch['activeKeys'].append(key)
        self.batch.update(lastList=event['id'], hasMore=event['hasMore'])
        self.save()
        if self.runtime:
            self.repository.record_protocol(self.runtime.layout.app_version, 'list', 'verified', event['id'])
        return {'status': 'captured', 'added': added, 'total': len(self.batch['jobs'])}

    def filter_pool(self, policy: dict, salary: str | None):
        self.batch['activeKeys'] = []
        marker = self.marker('search')
        event = self.navigator.filters(self.batch['plan']['source'], policy, salary, marker)
        self.accept_list(event)
        if self.profile is None:
            self.calibrate()

    def scroll_next(self, profile: dict | None = None) -> dict:
        self.state()
        profile = profile or self.profile
        if profile is None:
            raise MobileError('layout-calibration-required')
        if self.batch['hasMore'] is False:
            return {'status': 'end', 'swipes': 0}
        for count in range(1, profile['maxSwipes'] + 1):
            if not self.navigator.cards():
                raise MobileError('job-list-required-before-scroll')
            marker = self.marker('scroll')
            self.receipts.arm(marker)
            self.device.swipe(profile)
            event = self.receipts.wait_material(marker, 'list', seconds=2)
            if event:
                return {**self.accept_list(event), 'swipes': count}
        return {'status': 'no-new-response', 'swipes': profile['maxSwipes'], 'exhausted': False}

    def calibrate(self) -> dict:
        layout = self.runtime.layout
        profile = {'layout': asdict(layout), 'x': round(layout.width * .5), 'startY': round(layout.height * .8875),
                   'endY': round(layout.height * .26875), 'durationMs': 650, 'maxSwipes': 8}
        measurements = []
        for _ in range(2):
            result = self.scroll_next(profile)
            if result['status'] != 'captured' or result['added'] == 0:
                raise MobileError('scroll-calibration-needs-new-response:preserve-batch')
            measurements.append(result['swipes'])
        profile.update(maxSwipes=max(measurements) + 2, measurements=measurements)
        self.repository.save_layout(layout.key, profile)
        self.profile = profile
        return {'status': 'calibrated', 'layout': layout.key, 'measurements': measurements}

    def collect(self, count: int, excluded=()) -> dict:
        state = self.state()
        policy = store.load_policy(self.root)
        held = {a['targetKey'] for a in state['actions'].values() if a['status'] in store.HELD and a['kind'] in ('greet', 'application')}
        for key in list(self.batch['activeKeys']):
            if len(self.batch['selection']) >= count:
                break
            if key in self.batch['selection'] or key in held or key in excluded:
                continue
            job = self.batch['jobs'][key]
            try:
                check_target(policy, state, {'kind': 'greet', 'targetKey': key, 'targetFacts': job}, require_complete=False)
            except PolicyError as exc:
                self.batch['observations'][key] = {'excluded': str(exc)}
                continue
            if not job['complete']:
                located = self.navigator.locate_job(job, self.batch['jobs'], self.profile, self.marker)
                if located.status != LocateStatus.FOUND:
                    self.batch['observations'][key] = {'locate': located.status}
                    self.save()
                    continue
                self.batch['jobs'][key] = located.job
                self.repository.record_protocol(self.runtime.layout.app_version, 'detail', 'verified', located.job['detailEvidence'])
                self.device.back()
            if not self.valid_decision(key):
                cached = self.repository.reusable_decision(Binding(**self.batch['binding']),self.batch['jobs'][key],self.decision_context())
                if cached:
                    self.batch['decisions'][key] = cached
            self.batch['selection'].append(key)
            self.save()
        return {'status': 'collected', 'fullJds': len(self.batch['selection'])}

    def decision_context(self) -> str:
        return judgment_context(store.profile_fingerprint(self.root), store.load_policy(self.root), jev.QUESTION_VERSION)

    def valid_decision(self, key: str) -> bool:
        row = self.batch['decisions'].get(key)
        return bool(row and row['context'] == self.decision_context() and row['jdDigest'] == jd_digest(self.batch['jobs'][key]))

    def evaluate(self) -> dict:
        self.state()
        if self.batch.get('unresolvedLegacyEvaluation'):
            raise MobileError('unresolved-legacy-jev-attempt:inspect-existing-evidence')
        scope = self.batch['selection']
        pending = [self.batch['jobs'][key] for key in scope if not self.valid_decision(key)]
        if not pending:
            return {'status': 'already-evaluated', 'providerCalls': 0, 'count': len(scope)}
        if any(not j['complete'] for j in pending):
            raise MobileError('full-jds-required-for-one-batch-call')
        context = self.decision_context()
        signature = digest({'context': context, 'jobs': [jd_digest(job) for job in pending]})
        attempts = self.batch['evaluations']
        cached = signature in attempts
        if cached:
            attempt = attempts[signature]
            path = Path(attempt['evidence'])
            if not path.exists():
                raise MobileError('jev-result-unknown:do-not-repeat-provider-call')
            result = store.read_json(path)
        else:
            if not jev.api_key():
                raise MobileError('jev-key-unavailable')
            path = self.repository.directory / ('jev-' + uuid.uuid4().hex + '.json')
            attempts[signature] = {'status': 'pending', 'evidence': str(path), 'context': context, 'at': store.stamp()}
            self.save()
            result = jev.evaluate_jobs(self.root, pending)
            store.write_json(path, result)
        if self.decision_context() != context:
            raise MobileError('judgment-context-changed-during-provider-call')
        if {row['key'] for row in result['judgments']} != {job['key'] for job in pending}:
            raise MobileError('provider-job-set-mismatch')
        for row in result['judgments']:
            self.batch['decisions'][row['key']] = {**row, 'context': context,
                'jdDigest': jd_digest(self.batch['jobs'][row['key']]), 'evidence': str(path)}
        attempts[signature]['status'] = 'succeeded'
        self.save()
        return {'status': 'evaluated', 'providerCalls': int(not cached), 'count': len(pending),
                'apply': sum(row['apply'] for row in result['judgments'])}

    def original_action(self, key: str, kind='greet', inbound=None) -> dict | None:
        actions = self.repository.actions(Binding(**self.batch['binding']))
        return next((a for a in actions if a['targetKey'] == key and a['kind'] == kind and a['status'] in store.HELD
                     and (inbound is None or a.get('inboundId') == inbound)), None)

    def reconcile(self, action: dict) -> dict:
        if action['status'] in ('succeeded', 'failed'):
            return action
        if not action.get('marker'):
            # Old unknown is intentionally not assigned the current global capture marker.
            return action
        marker = Marker.from_dict(action['marker'])
        observation = self.receipts.observe(marker, seconds=0)
        if observation.evidence is None:
            proof = self.navigator.delivered_message(action, marker, self.runtime.layout)
            if proof is None and action['kind'] in ('greet', 'reply'):
                try:
                    self.navigator.verify_account(marker.account_label)
                    self.navigator.open_conversation(action['targetFacts'])
                    proof = self.navigator.delivered_message(action, marker, self.runtime.layout)
                except TargetUnavailable as exc:
                    observation = Observation(None, observation.diagnostics + (str(exc),))
            observation = Observation(proof, observation.diagnostics)
        return store.finish_mobile_action(self.root, self.token, action['id'],
                    observation.evidence.to_dict() if observation.evidence else None, observation.diagnostics)

    def submit_greeting(self, key: str) -> dict:
        self.state()
        original = self.original_action(key)
        if original and original.get('submissionStage') != 'prepared':
            return self.reconcile(original)
        if not self.valid_decision(key):
            return {'status': 'review-required', 'targetKey': key}
        job = self.batch['jobs'][key]
        located = self.navigator.locate_job(job, self.batch['jobs'], self.profile, self.marker)
        if located.status != LocateStatus.FOUND:
            self.batch['observations'][key] = {'locate': located.status}
            self.save()
            return {'status': located.status, 'targetKey': key}
        current = located.job
        self.repository.record_protocol(self.runtime.layout.app_version, 'detail', 'verified', current['detailEvidence'])
        if jd_digest(current) != jd_digest(job):
            self.batch['jobs'][key] = current
            self.save()
            self.device.back()
            return {'status': 'review-required', 'targetKey': key}
        tree = self.device.ui()
        if any(n.get('text') == '继续沟通' for n in tree.iter('node')):
            self.batch['observations'][key] = {'alreadyContacted': True}
            self.save()
            self.device.back()
            return {'status': 'already-contacted', 'targetKey': key}
        button = self.device.button('立即沟通', tree)
        policy = store.load_policy(self.root)
        try:
            check_target(policy, self.state(), {'kind': 'greet', 'targetKey': key, 'targetFacts': current})
        except PolicyError as exc:
            if not str(exc).startswith('excluded-'):
                raise
            self.batch['observations'][key] = {'excluded': str(exc)}
            self.save()
            self.device.back()
            return {'status': 'policy-excluded', 'targetKey': key}
        binding = Binding(**self.batch['binding'])
        request = {'kind': 'greet', 'platform': 'boss', 'targetKey': key, 'accountLabel': binding.account_label,
            'accountContextId': binding.account_id, 'contentMode': 'platform-default', 'targetFacts': current,
            'expectedGreeting': policy.get('communication', {}).get('greeting', ''),
            'context': 'Android full JD batch decision; conversation policy reviewed separately.',
            'authorizationEvidence': policy['authorization']['evidence'],
            'browsingContext': {'profileFingerprint': store.profile_fingerprint(self.root)}}
        action = self.prepare_action(request)
        if action.get('submissionStage') != 'prepared':
            return self.reconcile(action)
        marker = self.marker('greet', key, action['id'])
        self.receipts.arm(marker)
        # Only this transaction grants the subsequent sole click.
        action = store.start_mobile_action(self.root, self.token, action['id'], marker.to_dict())
        click_diagnostics = []
        try:
            self.device.tap(button)
        except DeviceUnavailable as exc:
            click_diagnostics.append(str(exc))
        observation = self.receipts.observe(marker)
        self.record_receipt_protocol(observation)
        if observation.evidence is None:
            try:
                proof = self.navigator.delivered_message(action, marker, self.runtime.layout)
            except DeviceUnavailable as exc:
                click_diagnostics.append(str(exc))
                proof = None
            observation = Observation(proof, observation.diagnostics)
        action = store.finish_mobile_action(self.root, self.token, action['id'],
                   observation.evidence.to_dict() if observation.evidence else None, observation.diagnostics + tuple(click_diagnostics))
        if action['status'] in ('succeeded', 'failed'):
            self.navigator.return_to_list()
        return action

    def remaining(self) -> int | None:
        state = self.state()
        policy = store.load_policy(self.root)
        day = store.local_now(policy).date().isoformat()
        manual = state['scheduler'].get('manualApplicationRun', {})
        cap = manual.get('dailyConfirmedCap') if manual.get('date') == day else policy['dailyLimits']['greet']
        if cap is None:
            return None
        held = {a['targetKey'] for a in state['actions'].values() if a['date'] == day and a['platform'] == 'boss'
                and a['kind'] in ('greet', 'application') and a['status'] in store.HELD}
        return max(0, cap - len(held))

    def apply(self, send=False, maximum=None) -> dict:
        self.state()
        keys = [key for key in self.batch['selection'] if self.valid_decision(key)
                and self.batch['decisions'][key]['apply']
                and (not self.original_action(key) or self.original_action(key)['status'] in ('pending', 'unknown'))]
        if not send:
            return {'status': 'dry', 'keys': keys, 'count': len(keys)}
        confirmed = 0
        observations = []
        for key in keys:
            original = self.original_action(key)
            reconcile_only = original and original.get('submissionStage') != 'prepared'
            if not reconcile_only and ((maximum is not None and confirmed >= maximum) or self.remaining() == 0):
                return {'status': 'limit-reached', 'newConfirmed': confirmed, 'observations': observations,
                        'results': self.repository.results(self.batch)}
            result = self.submit_greeting(key)
            observations.append({'key': key, 'status': result['status']})
            if result['status'] == 'succeeded':
                confirmed += 1
            elif result['status'] == 'review-required':
                return {'status': 'review-required', 'newConfirmed': confirmed, 'observations': observations}
            elif result['status'] in ('unknown', 'pending'):
                return {'status': 'unknown', 'newConfirmed': confirmed, 'actionId': result['id']}
        return {'status': 'batch-processed', 'newConfirmed': confirmed, 'observations': observations,
                'results': self.repository.results(self.batch)}

    def run(self, plan: dict, send=False, maximum=None, fresh=False, excluded=()) -> dict:
        started = time.monotonic()
        policy = self.prepare_search(plan, fresh)
        salaries = policy['search']['androidFilters'][plan['source']]['salaryLabels'] if plan['source'] == 'recommendation' else [None]
        for index, salary in enumerate(salaries):
            self.filter_pool(policy, salary)
            target = (plan['count'] * (index + 1) + len(salaries) - 1) // len(salaries)
            for _ in range(8):
                result = self.collect(target, excluded)
                if result['fullJds'] >= target:
                    break
                loaded = self.scroll_next()
                if loaded['status'] in ('end', 'no-new-response'):
                    break
        if not self.batch['selection']:
            return {'status': 'no-readable-candidates', 'exhausted': self.batch['hasMore'] is False}
        decision = self.evaluate()
        submission = self.apply(send, maximum)
        return {'status': submission['status'], 'decision': decision, 'submission': submission,
                'fullJds': len(self.batch['selection']), 'elapsedMs': round((time.monotonic()-started)*1000)}

    def reply_current(self, request: dict, send=False) -> dict:
        """Reviewable draft -> latest conversation check -> one click -> original proof."""
        if request.get('attachments'):
            raise MobileError('native-text-reply-does-not-support-attachments')
        self.state()
        self.runtime = self.device.observe_runtime()
        binding = Binding(**self.batch['binding'])
        if self.runtime.serial != binding.serial:
            raise IdentityChanged('conversation-device-changed')
        request = {**request, 'platform': 'boss', 'kind': 'reply', 'accountLabel': binding.account_label,
                   'accountContextId': binding.account_id,
                   'browsingContext': {'profileFingerprint': store.profile_fingerprint(self.root)}}
        original = self.original_action(request['targetKey'], 'reply', request['inboundId'])
        if original and original.get('submissionStage') != 'prepared':
            return self.reconcile(original)
        snapshot = self.navigator.conversation()
        if snapshot['company'] != request['targetFacts']['company'] or digest(snapshot) != request['conversationDigest']:
            raise MobileError('conversation-changed:review-latest-messages')
        text = request['content']
        request['priorOutboundTexts'] = [re.sub(r'\s+', '', m['text']) for m in snapshot['messages'] if m['outbound']]
        if re.sub(r'\s+', '', text) in request['priorOutboundTexts']:
            return {'status': 'already-present:no-resend'}
        if not send:
            return {'status': 'draft', 'request': request}
        action = self.prepare_action(request)
        if action.get('submissionStage') != 'prepared':
            return self.reconcile(action)
        # The same native connection holds the draft, observes the latest messages,
        # and consumes exactly one permission after the durable start transaction.
        diagnostics = []
        with self.navigator.prepare_text_session(text, action['id']) as native:
            if digest(native.snapshot) != request['conversationDigest']:
                store.cancel_prepared_mobile_action(self.root, self.token, action['id'], 'Conversation changed; no native click permission issued.')
                return {'status': 'review-required', 'conversation': native.snapshot}
            marker = self.marker('reply', request['targetKey'], action['id'])
            self.receipts.arm(marker)
            action = store.start_mobile_action(self.root, self.token, action['id'], marker.to_dict())
            try:
                native.commit()
            except SubmissionUncertain as exc:
                diagnostics.append(str(exc))
        proof = None
        for _ in range(6):
            try:
                proof = self.navigator.delivered_message(action, marker, self.runtime.layout)
            except DeviceUnavailable as exc:
                diagnostics.append(str(exc))
                break
            if proof:
                break
            time.sleep(.5)
        return store.finish_mobile_action(self.root, self.token, action['id'], proof.to_dict() if proof else None, tuple(diagnostics))

    def check_messages(self) -> dict:
        self.state()
        binding = Binding(**self.batch['binding'])
        self.runtime = self.device.observe_runtime()
        if self.runtime.serial != binding.serial:
            raise IdentityChanged('message-device-changed')
        self.navigator.verify_account(binding.account_label)
        result = self.navigator.message_check(self.runtime.layout)
        state = self.state()
        manual = state['scheduler'].get('manualApplicationRun', {})
        day = store.local_now(store.load_policy(self.root)).date().isoformat()
        if manual.get('date') == day:
            count = len({a['targetKey'] for a in state['actions'].values() if a['date'] == day
                         and a['kind'] in ('greet', 'application') and a['status'] == 'succeeded'})
            manual.update(lastMessageCheckCount=count, nextMessageCheckAtCount=count+15, lastMessageCheckAt=store.stamp())
            store.update(self.root, self.token, 'scheduler', 'manualApplicationRun', manual)
        return result

    def open_conversation(self, key: str) -> dict:
        self.state()
        binding = Binding(**self.batch['binding'])
        self.runtime = self.device.observe_runtime()
        if self.runtime.serial != binding.serial:
            raise IdentityChanged('conversation-device-changed')
        self.navigator.verify_account(binding.account_label)
        job = self.repository.target_job(key, binding)
        snapshot = self.navigator.open_conversation(job)
        if snapshot['company'] != job['company']:
            raise TargetUnavailable('opened-conversation-target-mismatch')
        return {**snapshot, 'digest': digest(snapshot), 'targetKey': key}
