"""Mobile batch/layout persistence and explicit v1 -> v3 migration."""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
import uuid
from android_domain import Binding, MobileError, TargetUnavailable, digest, jd_digest, judgment_context
import jev
import store
from boss_protocol import CODEC_VERSION

SCHEMA = 3

class MobileRepository:
    def __init__(self, root: Path, token: str):
        self.root, self.token = root, token
        self.directory = root / 'android'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'batch.json'
        self.decision_index = None

    def load(self) -> dict | None:
        if not self.path.exists():
            return None
        batch = store.read_json(self.path)
        if batch.get('schema') != SCHEMA:
            raise MobileError('mobile-schema-migration-required:run-migrate')
        for field in ('jobs', 'decisions', 'observations'):
            if not isinstance(batch.get(field), dict):
                raise MobileError('invalid-mobile-batch:' + field)
        Binding(**batch['binding'])
        return batch

    def save(self, batch: dict):
        store.require_token(store.load_state(self.root), self.token)
        if batch['schema'] != SCHEMA:
            raise MobileError('invalid-mobile-write-schema')
        store.write_json(self.path, batch)

    def create(self, binding: Binding, plan: dict) -> dict:
        old = self.load()
        if old:
            archive = self.directory / 'batches'
            archive.mkdir(exist_ok=True)
            store.write_json(archive / (old['id'] + '.json'), old)
            self.decision_index = None
        batch = {'schema': SCHEMA, 'id': uuid.uuid4().hex, 'binding': asdict(binding),
                 'plan': plan, 'jobs': {}, 'decisions': {}, 'observations': {},
                 'activeKeys': [], 'selection': [], 'evaluations': {}, 'hasMore': None}
        self.save(batch)
        return batch

    def layout(self, key: str) -> dict | None:
        path = self.directory / 'layouts.json'
        return store.read_json(path).get(key) if path.exists() else None

    def save_layout(self, key: str, profile: dict):
        path = self.directory / 'layouts.json'
        profiles = store.read_json(path) if path.exists() else {}
        profiles[key] = profile
        store.write_json(path, profiles)

    def record_protocol(self, version: str, capability: str, status: str, source: str):
        """Protocol success is recorded only from a decoded event, never a UI bubble."""
        path = self.directory / 'protocol.json'
        versions = store.read_json(path) if path.exists() else {}
        current = versions.setdefault(version, {'codecVersion': CODEC_VERSION,
            'list': 'unverified', 'detail': 'unverified', 'receipt': 'unverified'})
        if current['codecVersion'] != CODEC_VERSION:
            current = versions[version] = {'codecVersion': CODEC_VERSION,
                'list': 'unverified', 'detail': 'unverified', 'receipt': 'unverified'}
        current[capability] = status
        current.setdefault('evidence', {})[capability] = {'source': source, 'at': store.stamp()}
        try:
            store.write_json(path, versions)
        except OSError:
            # This capability summary is a derived diagnostic, not the action authority.
            return False
        return True

    def actions(self, binding: Binding) -> list[dict]:
        state = store.load_state(self.root)
        store.require_token(state, self.token)
        return [action for action in state['actions'].values()
                if action.get('platform') == 'boss' and action.get('accountLabel') == binding.account_label
                and (not action.get('accountContextId') or action['accountContextId'] == binding.account_id)]

    def results(self, batch: dict) -> dict:
        actions = self.actions(Binding(**batch['binding']))
        return {action['targetKey']: action['status'] for action in actions
                if action['targetKey'] in batch['jobs'] and action['kind'] in ('greet', 'application')
                and action['status'] in store.HELD}

    def target_job(self, key: str, binding: Binding) -> dict:
        current = (self.load() or {}).get('jobs', {}).get(key)
        if current and current.get('complete'):
            return current
        owned = sorted(self.actions(binding), key=lambda row: row.get('at',''), reverse=True)
        for action in owned:
            facts = action.get('targetFacts', {})
            if action['targetKey'] == key and facts.get('key') == key and facts.get('complete'):
                return facts
        archives = self.directory / 'batches'
        for path in sorted(archives.glob('*.json'), key=lambda path: path.stat().st_mtime, reverse=True):
            batch = store.read_json(path)
            if batch.get('schema') != SCHEMA or batch.get('binding') != asdict(binding):
                continue
            job = batch.get('jobs', {}).get(key)
            if job and job.get('complete'):
                return job
        if current:
            return current
        raise TargetUnavailable('owned-target-facts-not-found:' + key)

    def reusable_decision(self, binding: Binding, job: dict, context: str) -> dict | None:
        """An index of completed judgments; archived batches remain the source."""
        if self.decision_index is None:
            path = self.directory / 'judgments.json'
            index = store.read_json(path) if path.exists() else {'schema':1,'batches':[],'decisions':{}}
            if index.get('schema') != 1:
                raise MobileError('unsupported-judgment-index-schema')
            indexed = set(index['batches'])
            archives = sorted((self.directory/'batches').glob('*.json'),key=lambda path:path.stat().st_mtime)
            changed = False
            for archive in archives:
                if archive.stem in indexed:
                    continue
                batch = store.read_json(archive)
                if batch.get('schema') != SCHEMA:
                    continue
                owner = Binding(**batch['binding'])
                for key,row in batch['decisions'].items():
                    facts = batch['jobs'].get(key)
                    if facts and type(row.get('apply')) is bool and row.get('jdDigest') == jd_digest(facts):
                        identity = digest([owner.account_id,owner.account_label,key])
                        index['decisions'][identity] = row
                index['batches'].append(archive.stem)
                changed = True
            self.decision_index = index
            if changed:
                try:
                    store.write_json(path,index)
                except OSError:
                    pass  # Derived cache loss never causes a new provider call in this run.
        identity = digest([binding.account_id,binding.account_label,job['key']])
        row = self.decision_index['decisions'].get(identity)
        if row and row['context'] == context and row['jdDigest'] == jd_digest(job):
            return dict(row)
        return None

