"""Mobile communication contracts and incident regressions; no live platform."""
import pytest
from unittest.mock import patch
from android_domain import Evidence, SubmissionUncertain
import store
from android_text import successful_runner_output
pytestmark = [pytest.mark.android, pytest.mark.communication]

def test_text_reply_cannot_claim_an_attachment_was_shared(case):
    request, native = case.reply_request()
    request['attachments'] = [str(case.root / 'resume.pdf')]
    from android_domain import MobileError
    with pytest.raises(MobileError, match='does-not-support-attachments'):
        case.app.reply_current(request, send=True)
    assert not store.load_state(case.root)['actions']
    case.navigator.prepare_text_session.assert_not_called()

def test_confirmed_reply_reuses_original_result_even_after_conversation_changes(case):
    request, native = case.reply_request()
    case.navigator.delivered_message.side_effect = lambda action, marker, layout: Evidence(action['id'], 'Fixture', 'Fixture', 'boss:1', 'ui-delivery-confirmed-reply', 'succeeded', request['content'])
    first = case.app.reply_current(request, send=True)
    case.navigator.conversation.return_value = {'company': case.job['company'], 'messages': [{'text': 'Later HR response', 'outbound': False, 'delivered': False}]}
    second = case.app.reply_current(request, send=True)
    assert first['id'] == second['id']
    assert second['status'] == 'succeeded'
    native.commit.assert_called_once()
    case.navigator.conversation.assert_called_once()

def test_changed_conversation_cancels_prepared_without_commit(case):
    request, native = case.reply_request()
    native.snapshot = {**native.snapshot, 'messages': [{'text': 'New HR question', 'outbound': False, 'delivered': False}]}
    result = case.app.reply_current(request, send=True)
    assert result['status'] == 'review-required'
    native.commit.assert_not_called()
    case.feed.arm.assert_not_called()
    action = next(iter(store.load_state(case.root)['actions'].values()))
    assert action['submissionStage'] == 'not_submitted'

def test_reply_permission_is_durable_and_uncertain_result_is_reconciled_without_reclick(case):
    request, native = case.reply_request()

    def commit():
        action = next(iter(store.load_state(case.root)['actions'].values()))
        assert action['submissionStage'] == 'in_flight'
        assert action['marker']['action_id'] == action['id']
        raise SubmissionUncertain('helper-output-lost')
    native.commit.side_effect = commit
    with patch('android_application.time.sleep'):
        result = case.app.reply_current(request, send=True)
    assert result['status'] == 'unknown'
    case.app.reply_current(request, send=True)
    native.commit.assert_called_once()
    case.navigator.prepare_text_session.assert_called_once()
    assert case.feed.observe.call_args.args[0].action_id == result['id']

def test_uncertain_native_runner_cannot_hide_delivered_ui_evidence(case):
    request, native = case.reply_request()
    native.commit.side_effect = SubmissionUncertain('runner-transport-ended')

    def delivered(action, marker, layout):
        return Evidence(action['id'], 'Fixture', 'Fixture', 'boss:1', 'ui-native-bubble', 'succeeded', request['content'])
    case.navigator.delivered_message.side_effect = delivered
    result = case.app.reply_current(request, send=True)
    assert result['status'] == 'succeeded'
    assert result['diagnostics'] == ['runner-transport-ended']

def test_prepared_draft_changes_create_a_reviewed_unsubmitted_attempt(case):
    request, native = case.reply_request()
    old = store.begin(case.root, case.token, {**request, 'accountLabel': 'Fixture', 'accountContextId': 'Fixture'}, mobile=True)
    request['content'] = '您好，有 Agent 开发经验，可以进一步沟通。'
    with patch('android_application.time.sleep'):
        result = case.app.reply_current(request, send=True)
    state = store.load_state(case.root)
    assert result['id'] != old['id']
    assert state['actions'][old['id']]['submissionStage'] == 'not_submitted'
    assert result['content'] == request['content']
    native.commit.assert_called_once()
RUNNER_SUCCESS = b'INSTRUMENTATION_STATUS: class=jobhunter.NativeText\nINSTRUMENTATION_STATUS: test=testSetText\nINSTRUMENTATION_STATUS_CODE: 0\nOK (1 test)\nINSTRUMENTATION_STATUS_CODE: -1\n'

@pytest.mark.parametrize('output,expected', [(RUNNER_SUCCESS, True), (b'OK (1 test)\nINSTRUMENTATION_STATUS_CODE: -1\n', False), (RUNNER_SUCCESS + b'INSTRUMENTATION_STATUS: stack=AssertionError', False), (RUNNER_SUCCESS + b'FAILURES!!!', False), (b'', False)], ids=['real-success', 'fake-ok', 'method-error', 'junit-failure', 'no-output'])
def test_native_runner_requires_actual_method_execution(output, expected):
    assert successful_runner_output(output) is expected
