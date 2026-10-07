"""Mobile acquisition contracts and incident regressions; no live platform."""
import pytest
from unittest.mock import patch
from android_domain import Binding, Layout, Runtime
import store
pytestmark = [pytest.mark.android, pytest.mark.acquisition]

def test_changed_profile_prevents_reusing_an_archived_decision(case):
    fresh_with_same_job(case)
    (case.root / 'profile.md').write_text('New verified profile facts', encoding='utf-8')
    case.app.collect(1)
    assert not case.app.valid_decision('boss:1')
    judgments = {'judgments': [{'key': 'boss:1', 'apply': False}]}
    with patch('jev.api_key', return_value='fixture'), patch('jev.evaluate_jobs', return_value=judgments) as provider:
        case.app.evaluate()
    provider.assert_called_once()

def fresh_with_same_job(case):
    case.repo.create(Binding('phone', 'Fixture', 'Fixture'), dict(case.app.batch['plan']))
    case.app.batch = case.repo.load()
    case.app.batch['jobs'] = {'boss:1': dict(case.job)}
    case.app.batch['activeKeys'] = ['boss:1']

def test_same_full_jd_in_a_new_batch_reuses_the_original_decision(case):
    original = dict(case.app.batch['decisions']['boss:1'])
    fresh_with_same_job(case)
    case.app.collect(1)
    with patch('jev.api_key', return_value='fixture'), patch('jev.evaluate_jobs', side_effect=AssertionError('Unchanged JD must reuse its decision')) as provider:
        result = case.app.evaluate()
    assert result['providerCalls'] == 0
    assert case.app.batch['decisions']['boss:1'] == original
    provider.assert_not_called()

def test_changed_jd_or_judgment_context_cannot_reuse_an_old_batch_decision(case):
    fresh_with_same_job(case)
    case.app.batch['jobs']['boss:1']['text'] += ' Changed core responsibilities.'
    case.app.collect(1)
    assert not case.app.valid_decision('boss:1')
    result = {'judgments': [{'key': 'boss:1', 'apply': True}]}
    with patch('jev.api_key', return_value='fixture'), patch('jev.evaluate_jobs', return_value=result) as provider:
        case.app.evaluate()
    provider.assert_called_once()

def test_layout_and_authorization_do_not_invalidate_judgment(case):
    case.app.runtime = Runtime('phone', Layout(1080, 2400, '14.171'))
    policy = store.load_policy(case.root)
    policy['authorization']['greet'] = 'draft'
    store.write_json(case.root / 'policy.json', policy)
    assert case.app.valid_decision('boss:1')
    assert case.app.evaluate()['providerCalls'] == 0
    with pytest.raises(ValueError):
        case.app.submit_greeting('boss:1')
    case.device.tap.assert_not_called()
    policy['search']['roleFocus'] = 'Changed direction'
    store.write_json(case.root / 'policy.json', policy)
    assert not case.app.valid_decision('boss:1')

def test_one_batch_provider_call_and_unknown_call_is_not_repeated(case):
    case.app.batch['decisions'] = {}
    result = {'judgments': [{'key': 'boss:1', 'apply': True}]}
    with patch('jev.api_key', return_value='fixture'), patch('jev.evaluate_jobs', return_value=result) as call:
        case.app.evaluate()
        case.app.evaluate()
    call.assert_called_once()
    case.app.batch['decisions'] = {}
    case.app.batch['evaluations'] = {}
    with patch('jev.api_key', return_value='fixture'), patch('jev.evaluate_jobs', side_effect=ConnectionError('lost')) as call:
        with pytest.raises(ConnectionError):
            case.app.evaluate()
        with pytest.raises(RuntimeError, match='do-not-repeat'):
            case.app.evaluate()
    call.assert_called_once()