def migrate_mobile(root: Path, token: str) -> dict:
    """Idempotent one-time migration. Never infer a lost original click marker."""
    path = root / 'android' / 'batch.json'
    if not path.exists():
        return {'status': 'no-mobile-batch'}
    old = store.read_json(path)
    if old.get('schema') == SCHEMA:
        return {'status': 'already-migrated'}
    for name in ('contextId', 'accountLabel', 'signature', 'jobs', 'decisions'):
        if name not in old:
            raise MobileError('unsupported-legacy-mobile-batch:' + name)
    policy = store.load_policy(root)
    context = judgment_context(store.profile_fingerprint(root), policy, jev.QUESTION_VERSION)
    current_context = (old.get('policyFingerprint') == store.fingerprint(policy)
                       and old.get('profileFingerprint') == store.profile_fingerprint(root))
    decisions = {key: {**row, 'jdDigest': jd_digest(old['jobs'][key]), 'context': context if current_context else 'stale'}
                 for key, row in old['decisions'].items() if key in old['jobs'] and old['jobs'][key].get('complete')}
    batch = {'schema': SCHEMA, 'id': old['contextId'],
             'binding': {'serial': old['signature']['serial'], 'account_id': old.get('accountContextId') or old['accountLabel'],
                         'account_label': old['accountLabel']},
             'plan': {key: old.get('plan', {}).get(key) for key in ('source', 'city', 'query', 'count')},
             'jobs': old['jobs'], 'decisions': decisions,
             'observations': {key: {'locate': value} for key, value in old.get('results', {}).items() if value == 'not-located'},
             'activeKeys': old.get('activeKeys', []), 'selection': old.get('selection', []),
             'evaluations': {}, 'hasMore': old.get('hasMore')}
    # Completed decisions retain their evidence; an unresolved old provider call must not be repeated.
    attempt = old.get('evaluation')
    if attempt and attempt.get('status') == 'pending':
        batch['unresolvedLegacyEvaluation'] = attempt
    with store.transaction(root):
        state = store.load_state(root)
        store.require_token(state, token)
        changed = 0
        for action in state['actions'].values():
            if action.get('platform') != 'boss' or 'Android' not in action.get('context', '') or action.get('driver') == 'android':
                continue
            action.update(driver='android', marker=None, diagnostics=[],
                          submissionStage='legacy_unknown' if action['status'] in ('pending', 'unknown') else action['status'])
            if action['status'] == 'pending':
                action['status'] = 'unknown'
                action['events'].append({'at': store.stamp(), 'status': 'unknown', 'evidence': 'Migration: no durable original marker; no resend.'})
            changed += 1
        state.setdefault('mobileMigrations', {})['schema3'] = {'at': store.stamp(), 'batchId': batch['id']}
        store.write_json(root / 'state.json', state)
    store.write_json(path, batch)
    (root / 'android' / 'scroll.json').unlink(missing_ok=True)
    return {'status': 'migrated', 'fullJds': sum(j.get('complete', False) for j in batch['jobs'].values()),
            'decisions': len(decisions), 'actionsMigrated': changed, 'providerCalls': 0}
