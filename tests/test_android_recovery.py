"""Mobile recovery contracts and incident regressions; no live platform."""
import pytest
import json
from unittest.mock import patch
from android_domain import Binding, Evidence, Layout, Runtime
from android_repository import migrate_mobile
import store
pytestmark = [pytest.mark.android, pytest.mark.recovery]

def test_opening_previous_batch_conversation_uses_owned_action_facts(case):
    original = case.start(case.prepare())
    case.repo.create(Binding('phone', 'Fixture', 'Fixture'), dict(case.app.batch['plan']))
    case.app.batch = case.repo.load()
    case.navigator.open_conversation.return_value = {'company': case.job['company'], 'messages': []}
    result = case.app.open_conversation('boss:1')
    assert result['targetKey'] == 'boss:1'
    case.navigator.open_conversation.assert_called_once_with(original['targetFacts'])
    case.device.tap.assert_not_called()

def test_previous_batch_unknown_reconciles_its_original_conversation_without_resending(case):
    original = case.start(case.prepare())
    original = store.finish_mobile_action(case.root, case.token, original['id'], None)
    case.repo.create(Binding('phone', 'Fixture', 'Fixture'), dict(case.app.batch['plan']))
    case.app.batch = case.repo.load()
    proof = Evidence(original['id'], 'Fixture', 'Fixture', 'boss:1', 'ui-delivery-old-batch', 'succeeded')
    case.navigator.delivered_message.side_effect = [None, proof]
    result = case.app.reconcile(original)
    assert result['status'] == 'succeeded'
    case.navigator.open_conversation.assert_called_once_with(original['targetFacts'])
    assert case.feed.observe.call_args.args[0].context_id == original['marker']['context_id']
    case.device.tap.assert_not_called()

def test_layout_change_recalibrates_and_retains_business_state_without_jev(case):
    original = case.start(case.prepare())
    before = json.loads(json.dumps(case.app.batch))
    case.device.observe_runtime.return_value = Runtime('phone', Layout(1080, 2400, '14.171'))
    case.app.prepare_search(before['plan'])
    assert case.app.profile is None
    with patch.object(case.app, 'scroll_next', side_effect=[{'status': 'captured', 'added': 2, 'swipes': 1}, {'status': 'captured', 'added': 3, 'swipes': 2}]):
        case.app.calibrate()
    assert case.app.batch['id'] == before['id']
    assert case.app.batch['jobs'] == before['jobs']
    assert case.app.batch['decisions'] == before['decisions']
    assert store.load_state(case.root)['actions'][original['id']]['submissionStage'] == 'in_flight'
    assert case.app.evaluate()['providerCalls'] == 0
    assert case.repo.layout('1080x2400@14.171') is not None

def test_old_empty_exclusions_do_not_create_new_batch_or_lose_judgments(case):
    plan = dict(case.app.batch['plan'])
    case.app.batch['plan']['excluded'] = []
    original = case.app.batch['id']
    case.app.prepare_search(plan)
    assert case.app.batch['id'] == original
    assert case.app.valid_decision('boss:1')

def test_explicit_migration_preserves_jds_decisions_and_unknown(case):
    action = case.prepare()
    state = store.load_state(case.root)
    state['actions'][action['id']].pop('driver')
    state['actions'][action['id']].pop('marker')
    store.write_json(case.root / 'state.json', state)
    old = {'contextId': 'old', 'accountLabel': 'Fixture', 'signature': {'serial': 'phone'}, 'jobs': {'boss:1': case.job}, 'decisions': {'boss:1': {'apply': True}}, 'results': {'boss:1': 'unknown'}, 'policyFingerprint': store.fingerprint(store.load_policy(case.root)), 'profileFingerprint': store.profile_fingerprint(case.root)}
    store.write_json(case.repo.path, old)
    result = migrate_mobile(case.root, case.token)
    assert (result['fullJds'], result['decisions'], result['providerCalls']) == (1, 1, 0)
    assert 'results' not in case.repo.load()
    migrated = store.load_state(case.root)['actions'][action['id']]
    assert migrated['status'] == 'unknown'
    assert migrated['marker'] is None
    assert migrate_mobile(case.root, case.token)['status'] == 'already-migrated'
