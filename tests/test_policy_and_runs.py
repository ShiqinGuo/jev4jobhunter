from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'job-hunter' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import store
import run_ledger
import doctor
import webbridge_client


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        store.initialize(self.root)
        self.token = store.run_lock(self.root, 'acquire')['token']

    def configure(self, **changes):
        p = store.load_policy(self.root)
        p.update(changes)
        store.write_json(self.root / 'policy.json', p)

    def allow(self):
        self.configure(authorization={**{k: 'allow' for k in store.KINDS}, 'evidence': 'Fixture authorization'})

    def request(self, **changes):
        return dict(kind='reply', platform='boss', targetKey='boss:thread-1', inboundId='msg-1',
                    content='您好，我使用 Python。', context='Fixture recruiter question',
                    authorizationEvidence='Fixture explicit authorization', **changes)

    def one_shot(self, request):
        return {**{k: request.get(k) for k in ('kind', 'platform', 'targetKey', 'inboundId')},
                'evidence': 'Fixture user selected this reply'}


class PolicyTests(Fixture):
    def test_platform_default_greet_reserves_without_inventing_message_content(self):
        request = self.request()
        request.update(kind='greet', contentMode='platform-default', content=None)
        request['oneShotAuthorization'] = self.one_shot(request)
        action = store.begin(self.root, self.token, request)
        saved = store.load_state(self.root)['actions'][action['id']]
        self.assertIsNone(saved['content'])
        self.assertIsNone(saved['observedContent'])
        self.assertIsNone(saved['audit'])
        self.assertEqual(saved['contentMode'], 'platform-default')
        self.assertTrue(store.check_action(self.root, self.token, action['id'])['ready'])
        self.assertEqual(store.load_policy(self.root)['authorization']['greet'], 'draft')
        with self.assertRaisesRegex(store.StoreError, 'duplicate-or-unresolved'):
            store.begin(self.root, self.token, request)

    def test_platform_default_keeps_authorization_required(self):
        request = self.request()
        request.update(kind='greet', contentMode='platform-default', content='')
        with self.assertRaisesRegex(store.StoreError, 'authorization-required'):
            store.begin(self.root, self.token, request)
        self.assertEqual(store.load_state(self.root)['actions'], {})

    def test_platform_default_rejects_other_actions_and_invented_materials(self):
        self.allow()
        cases = [
            ({'kind': 'reply'}, 'only-supports-boss-greet'),
            ({'kind': 'commitment'}, 'only-supports-boss-greet'),
            ({'platform': 'other', 'targetKey': 'other:job'}, 'only-supports-boss-greet'),
            ({'content': '平台默认招呼'}, 'content-must-be-unobserved'),
            ({'observedContent': 'invented opener'}, 'cannot-predeclare-observed-content'),
            ({'answers': {'question': 'answer'}}, 'cannot-include-other-materials'),
            ({'attachments': ['not-a-real-file.pdf']}, 'cannot-include-other-materials'),
            ({'contentMode': 'unknown'}, 'invalid-content-mode'),
        ]
        for changes, error in cases:
            with self.subTest(changes=changes):
                request = self.request()
                request.update(kind='greet', contentMode='platform-default', content=None)
                request.update(changes)
                with self.assertRaisesRegex(store.StoreError, error):
                    store.begin(self.root, self.token, request)
        self.assertEqual(store.load_state(self.root)['actions'], {})

    def test_custom_messages_still_require_text(self):
        self.allow()
        for kind in ('greet', 'reply', 'commitment'):
            with self.subTest(kind=kind):
                request = self.request()
                request.update(kind=kind, content='')
                with self.assertRaisesRegex(store.StoreError, 'message-content-required'):
                    store.begin(self.root, self.token, request)

    def test_profile_change_after_begin_rejects_preclick_action_check(self):
        self.allow()
        profile = self.root / 'profile.md'
        profile.write_text('Formal experience: four months. Target: Python backend.\n', encoding='utf-8')
        action = store.begin(self.root, self.token, self.request())
        self.assertTrue(store.check_action(self.root, self.token, action['id'])['ready'])
        original_action = deepcopy(store.load_state(self.root)['actions'][action['id']])
        profile.write_text('Formal experience: one year. Target: data platform backend.\n', encoding='utf-8')
        with self.assertRaisesRegex(store.StoreError, 'profile-changed'):
            store.check_action(self.root, self.token, action['id'])
        self.assertEqual(store.load_state(self.root)['actions'][action['id']], original_action)

    def test_default_draft_does_not_reserve_or_send(self):
        with self.assertRaisesRegex(store.StoreError, 'authorization-required'):
            store.begin(self.root, self.token, self.request())
        self.assertEqual(store.load_state(self.root)['actions'], {})

    def test_specific_grant_works_without_changing_long_term_policy(self):
        request = self.request()
        request['oneShotAuthorization'] = self.one_shot(request)
        store.begin(self.root, self.token, request)
        self.assertEqual(store.load_policy(self.root)['authorization']['reply'], 'draft')
        request['targetKey'] = 'boss:another'
        with self.assertRaisesRegex(store.StoreError, 'scope-mismatch'):
            store.begin(self.root, self.token, request)

    def test_platform_and_account_scope(self):
        self.allow()
        self.configure(platforms=[{'id': 'boss', 'accountLabel': 'Fixture account'}])
        with self.assertRaisesRegex(store.StoreError, 'account-not-verified'):
            store.begin(self.root, self.token, self.request())
        request = self.request(accountLabel='Fixture account')
        store.begin(self.root, self.token, request)
        request.update(platform='other', targetKey='other:job')
        with self.assertRaisesRegex(store.StoreError, 'outside-authorized-platforms'):
            store.begin(self.root, self.token, request)

    def test_empty_scope_is_not_unrestricted(self):
        self.allow()
        p = store.load_policy(self.root)
        p['authorization']['scope'] = {'platforms': []}
        self.configure(authorization=p['authorization'])
        with self.assertRaisesRegex(store.StoreError, 'outside-authorized-platforms'):
            store.begin(self.root, self.token, self.request())

    def test_company_alias_blocks_reply_and_stale_candidate(self):
        self.allow()
        self.configure(targets={'excludedCompanies': [{'name': 'Example Staffing', 'aliases': ['示例招聘']} ]})
        store.update(self.root, self.token, 'threads', 'boss:thread-1', {'company': '示例招聘'})
        with self.assertRaisesRegex(store.StoreError, 'excluded-company'):
            store.begin(self.root, self.token, self.request(targetFacts={'company': 'Different claim'}))

    def test_headhunter_blocks_new_contacts_and_old_resume_requests(self):
        self.allow()
        self.configure(search={'excludeHeadhunterPosted': True})
        for kind in ('greet', 'application', 'share_resume'):
            request = self.request()
            request.update(kind=kind, targetKey='boss:' + kind,
                           targetFacts={'company': 'Fixture', 'publisherType': 'headhunter', 'evidence': '猎头职位'})
            with self.assertRaisesRegex(store.StoreError, 'excluded-headhunter'):
                store.begin(self.root, self.token, request)

    def test_unknown_publisher_requires_reading_not_guessing(self):
        self.allow()
        self.configure(search={'excludeHeadhunterPosted': True})
        request = self.request()
        request.update(kind='greet', targetFacts={'company': 'Fixture'})
        with self.assertRaisesRegex(store.StoreError, 'publisher-unverified'):
            store.begin(self.root, self.token, request)
        request['targetFacts'].update(publisherType='direct', evidence='Page labels publisher as employer HR')
        store.begin(self.root, self.token, request)

    def test_project_exclusion_survives_different_recruiter(self):
        self.allow()
        self.configure(search={'excludedOpportunityGroups': [{'key': 'project-1', 'aliases': ['项目一']}]})
        store.update(self.root, self.token, 'jobs', 'boss:job-1', {'opportunityGroup': '项目一'})
        request = self.request()
        request.update(kind='share_resume', targetFacts={'jobKey': 'boss:job-1', 'company': 'New recruiter'})
        with self.assertRaisesRegex(store.StoreError, 'excluded-opportunity-group'):
            store.begin(self.root, self.token, request)

    def test_resume_content_must_match_authorized_hash(self):
        self.allow()
        resume = self.root / 'resume.pdf'
        resume.write_bytes(b'authorized fixture')
        sha = hashlib.sha256(resume.read_bytes()).hexdigest()
        self.configure(resume={'path': str(resume), 'sha256': sha})
        resume.write_bytes(b'unapproved replacement')
        request = self.request()
        request.update(kind='share_resume', attachments=[str(resume)])
        with self.assertRaisesRegex(store.StoreError, 'attachment-outside'):
            store.begin(self.root, self.token, request)

    def test_platform_resume_needs_version_and_visible_evidence(self):
        self.allow()
        self.configure(resume={'sha256': 'a' * 64})
        request = self.request()
        request.update(kind='share_resume', answers={'resumeSha256': 'a' * 64})
        with self.assertRaisesRegex(store.StoreError, 'resume-attachment-required'):
            store.begin(self.root, self.token, request)
        request['answers']['platformResumeEvidence'] = 'Fixture page file/version matches previously uploaded hash'
        store.begin(self.root, self.token, request)

    def test_config_update_is_compare_and_swap_with_backup(self):
        config = store.effective_config(self.root)
        changed = deepcopy(config['policy'])
        changed['targets']['excludedCompanies'] = [{'name': 'Fixture'}]
        result = store.set_policy(self.root, self.token, changed, config['policyFingerprint'], 'User excludes Fixture')
        self.assertTrue((self.root / 'logs' / result['backup']).is_file())
        with self.assertRaisesRegex(store.StoreError, 'policy-changed'):
            store.set_policy(self.root, self.token, config['policy'], config['policyFingerprint'], 'Stale change')
        self.assertEqual(store.load_policy(self.root)['targets']['excludedCompanies'], [{'name': 'Fixture'}])

    def test_changed_policy_or_attachment_cannot_submit_pending_action(self):
        self.allow()
        request = self.request()
        resume = self.root / 'resume.pdf'
        resume.write_bytes(b'fixture')
        request['attachments'] = [str(resume)]
        action = store.begin(self.root, self.token, request)
        self.assertTrue(store.check_action(self.root, self.token, action['id'])['ready'])
        resume.write_bytes(b'changed')
        with self.assertRaisesRegex(store.StoreError, 'attachment-changed'):
            store.check_action(self.root, self.token, action['id'])
        self.configure(targets={'excludedCompanies': [{'name': 'Fixture'}]})
        with self.assertRaisesRegex(store.StoreError, 'policy-changed-before-submit'):
            store.check_action(self.root, self.token, action['id'])

    def test_invitation_and_confirmation_require_distinct_evidence(self):
        with self.assertRaisesRegex(store.StoreError, 'invite-evidence'):
            store.update(self.root, self.token, 'threads', 'boss:thread', {'stage': 'interview'})
        invite = {'stage': 'interview', 'interviewInvite': {'inboundId': 'invite-1', 'evidence': 'Please attend an interview'}}
        store.update(self.root, self.token, 'threads', 'boss:thread', invite)
        with self.assertRaisesRegex(store.StoreError, 'confirmation-evidence'):
            store.update(self.root, self.token, 'threads', 'boss:thread', {'interviewConfirmed': True})
        store.update(self.root, self.token, 'threads', 'boss:thread', {
            'interviewConfirmed': True, 'interview': {'mode': 'online', 'startAt': '2026-09-09T10:00:00+08:00',
                                                      'confirmationEvidence': 'Both parties agreed on this time'}})

    def test_doctor_does_not_leak_account_or_write_state(self):
        self.allow()
        self.configure(platforms=[{'id': 'boss', 'accountLabel': 'DO-NOT-EXPORT-ME'}])
        before = (self.root / 'state.json').read_bytes()
        output = json.dumps(doctor.diagnose(self.root))
        self.assertNotIn('DO-NOT-EXPORT-ME', output)
        self.assertNotIn(self.token, output)
        self.assertEqual(before, (self.root / 'state.json').read_bytes())


