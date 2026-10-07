"""Mobile search -> full JD -> one Jev batch -> durable delivery -> message trial."""
import pytest
from unittest.mock import patch
from android_domain import Evidence, LocatedJob, LocateStatus
import store
pytestmark = [pytest.mark.android, pytest.mark.workflow]

def setup_pipeline(case):
    first = dict(case.job)
    second = {**first, 'key': 'boss:2', 'jobId': '2', 'title': 'Python API', 'company': 'Second company', 'detailEvidence': 'detail-2'}
    case.full = {row['key']: row for row in (first, second)}
    case.app.batch.update(jobs={}, selection=[], decisions={})
    case.app.save()
    case.repo.save_layout(case.app.runtime.layout.key, case.app.profile)

    def filtered(source, policy, salary, marker):
        return {'type': 'list', 'id': 'search-1', 'marker': marker.to_dict(), 'hasMore': True, 'jobs': [{**row, 'text': 'List abstract', 'complete': False} for row in case.full.values()]}
    case.navigator.filters.side_effect = filtered
    case.navigator.locate_job.side_effect = lambda job, *_: LocatedJob(LocateStatus.FOUND, case.full[job['key']])
    case.navigator.delivered_message.side_effect = lambda action, marker, layout: Evidence(action['id'], marker.account_id, marker.account_label, action['targetKey'], 'ui-delivery-' + action['id'], 'succeeded', action.get('content'))
    judgments = {'judgments': [{'key': key, 'apply': True} for key in case.full]}
    return judgments

def test_authorized_main_flow_uses_full_jds_once_and_repeating_it_adds_no_click(case):
    judgments = setup_pipeline(case)
    plan = dict(case.app.batch['plan'])
    with patch('jev.api_key', return_value='fixture-key'), patch('jev.evaluate_jobs', return_value=judgments) as jev_call:
        result = case.app.run(plan, send=True, maximum=2)
        again = case.app.run(plan, send=True, maximum=2)
    jev_call.assert_called_once()
    full_input = jev_call.call_args.args[1]
    assert {row['key'] for row in full_input} == {'boss:1', 'boss:2'}
    assert all((row['complete'] and row['text'] != 'List abstract' for row in full_input))
    assert result['submission']['newConfirmed'] == 2
    assert again['decision']['providerCalls'] == 0
    assert again['submission']['newConfirmed'] == 0
    assert case.device.tap.call_count == 2
    assert len(store.load_state(case.root)['actions']) == 2
    request, native = case.reply_request()
    reply = case.app.reply_current(request, send=True)
    assert reply['status'] == 'succeeded'
    native.commit.assert_called_once()
    state = store.load_state(case.root)
    assert sum((a['kind'] == 'greet' and a['status'] == 'succeeded' for a in state['actions'].values())) == 2

def test_preview_collects_and_judges_without_creating_any_send_action(case):
    judgments = setup_pipeline(case)
    with patch('jev.api_key', return_value='fixture-key'), patch('jev.evaluate_jobs', return_value=judgments):
        result = case.app.run(dict(case.app.batch['plan']), send=False)
    assert result['status'] == 'dry'
    assert result['fullJds'] == 2
    case.device.tap.assert_not_called()
    assert not store.load_state(case.root)['actions']

def test_unlocatable_candidate_does_not_end_application_of_other_valid_jobs(case):
    case.app.batch['jobs']['boss:2'] = {**case.job, 'key': 'boss:2', 'company': 'Second company'}
    case.app.batch['selection'].append('boss:2')
    case.app.batch['decisions']['boss:2'] = {**case.app.batch['decisions']['boss:1'], 'key': 'boss:2'}
    from android_domain import jd_digest
    case.app.batch['decisions']['boss:2']['jdDigest'] = jd_digest(case.app.batch['jobs']['boss:2'])
    case.navigator.locate_job.side_effect = [LocatedJob(LocateStatus.NOT_VISIBLE), LocatedJob(LocateStatus.FOUND, case.app.batch['jobs']['boss:2'])]
    case.navigator.delivered_message.side_effect = lambda action, marker, layout: Evidence(action['id'], marker.account_id, marker.account_label, action['targetKey'], 'ui-delivery-second', 'succeeded')
    result = case.app.apply(send=True, maximum=2)
    assert result['newConfirmed'] == 1
    assert result['observations'] == [{'key': 'boss:1', 'status': 'not-visible'}, {'key': 'boss:2', 'status': 'succeeded'}]
    case.device.tap.assert_called_once()

def test_daily_cap_is_shared_across_batches_and_message_check_records_actual_count(case):
    setup_pipeline(case)
    day = store.local_now(store.load_policy(case.root)).date().isoformat()
    store.update(case.root, case.token, 'scheduler', 'manualApplicationRun', {'date': day, 'dailyConfirmedCap': 1})
    result = {'judgments': [{'key': key, 'apply': True} for key in case.full]}
    with patch('jev.api_key', return_value='fixture-key'), patch('jev.evaluate_jobs', return_value=result):
        case.app.run(dict(case.app.batch['plan']), send=True, maximum=2)
    assert case.device.tap.call_count == 1
    case.navigator.message_check.return_value = {'status': 'checked', 'rows': [], 'recentPages': 1, 'atEnd': False, 'possibleIncoming': 0}
    case.app.check_messages()
    manual = store.load_state(case.root)['scheduler']['manualApplicationRun']
    assert manual['lastMessageCheckCount'] == 1
    assert case.app.remaining() == 0
