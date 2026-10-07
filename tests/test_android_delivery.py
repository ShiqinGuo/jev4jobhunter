"""Mobile delivery contracts and incident regressions; no live platform."""
import pytest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from android_domain import Evidence, Observation
import store
pytestmark = [pytest.mark.android, pytest.mark.delivery]

def test_reserved_quota_does_not_block_reconciling_the_original_unknown(case):
    action = case.start(case.prepare())
    store.finish_mobile_action(case.root, case.token, action['id'], None)
    state = store.load_state(case.root)
    state['scheduler']['manualApplicationRun'] = {'date': action['date'], 'dailyConfirmedCap': 1}
    store.write_json(case.root / 'state.json', state)
    proof = Evidence(action['id'], 'Fixture', 'Fixture', 'boss:1', 'ui-delivery-original', 'succeeded')
    case.feed.observe.return_value = Observation(proof)
    result = case.app.apply(send=True, maximum=1)
    assert result['newConfirmed'] == 1
    case.device.tap.assert_not_called()

def test_protocol_summary_failure_cannot_hide_confirmed_delivery(case):
    original_write = store.write_json

    def write(path, value):
        if Path(path).name == 'protocol.json':
            raise OSError('diagnostic disk full')
        return original_write(path, value)

    def delivered(action, marker, layout):
        return Evidence(action['id'], 'Fixture', 'Fixture', 'boss:1', 'ui-delivery-confirmed', 'succeeded')
    case.navigator.delivered_message.side_effect = delivered
    with patch('store.write_json', side_effect=write):
        result = case.app.submit_greeting('boss:1')
    assert result['status'] == 'succeeded'

def test_atomic_prepare_returns_one_original_action(case):
    first = case.prepare()
    second = case.prepare()
    assert first['id'] == second['id']
    assert first['submissionStage'] == 'prepared'
    assert len(store.load_state(case.root)['actions']) == 1

def test_concurrent_start_grants_only_one_click_boundary(case):
    action = case.prepare()

    def start():
        try:
            return case.start(action)['submissionStage']
        except store.StoreError:
            return 'rejected'
    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(lambda _: start(), range(2)))
    assert outcomes.count('in_flight') == 1
    assert outcomes.count('rejected') == 1

def test_unknown_reserves_manual_day_quota(case):
    action = case.start(case.prepare())
    store.finish_mobile_action(case.root, case.token, action['id'], None)
    state = store.load_state(case.root)
    state['scheduler']['manualApplicationRun'] = {'date': action['date'], 'dailyConfirmedCap': 1}
    store.write_json(case.root / 'state.json', state)
    assert case.app.remaining() == 0

def test_durable_marker_exists_before_click_and_unknown_is_never_reclicked(case):

    def click(_):
        actions = list(store.load_state(case.root)['actions'].values())
        assert len(actions) == 1
        action = actions[0]
        assert action['submissionStage'] == 'in_flight'
        assert action['marker']['action_id'] == action['id']
    case.device.tap.side_effect = click
    result = case.app.submit_greeting('boss:1')
    assert result['status'] == 'unknown'
    case.app.submit_greeting('boss:1')
    case.device.tap.assert_called_once()

def test_crash_after_start_before_click_never_reclicks(case):
    action = case.start(case.prepare())
    case.app.submit_greeting('boss:1')
    case.device.tap.assert_not_called()
    assert store.load_state(case.root)['actions'][action['id']]['status'] == 'unknown'

def test_click_transport_error_retains_in_flight(case):
    case.device.tap.side_effect = RuntimeError('process lost after possible click')
    with pytest.raises(RuntimeError, match='process lost'):
        case.app.submit_greeting('boss:1')
    action = next(iter(store.load_state(case.root)['actions'].values()))
    assert action['submissionStage'] == 'in_flight'
    case.device.tap.side_effect = None
    case.app.submit_greeting('boss:1')
    assert case.device.tap.call_count == 1

def test_original_marker_survives_global_marker_overwrite(case):
    action = case.start(case.prepare())
    case.feed.arm(case.app.marker('scroll'))
    case.app.reconcile(action)
    marker = case.feed.observe.call_args.args[0]
    assert marker.action_id == action['id']
    assert marker.target_key == 'boss:1'

def test_evidence_identity_terminal_conflict_and_count_rebuild(case):
    action = case.start(case.prepare())
    wrong = Evidence(action['id'], 'Other', 'Fixture', 'boss:1', 'receipt', 'succeeded')
    with pytest.raises(ValueError, match='identity-mismatch'):
        store.finish_mobile_action(case.root, case.token, action['id'], wrong.to_dict())
    proof = Evidence(action['id'], 'Fixture', 'Fixture', 'boss:1', 'receipt', 'succeeded')
    store.finish_mobile_action(case.root, case.token, action['id'], proof.to_dict(), ('decode-error',))
    unchanged = store.finish_mobile_action(case.root, case.token, action['id'], None, ('late-error',))
    assert unchanged['status'] == 'succeeded'
    assert case.repo.results(case.app.batch) == {'boss:1': 'succeeded'}
    with pytest.raises(ValueError, match='conflicting-terminal'):
        store.finish_mobile_action(case.root, case.token, action['id'], {**proof.to_dict(), 'outcome': 'failed'})

def test_store_failure_after_evidence_leaves_original_for_reconciliation(case):
    action = case.start(case.prepare())
    proof = Evidence(action['id'], 'Fixture', 'Fixture', 'boss:1', 'receipt', 'succeeded')
    with patch('store.write_json', side_effect=OSError('disk full')):
        with pytest.raises(OSError):
            store.finish_mobile_action(case.root, case.token, action['id'], proof.to_dict())
    assert store.load_state(case.root)['actions'][action['id']]['submissionStage'] == 'in_flight'
    store.finish_mobile_action(case.root, case.token, action['id'], proof.to_dict())
    case.app.submit_greeting('boss:1')
    case.device.tap.assert_not_called()