class RunTests(Fixture):
    def schedule(self):
        self.configure(timezone='+08:00', schedule={'enabled': True, 'windows': [
            {'name': 'search-apply', 'start': '08:00'},
            {'name': 'reply-check', 'start': '09:00', 'end': '19:00', 'intervalMinutes': 30},
            {'name': 'daily-report', 'time': '20:00'}]})

    def test_missed_slots_coalesce_and_report_is_due(self):
        self.schedule()
        with patch.object(store, 'now', return_value=datetime(2026, 9, 8, 7, 42, tzinfo=timezone.utc)):
            due = run_ledger.due(self.root)['due']
            replies = [r for r in due if r['mode'] == 'reply-check']
            self.assertEqual([r['slot'] for r in replies], ['15:30'])
        with patch.object(store, 'now', return_value=datetime(2026, 9, 8, 13, tzinfo=timezone.utc)):
            modes = [r['mode'] for r in run_ledger.due(self.root)['due']]
            self.assertIn('daily-report', modes)
            self.assertNotIn('reply-check', modes)

    def test_complete_report_requires_artifact_and_delivery_receipt(self):
        self.schedule()
        run = run_ledger.start(self.root, self.token, 'daily-report', '2026-09-08', '20:00')
        with self.assertRaisesRegex(store.StoreError, 'artifact-must-exist'):
            run_ledger.finish(self.root, self.token, run['key'], 'completed', 'Generated')
        report = self.root / 'logs' / '2026-09-08-daily-summary.md'
        report.write_text('Fixture verified daily report', encoding='utf-8')
        run_ledger.finish(self.root, self.token, run['key'], 'completed', 'Generated', 'logs/' + report.name)
        self.assertTrue(run_ledger.start(self.root, self.token, 'daily-report', '2026-09-08', '20:00')['alreadyCompleted'])
        with patch.object(store, 'now', return_value=datetime(2026, 9, 9, 0, tzinfo=timezone.utc)):
            records = run_ledger.due(self.root)['due']
            self.assertTrue(any(r['key'] == run['key'] and r['operation'] == 'deliver' for r in records))
        run_ledger.delivered(self.root, self.token, run['key'], 'Fixture host delivered response')
        self.assertTrue(run_ledger.delivered(self.root, self.token, run['key'], 'same')['unchanged'])

    def test_run_resume_preserves_attempts_and_unknown_actions(self):
        self.allow()
        action = store.begin(self.root, self.token, self.request())
        store.resolve(self.root, self.token, action['id'], 'unknown', 'Fixture connection lost')
        run_ledger.start(self.root, self.token, 'reply-check', '2026-09-08', '15:00')
        store.run_lock(self.root, 'release', self.token)
        self.token = store.run_lock(self.root, 'acquire')['token']
        resumed = run_ledger.start(self.root, self.token, 'reply-check', '2026-09-08', '15:00')
        self.assertEqual(resumed['attempt'], 2)
        self.assertIn(action['id'], resumed['unresolved'])

    def test_run_records_cannot_be_overwritten_by_generic_update(self):
        with self.assertRaisesRegex(store.StoreError, 'use-run-ledger'):
            store.update(self.root, self.token, 'scheduler', 'run:fake', {'status': 'completed'})

    def test_report_that_never_started_can_be_caught_up(self):
        self.schedule()
        policy = store.load_policy(self.root)
        policy['schedule']['startDate'] = '2026-09-08'
        self.configure(schedule=policy['schedule'])
        with patch.object(store, 'now', return_value=datetime(2026, 9, 9, 0, tzinfo=timezone.utc)):
            records = run_ledger.due(self.root)['due']
        self.assertTrue(any(r['mode'] == 'daily-report' and r['date'] == '2026-09-08' for r in records))
        self.assertFalse(any(r.get('date', '') < '2026-09-08' for r in records))


class BridgeTests(unittest.TestCase):
    def test_chinese_transport_and_no_implicit_submission_success(self):
        captured = []
        def response(request, **kwargs):
            captured.append(json.loads(request.data.decode('utf-8')))
            return io.BytesIO(b'{"ok":true,"data":{"success":true}}')
        with patch.object(webbridge_client, 'urlopen', side_effect=response):
            result = webbridge_client.command('fixture-session', 'fill', {'value': '中文简历'})
        self.assertEqual(captured[0]['args']['value'], '中文简历')
        self.assertFalse(result['submissionVerified'])

    def test_timeout_after_click_is_unknown_and_never_retried(self):
        with patch.object(webbridge_client, 'urlopen', side_effect=TimeoutError()) as mocked:
            result = webbridge_client.command('fixture-session', 'click', {'selector': '@e1'})
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(result['outcome'], 'unknown')
        self.assertFalse(result['retrySafe'])


if __name__ == '__main__':
    unittest.main()
