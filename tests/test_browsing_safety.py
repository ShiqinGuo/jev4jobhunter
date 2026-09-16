"""Offline browsing guards: all state and receipts live in temporary fixtures."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills' / 'job-hunter' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import store
from browsing_safety import Safety, classify
from browser_actions import Engine
import boss_page


class BrowsingFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='job-hunter-browsing-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        clock = patch.object(store, 'now', return_value=datetime(2026, 9, 15, 4, tzinfo=timezone.utc))
        clock.start()
        self.addCleanup(clock.stop)
        store.initialize(self.root)
        self.token = store.run_lock(self.root, 'acquire')['token']
        policy = store.load_policy(self.root)
        policy.update(
            timezone='+08:00',
            platforms=[{'id': 'boss', 'accountLabel': 'Fixture Candidate'}],
            authorization={**{kind: 'allow' for kind in store.KINDS}, 'evidence': 'Fixture explicit authorization'},
            dailyLimits={kind: None for kind in store.KINDS},
            search={'singleCityPerDay': True, 'experienceFilter': {
                'enabled': True, 'allowedLabels': ['应届生', '1年以内', '1-3年'],
                'excludedLabels': ['3-5年', '5-10年', '10年以上']}})
        store.write_json(self.root / 'policy.json', policy)
        store.update(self.root, self.token, 'scheduler', 'cityDailyPlan', {
            'date': '2026-09-15', 'city': '杭州', 'lockedCity': '杭州', 'confirmedCount': 0})
        self.guard = Safety(self.root, self.token, 'boss', 'fixture-session-1')
        self.query = {'city': '杭州', 'keyword': 'Python', 'experience': ['1-3年'],
                      'accountLabel': 'Fixture Candidate'}

    def card(self, key='boss:job-a', **changes):
        return {'key': key, 'company': 'Fixture Software', 'hrName': 'Fixture HR',
                'city': '杭州', 'experience': '1-3年', 'publisherType': 'direct',
                'evidence': 'Fixture visible card', **changes}

    def page(self, keys=('boss:job-a', 'boss:job-b'), **changes):
        return {'url': 'https://example.invalid/jobs?city=杭州&query=Python&experience=1-3年',
                'body': 'Fixture rendered job list', 'accountLabel': 'Fixture Candidate',
                'filters': {k: deepcopy(self.query[k]) for k in ('city', 'keyword', 'experience')},
                'cards': [self.card(key) for key in keys], 'controls': [{'id': 'keyword-input'}], **changes}

    def batch(self, keys=('boss:job-a', 'boss:job-b')):
        self.guard.start_query(self.query)
        self.guard.observe(self.page(keys))
        return self.guard.capture_list()

    def screen(self, key='boss:job-a', decision='shortlisted'):
        return self.guard.screen({'key': key, 'decision': decision, 'evidence': 'Fixture list review'})

    def open_detail(self, key='boss:job-a'):
        self.screen(key)
        result = self.guard.reserve('open-detail', {'key': key})
        self.guard.observe(self.page(detail={'key': key, 'text': 'Fixture full backend JD', 'complete': True}))
        return result

    def request(self, key='boss:job-a'):
        return {'kind': 'greet', 'platform': 'boss', 'targetKey': key,
                'accountLabel': 'Fixture Candidate', 'targetFacts': self.card(key),
                'context': 'Fixture reviewed Python backend opportunity',
                'content': '您好，我使用 Python 开发业务后端。',
                'authorizationEvidence': 'Fixture explicit authorization'}

    def restart(self):
        store.run_lock(self.root, 'release', self.token)
        self.token = store.run_lock(self.root, 'acquire')['token']
        self.guard = Safety(self.root, self.token, 'boss', 'fixture-session-2')


class BrowsingOrderTests(BrowsingFixture):
    def test_resume_context_recovers_route_and_checkpoint_without_browser_or_lock(self):
        self.batch()
        store.run_lock(self.root, 'release', self.token)
        calls = []
        engine = Engine(self.root, '', 'boss', '', lambda *args: calls.append(args))
        result = engine.execute('resume-context')
        self.assertEqual(result['browser']['provider'], 'kimi-webbridge')
        self.assertFalse(result['browser']['allowFallback'])
        self.assertEqual(result['flow']['query']['keyword'], 'Python')
        self.assertEqual(result['session'], 'fixture-session-1')
        self.assertEqual(result['policyFingerprint'], store.fingerprint(store.load_policy(self.root)))
        self.assertEqual(calls, [])

    def test_conflicting_browser_policy_rejected_before_any_transport(self):
        calls = []
        engine = Engine(self.root, self.token, 'boss', 'fixture-session-1', lambda *args: calls.append(args))
        original = store.load_policy(self.root)
        for browser in [{'required': 'native'}, {'preferred': 'cua'}, {'allowFallback': True}]:
            store.write_json(self.root / 'policy.json', {**original, 'browser': browser})
            with self.subTest(browser=browser), self.assertRaisesRegex(store.StoreError, 'browser-'):
                engine.execute('inspect')
        self.assertEqual(calls, [])

    def test_legacy_policy_without_browser_still_uses_kimi(self):
        policy = store.load_policy(self.root)
        policy.pop('browser', None)
        store.write_json(self.root / 'policy.json', policy)
        engine = Engine(self.root, '', 'boss', '', lambda *args: self.fail('unexpected browser call'))
        self.assertEqual(engine.execute('resume-context')['browser']['provider'], 'kimi-webbridge')

    def test_keyword_allowlist_rejects_agent_expansion_before_browser_call(self):
        policy = store.load_policy(self.root)
        policy['search']['queries'] = ['Python']
        store.write_json(self.root / 'policy.json', policy)
        calls = []
        engine = Engine(self.root, self.token, 'boss', 'fixture-session-1',
                        transport=lambda *args: calls.append(args))
        for word in ['FastAPI', '服务端开发', 'Python 后端']:
            with self.subTest(word=word), self.assertRaisesRegex(store.StoreError, 'keyword-outside-policy'):
                engine.execute('start-query', {**self.query, 'keyword': word})
        self.assertEqual(calls, [])
        self.assertEqual(engine.execute('start-query', self.query)['query']['keyword'], 'Python')

    def test_fixed_filter_selection_cannot_be_silently_narrowed(self):
        policy = store.load_policy(self.root)
        policy['search']['experienceFilter']['selectedLabels'] = ['应届生', '1年以内', '1-3年']
        policy['search']['salaryFilter'] = {'enabled': True, 'allowedLabels': ['10-20K', '20-50K'],
                                           'selectedLabels': ['10-20K']}
        store.write_json(self.root / 'policy.json', policy)
        with self.assertRaisesRegex(store.StoreError, 'experience-selection-must-match-policy'):
            self.guard.start_query({**self.query, 'salary': ['10-20K']})
        query = {**self.query, 'experience': ['应届生', '1年以内', '1-3年'], 'salary': ['20-50K']}
        with self.assertRaisesRegex(store.StoreError, 'salary-selection-must-match-policy'):
            self.guard.start_query(query)
        query['salary'] = ['10-20K']
        self.guard.start_query(query)

    def test_loaded_card_tail_does_not_stop_scroll_before_loading_threshold(self):
        self.batch()
        self.screen('boss:job-a', 'skipped')
        self.screen('boss:job-b', 'skipped')
        self.guard.reserve('scroll', {})
        self.guard.observe(self.page(listTailBelowViewport=False, scrollRemaining=200,
                                     loading=False, endOfList=False))
        with self.assertRaisesRegex(store.StoreError, 'completed-list-no-growth-observation-required'):
            self.guard.finish_list_read({'evidence': 'Last loaded card visible but feedback area remains'})
        result = self.guard.capture_list()
        self.assertEqual(result['nextAction'], 'scroll-current-query')
        self.assertFalse(result['exhaustionVerified'])
        self.guard.reserve('scroll', {})
        self.guard.observe(self.page(keys=('boss:job-a', 'boss:job-b', 'boss:job-c'),
                                     listTailBelowViewport=True, scrollRemaining=300, loading=False))
        result = self.guard.capture_list()
        self.assertEqual(list(result['candidates']), ['boss:job-c'])
        self.assertEqual(result['batch']['query']['keyword'], 'Python')
        with self.assertRaisesRegex(store.StoreError, 'finish-current-batch-before-one-scroll'):
            self.guard.reserve('scroll', {})

    def test_query_reset_does_not_inherit_previous_end_marker(self):
        self.batch()
        self.screen('boss:job-a', 'skipped')
        self.screen('boss:job-b', 'skipped')
        self.guard.reserve('scroll', {})
        self.guard.observe(self.page(endOfList=True))
        self.guard.capture_list()
        self.assertTrue(self.guard.status()['flow']['endOfList'])
        self.guard.start_query(self.query)
        self.assertFalse(self.guard.status()['flow']['endOfList'])

    def test_quota_notice_requires_matching_unknown_and_cannot_repeat(self):
        self.batch()
        request = self.request()
        request['browsingContext'] = {'batchId': self.guard.status()['flow']['batch']['id']}
        action = store.begin(self.root, self.token, request)
        store.resolve(self.root, self.token, action['id'], 'unknown', 'Fixture blocked by quota reminder')
        page = self.page(body='您今天已与120位BOSS沟通，还剩30次沟通机会哦',
                         detail={'key':'boss:job-b','text':'Fixture JD'})
        self.guard.observe(page)
        with self.assertRaises(store.StoreError):
            self.guard.reserve('ack-quota-notice', {'actionId':action['id']})
        page['detail']['key'] = 'boss:job-a'
        self.guard.observe(page)
        self.guard.reserve('ack-quota-notice', {'actionId':action['id']})
        self.guard.observe(page)
        with self.assertRaises(store.StoreError):
            self.guard.reserve('ack-quota-notice', {'actionId':action['id']})
        self.assertEqual(store.load_state(self.root)['actions'][action['id']]['status'], 'unknown')

    def test_stale_receipt_can_be_closed_before_next_reviewed_submit(self):
        self.batch()
        self.open_detail('boss:job-a')
        self.guard.review_detail({'key': 'boss:job-a', 'decision': 'apply',
                                  'eligibilityPassed': True,
                                  'evidence': 'Fixture current detail is a fit'})
        self.guard.observe(self.page(body='已向BOSS发送消息',
                                     detail={'key': 'boss:job-a', 'text': 'Fixture full backend JD', 'complete': True}))
        pending = self.guard.reserve('dismiss-receipt', {})
        self.assertEqual(pending['operation'], 'dismiss-receipt')
        self.guard.observe(self.page(detail={'key': 'boss:job-a', 'text': 'Fixture full backend JD', 'complete': True}))

        request = self.request('boss:job-a')
        action = store.begin(self.root, self.token, request)
        self.guard.observe(self.page(body='已向BOSS发送消息',
                                     detail={'key': 'boss:job-a', 'text': 'Fixture full backend JD', 'complete': True}))
        with self.assertRaisesRegex(store.StoreError, 'reconcile-before-dismiss'):
            self.guard.reserve('dismiss-receipt', {})
        self.assertEqual(store.load_state(self.root)['actions'][action['id']]['status'], 'pending')

    def test_scroll_may_traverse_completed_batch_before_loading_more(self):
        self.batch()
        self.screen('boss:job-a', 'skipped')
        self.screen('boss:job-b', 'skipped')
        self.guard.reserve('scroll', {})
        self.guard.observe(self.page(listTailBelowViewport=True, loading=False))
        self.assertTrue(self.guard.capture_list()['mayScroll'])
        self.guard.reserve('scroll', {})
        self.guard.observe(self.page(listTailBelowViewport=False, loading=False))
        self.assertFalse(self.guard.capture_list()['mayScroll'])
        with self.assertRaises(store.StoreError):
            self.guard.reserve('scroll', {})
        result = self.guard.finish_list_read({'evidence': 'Observed bottom of processed batch, no new cards and no loading.'})
        self.assertFalse(result['exhaustionVerified'])
        self.assertIsNone(self.guard.status()['flow']['pending'])
        with self.assertRaises(store.StoreError):
            self.guard.finish_list_read({'evidence': 'No matching pending scroll'})

    def test_detail_requires_captured_list_and_explicit_screen(self):
        self.guard.start_query(self.query)
        self.guard.observe(self.page())
        with self.assertRaisesRegex(store.StoreError, 'one-current-list-candidate-required'):
            self.guard.reserve('open-detail', {'key': 'boss:job-a'})
        self.guard.capture_list()
        with self.assertRaisesRegex(store.StoreError, 'list-screen-required-before-detail'):
            self.guard.reserve('open-detail', {'key': 'boss:job-a'})
        self.assertIsNone(self.guard.status()['flow']['pending'])
        self.assertIsNone(self.guard.status()['flow']['activeKey'])

    def test_next_detail_is_denied_until_active_candidate_is_terminal(self):
        self.batch()
        self.screen('boss:job-b')
        self.open_detail()
        with self.assertRaisesRegex(store.StoreError, 'one-current-list-candidate-required'):
            self.guard.reserve('open-detail', {'key': 'boss:job-b'})
        self.guard.review_detail({'key': 'boss:job-a', 'decision': 'skipped', 'evidence': 'Fixture hard tenure mismatch'})
        self.assertEqual(self.guard.reserve('open-detail', {'key': 'boss:job-b'})['operation'], 'open-detail')

    def test_apply_decision_still_holds_active_candidate(self):
        self.batch()
        self.screen('boss:job-b')
        self.open_detail()
        self.guard.review_detail({'key': 'boss:job-a', 'decision': 'apply',
                                  'evidence': 'Fixture eligibility verified', 'eligibilityPassed': True})
        with self.assertRaises(store.StoreError):
            self.guard.reserve('open-detail', {'key': 'boss:job-b'})
        with self.assertRaises(store.StoreError):
            self.guard.reserve('scroll', {})

    def test_unreviewed_batch_member_prevents_scroll(self):
        self.batch()
        self.screen('boss:job-a', 'skipped')
        with self.assertRaisesRegex(store.StoreError, 'finish-current-batch-before-one-scroll'):
            self.guard.reserve('scroll', {})
        self.screen('boss:job-b', 'deferred')
        self.assertEqual(self.guard.reserve('scroll', {})['operation'], 'scroll')

    def test_one_scroll_requires_new_ids_before_another_scroll(self):
        self.batch(('boss:job-a',))
        self.screen(decision='skipped')
        self.guard.reserve('scroll', {})
        self.guard.observe(self.page(('boss:job-a',)))
        result = self.guard.capture_list()
        self.assertTrue(result['waiting'])
        self.assertFalse(result['mayScroll'])
        with self.assertRaisesRegex(store.StoreError, 'browser-step-unresolved'):
            self.guard.reserve('scroll', {})
        self.guard.observe(self.page(('boss:job-a', 'boss:job-c')))
        self.assertEqual(self.guard.capture_list()['batch']['keys'], ['boss:job-c'])
        self.screen('boss:job-c', 'skipped')
        self.assertEqual(self.guard.reserve('scroll', {})['operation'], 'scroll')

    def test_foreign_platform_ids_do_not_count_as_new_scroll_results(self):
        self.batch(('boss:job-a',))
        self.screen(decision='skipped')
        self.guard.reserve('scroll', {})
        self.guard.observe(self.page(('boss:job-a', 'careers:unrelated')))
        self.guard.capture_list()
        with self.assertRaises(store.StoreError):
            self.guard.reserve('scroll', {})

    def test_passive_dom_prefetch_cannot_expand_frozen_batch(self):
        initial = self.batch(('boss:job-a',))['batch']
        self.guard.observe(self.page(('boss:job-a', 'boss:prefetched')))
        current = self.guard.capture_list()
        self.assertTrue(current['unchanged'])
        self.assertEqual(current['batch'], initial)
        self.assertNotIn('boss:prefetched', self.guard.status()['flow']['seen'])
        with self.assertRaisesRegex(store.StoreError, 'candidate-not-in-current-batch'):
            self.screen('boss:prefetched')

    def test_end_of_list_prevents_further_scroll(self):
        self.batch(('boss:job-a',))
        self.screen(decision='skipped')
        self.guard.reserve('scroll', {})
        self.guard.observe(self.page(('boss:job-a',), endOfList=True))
        self.guard.capture_list()
        with self.assertRaisesRegex(store.StoreError, 'finish-current-batch-before-one-scroll'):
            self.guard.reserve('scroll', {})

    def test_wrong_detail_cannot_complete_pending_open(self):
        self.batch()
        self.screen()
        self.guard.reserve('open-detail', {'key': 'boss:job-a'})
        self.guard.observe(self.page(detail={'key': 'boss:job-b', 'text': 'Unrelated JD'}))
        flow = self.guard.status()['flow']
        self.assertEqual(flow['activeKey'], 'boss:job-a')
        self.assertEqual(flow['pending']['operation'], 'open-detail')
        with self.assertRaisesRegex(store.StoreError, 'loaded-active-detail-required'):
            self.guard.review_detail({'key': 'boss:job-a', 'decision': 'apply',
                                      'evidence': 'Wrong page', 'eligibilityPassed': True})

    def test_unfinished_batch_cannot_be_replaced_by_another_query(self):
        self.batch()
        with self.assertRaisesRegex(store.StoreError, 'finish-current-batch-before-query-change'):
            self.guard.start_query({**self.query, 'keyword': 'Django'})

    def test_new_keyword_repeated_first_page_allows_one_scroll_not_reopening(self):
        self.batch()
        self.screen('boss:job-a', 'skipped')
        self.screen('boss:job-b', 'deferred')
        previous = deepcopy(self.guard.status()['flow']['candidates'])
        self.query['keyword'] = 'Django'
        self.guard.start_query(self.query)
        self.guard.observe(self.page())
        result = self.guard.capture_list()
        self.assertEqual(result['batch']['keys'], ['boss:job-a', 'boss:job-b'])
        self.assertEqual(self.guard.status()['flow']['candidates'], previous)
        self.assertEqual(self.guard.status()['flow']['seen'], ['boss:job-a', 'boss:job-b'])
        with self.assertRaisesRegex(store.StoreError, 'list-screen-required-before-detail'):
            self.guard.reserve('open-detail', {'key': 'boss:job-a'})
        self.guard.reserve('scroll', {})
        self.guard.observe(self.page())
        self.assertTrue(self.guard.capture_list()['waiting'])
        with self.assertRaises(store.StoreError):
            self.guard.reserve('scroll', {})


class BrowsingFilterTests(BrowsingFixture):
    def test_unknown_company_and_publisher_stay_available_for_detail_review(self):
        policy = store.load_policy(self.root)
        policy['targets']['excludedCompanies'] = [{'name': 'Excluded Agency'}]
        policy['search']['excludeHeadhunterPosted'] = True
        store.write_json(self.root / 'policy.json', policy)
        self.guard.start_query(self.query)
        unknown = self.card(company=None, publisherType='unknown', evidence='')
        self.guard.observe(self.page(cards=[unknown]))
        result = self.guard.capture_list()
        self.assertEqual(result['candidates']['boss:job-a']['decision'], 'unreviewed')
        self.screen()
        self.assertEqual(self.guard.reserve('open-detail', {'key': 'boss:job-a'})['operation'], 'open-detail')
        request = {**self.request(), 'targetFacts': unknown}
        with self.assertRaisesRegex(store.StoreError, 'company-unverified'):
            store.begin(self.root, self.token, request)

    def test_known_exclusions_still_skip_even_with_other_unknown_card_fields(self):
        policy = store.load_policy(self.root)
        policy['targets']['excludedCompanies'] = [{'name': 'Excluded Agency'}]
        policy['search']['excludeHeadhunterPosted'] = True
        store.write_json(self.root / 'policy.json', policy)
        store.update(self.root, self.token, 'jobs', 'boss:historical-exclusion', {'company': 'Excluded Agency'})
        cards = [self.card('boss:excluded-company', company='Excluded Agency', publisherType='unknown'),
                 self.card('boss:known-headhunter', company=None, publisherType='headhunter'),
                 self.card('boss:historical-exclusion', company='Unverified Different Name', publisherType='unknown')]
        self.guard.start_query(self.query)
        self.guard.observe(self.page(cards=cards))
        candidates = self.guard.capture_list()['candidates']
        for key in ('boss:excluded-company', 'boss:historical-exclusion'):
            self.assertEqual(candidates[key]['decision'], 'skipped')
            self.assertIn('excluded-company', candidates[key]['evidence'])
        self.assertEqual(candidates['boss:known-headhunter']['decision'], 'skipped')
        self.assertIn('excluded-headhunter-posted', candidates['boss:known-headhunter']['evidence'])

    def test_valid_url_without_ui_selected_filters_is_rejected(self):
        self.guard.start_query(self.query)
        self.guard.observe(self.page(filters={}))
        with self.assertRaisesRegex(store.StoreError, 'visible-selected-filters-do-not-match-query'):
            self.guard.capture_list()
        self.assertIsNone(self.guard.status()['flow']['batch'])

    def test_allowed_experience_nonempty_subset_is_accepted(self):
        self.query['experience'] = ['应届生', '1年以内']
        self.assertEqual(self.batch()['batch']['query']['experience'], self.query['experience'])

    def test_salary_filter_is_validated_when_policy_enables_it(self):
        policy = store.load_policy(self.root)
        policy['search']['salaryFilter'] = {
            'enabled': True, 'allowedLabels': ['10-20K'], 'selectedLabels': ['10-20K']}
        store.write_json(self.root / 'policy.json', policy)
        query = {**self.query, 'salary': ['10-20K']}
        with self.assertRaisesRegex(store.StoreError, 'salary-outside-policy'):
            self.guard.start_query({**query, 'salary': ['20-50K']})
        self.guard.start_query(query)
        page = self.page()
        page['filters']['salary'] = ['10-20K']
        self.guard.observe(page)
        self.assertEqual(self.guard.capture_list()['batch']['query']['salary'], ['10-20K'])
        with self.assertRaisesRegex(store.StoreError, 'visible-selected-filters-do-not-match-query'):
            self.guard.observe({**page, 'filters': {**page['filters'], 'salary': ['5-10K']}})
            self.guard.capture_list()

    def test_empty_or_outside_policy_experience_is_rejected(self):
        for experience in ([], ['3-5年'], ['1-3年', '5-10年']):
            with self.subTest(experience=experience):
                with self.assertRaisesRegex(store.StoreError, 'experience-outside-policy'):
                    self.guard.start_query({**self.query, 'experience': experience})

    def test_extra_ui_selected_experience_cannot_hide_behind_valid_query(self):
        self.guard.start_query(self.query)
        filters = {k: deepcopy(self.query[k]) for k in ('city', 'keyword', 'experience')}
        filters['experience'].append('3-5年')
        self.guard.observe(self.page(filters=filters))
        with self.assertRaisesRegex(store.StoreError, 'visible-selected-filters-do-not-match-query'):
            self.guard.capture_list()

    def test_daily_city_and_account_must_match_recorded_policy(self):
        for changes, message in [({'city': '上海'}, 'city-mismatch'),
                                 ({'accountLabel': 'Other Candidate'}, 'policy-account-mismatch')]:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(store.StoreError, message):
                    self.guard.start_query({**self.query, **changes})

    def test_policy_change_requires_query_revalidation(self):
        self.batch()
        self.screen()
        policy = store.load_policy(self.root)
        policy['search']['experienceFilter']['allowedLabels'] = ['应届生', '1年以内']
        store.write_json(self.root / 'policy.json', policy)
        with self.assertRaisesRegex(store.StoreError, 'policy-changed-revalidate-query'):
            self.guard.reserve('open-detail', {'key': 'boss:job-a'})


class BrowsingPersistenceTests(BrowsingFixture):
    def test_pending_active_and_seen_survive_run_restart(self):
        self.batch()
        self.screen()
        self.guard.reserve('open-detail', {'key': 'boss:job-a'})
        before = self.guard.status()['flow']
        self.restart()
        after = self.guard.status()['flow']
        for field in ('pending', 'activeKey', 'seen', 'batch', 'candidates'):
            self.assertEqual(after[field], before[field])
        with self.assertRaisesRegex(store.StoreError, 'browser-step-unresolved'):
            self.guard.reserve('open-detail', {'key': 'boss:job-b'})

    def test_unknown_outbound_is_excluded_in_a_new_run(self):
        action = store.begin(self.root, self.token, self.request())
        store.resolve(self.root, self.token, action['id'], 'unknown', 'Fixture lost receipt')
        self.restart()
        result = self.batch()
        self.assertEqual(result['candidates']['boss:job-a']['decision'], 'skipped')
        self.assertEqual(result['candidates']['boss:job-a']['evidence'], 'already-contacted-or-unresolved')
        self.assertEqual(result['candidates']['boss:job-b']['decision'], 'unreviewed')
        with self.assertRaises(store.StoreError):
            self.guard.reserve('open-detail', {'key': 'boss:job-a'})
        self.assertEqual(store.load_state(self.root)['actions'][action['id']]['status'], 'unknown')

    def test_restart_requires_current_session_page_evidence(self):
        self.batch(('boss:job-a',))
        self.screen(decision='skipped')
        self.restart()
        with self.assertRaisesRegex(store.StoreError, 'current-session-observation-required'):
            self.guard.reserve('scroll', {})

    def test_status_is_read_only(self):
        self.batch()
        before = (self.root / 'state.json').read_bytes()
        self.assertEqual(self.guard.status()['networkCalls'], 0)
        self.assertEqual((self.root / 'state.json').read_bytes(), before)

    def test_new_run_cannot_reuse_old_observation_even_in_same_browser_session(self):
        self.batch(('boss:job-a',))
        self.screen(decision='skipped')
        session = self.guard.session
        self.restart()
        self.guard = Safety(self.root, self.token, 'boss', session)
        with self.assertRaisesRegex(store.StoreError, 'current-run-observation-required'):
            self.guard.reserve('scroll', {})
        self.guard.observe(self.page(('boss:job-a',)))
        self.assertEqual(self.guard.reserve('scroll', {})['operation'], 'scroll')


class BrowsingAccessTests(BrowsingFixture):
    def test_security_words_inside_job_content_do_not_create_access_block(self):
        detail_text = '安全平台开发：实现访问受限提示和滑动验证组件。'
        card_text = 'Python安全平台开发，处理账号存在异常行为日志。'
        page = self.page(body=card_text + '\n' + detail_text,
                         cards=[self.card(text=card_text)],
                         detail={'key': 'boss:job-a', 'text': detail_text})
        self.assertEqual(self.guard.observe(page)['classification'], 'normal')
        self.assertIsNone(self.guard.status()['accessBlock'])
        page['body'] += '\n当前IP存在异常行为，请完成安全验证'
        self.assertEqual(self.guard.observe(page)['classification'], 'access-restricted')
        self.assertIsNotNone(self.guard.status()['accessBlock'])

    def test_access_clear_operation_cannot_clear_send_quota(self):
        store.update(self.root, self.token, 'blocks', 'boss', {
            'active': True, 'kinds': ['greet'], 'scope': 'send', 'reason': 'Platform daily quota reached'})
        self.guard.observe(self.page())
        with self.assertRaisesRegex(store.StoreError, 'access-block-required'):
            self.guard.clear_access_block({'evidence': 'Normal page is not a quota reset'})
        self.assertTrue(store.load_state(self.root)['blocks']['boss']['active'])
        with self.assertRaisesRegex(store.StoreError, 'platform-blocked'):
            store.begin(self.root, self.token, self.request())

    def test_access_clear_rejects_a_different_browser_session(self):
        self.guard.observe(self.page(body='访问受限'))
        original_session = self.guard.session
        self.restart()
        self.guard.observe(self.page())
        with self.assertRaisesRegex(store.StoreError, 'same-session-recovery-required'):
            self.guard.clear_access_block({'evidence': 'Different browser is not recovery evidence'})
        block = store.load_state(self.root)['blocks']['boss']
        self.assertTrue(block['active'])
        self.assertEqual(block['session'], original_session)

    def test_access_block_survives_new_query_account_session_and_expiry(self):
        self.batch(('boss:job-a',))
        self.screen(decision='skipped')
        restriction = self.guard.observe(self.page(body='访问受限，请于 2026-09-14 09:00 后重新核验'))
        first = store.load_state(self.root)['blocks']['boss']['firstEvidence']
        self.assertEqual(first, restriction['evidence'])
        self.restart()
        self.guard.observe(self.page(accountLabel='Other Candidate'))
        with self.assertRaisesRegex(store.StoreError, 'platform-access-blocked'):
            self.guard.start_query({**self.query, 'keyword': 'Django', 'accountLabel': 'Other Candidate'})
        with self.assertRaisesRegex(store.StoreError, 'platform-access-blocked'):
            self.guard.preflight()
        block = store.load_state(self.root)['blocks']['boss']
        self.assertTrue(block['active'])
        self.assertEqual(block['firstEvidence'], first)
        self.assertEqual(block['retryNotBefore'], '2026-09-14T09:00:00+08:00')

    def test_normal_passive_observation_cannot_clear_existing_restriction(self):
        self.guard.observe(self.page(body='请完成安全验证'))
        self.guard.observe(self.page())
        self.assertIsNotNone(self.guard.status()['accessBlock'])
        with self.assertRaisesRegex(store.StoreError, 'platform-access-blocked'):
            self.guard.start_query(self.query)

    def test_send_quota_does_not_prevent_list_or_detail_reading(self):
        store.update(self.root, self.token, 'blocks', 'boss', {
            'active': True, 'kinds': ['greet'], 'reason': 'Platform daily quota reached', 'scope': 'send'})
        self.assertIsNone(self.guard.status()['accessBlock'])
        self.batch(('boss:job-a',))
        self.open_detail()
        self.assertEqual(self.guard.status()['flow']['phase'], 'detail')
        with self.assertRaisesRegex(store.StoreError, 'platform-blocked'):
            store.begin(self.root, self.token, self.request())

    def test_passive_page_read_under_block_preserves_38_fixture_successes(self):
        for index in range(38):
            action = store.begin(self.root, self.token, self.request(f'boss:confirmed-{index}'))
            store.resolve(self.root, self.token, action['id'], 'succeeded', f'Fixture receipt {index}')
        store.update(self.root, self.token, 'scheduler', 'cityDailyPlan', {'confirmedCount': 38})
        self.guard.observe(self.page(body='访问受限'))
        before = store.load_state(self.root)
        result = self.guard.observe(self.page(body='访问受限，当前页面只读'))
        after = store.load_state(self.root)
        self.assertEqual(result['classification'], 'access-restricted')
        self.assertEqual(after['actions'], before['actions'])
        self.assertEqual(after['scheduler'], before['scheduler'])
        self.assertEqual(sum(a['status'] == 'succeeded' for a in after['actions'].values()), 38)
        self.assertEqual(after['blocks']['boss']['firstEvidence'], before['blocks']['boss']['firstEvidence'])

    def test_rendered_restriction_wins_even_when_stale_cards_remain(self):
        self.assertEqual(classify(self.page(body='当前IP存在异常行为')), 'access-restricted')
        self.assertEqual(classify(self.page(body='登录查看完整内容')), 'login-required')
        self.assertEqual(classify({'url': 'https://example.invalid/passport/zp/403', 'body': ''}), 'access-restricted')
        self.assertEqual(classify({'body': '请稍候', 'url': 'https://example.invalid/'}), 'security-check')

    def test_clear_requires_retry_time_and_same_account_normal_evidence(self):
        self.guard.observe(self.page(body='访问受限，请于 2026-09-16 09:00 后重新核验'))
        self.guard.observe(self.page())
        with self.assertRaisesRegex(store.StoreError, 'platform-retry-time-not-reached'):
            self.guard.clear_access_block({'evidence': 'Fixture recovery check'})
        with patch.object(store, 'now', return_value=datetime(2026, 9, 16, 4, tzinfo=timezone.utc)):
            self.guard.observe(self.page(accountLabel='Other Candidate'))
            with self.assertRaisesRegex(store.StoreError, 'same-account-normal-page-evidence-required'):
                self.guard.clear_access_block({'evidence': 'Wrong account'})
            self.guard.observe(self.page())
            self.assertTrue(self.guard.clear_access_block({'evidence': 'Fixture verified original account recovery'})['cleared'])
        self.assertIsNone(self.guard.status()['accessBlock'])


class AccountContextTests(BrowsingFixture):
    def select_new(self, **changes):
        self.guard.observe(self.page(accountLabel='Other Candidate'))
        return self.guard.select_account_context({
            'contextId': 'test-account', 'accountLabel': 'Other Candidate',
            'userDeclaredIndependent': True, 'originalContextId': 'original-account',
            'originalAccountLabel': 'Fixture Candidate', 'evidence': 'User explicitly identified a separate test account',
            **changes})

    def configure_new_policy(self):
        policy = store.load_policy(self.root)
        policy['platforms'][0]['accountLabel'] = 'Other Candidate'
        store.write_json(self.root / 'policy.json', policy)

    def test_explicit_selection_preserves_original_block_and_old_account_stays_blocked(self):
        self.guard.observe(self.page(body='访问受限'))
        before = deepcopy(store.load_state(self.root)['blocks']['boss'])
        result = self.select_new()
        self.assertIsNone(result['accessBlock'])
        self.guard.preflight()
        after = store.load_state(self.root)['blocks']['boss']
        for key, value in before.items():
            self.assertEqual(after[key], value)
        self.assertEqual(after['accountContextId'], 'original-account')
        self.guard.observe(self.page())
        self.guard.select_account_context({'contextId': 'original-account', 'accountLabel': 'Fixture Candidate',
                                          'evidence': 'User returned to original account'})
        with self.assertRaisesRegex(store.StoreError, 'platform-access-blocked'):
            self.guard.preflight()

    def test_selection_requires_user_declaration_and_current_normal_matching_account(self):
        self.guard.observe(self.page(body='访问受限'))
        with self.assertRaisesRegex(store.StoreError, 'declaration-required'):
            self.select_new(userDeclaredIndependent=False)
        with self.assertRaisesRegex(store.StoreError, 'normal-page-evidence-required'):
            self.guard.select_account_context({'contextId': 'test-account', 'accountLabel': 'Wrong Account',
                'userDeclaredIndependent': True, 'evidence': 'Fixture'})
        self.assertNotIn('accountContextId', store.load_state(self.root)['blocks']['boss'])

    def test_new_restriction_is_bound_to_selected_context_and_blocks_outbox(self):
        self.guard.observe(self.page(body='访问受限'))
        self.select_new()
        self.configure_new_policy()
        self.guard.observe(self.page(accountLabel='Other Candidate', body='当前 IP 存在异常行为，访问受限'))
        state = store.load_state(self.root)
        self.assertTrue(state['blocks']['boss']['active'])
        self.assertEqual(state['contextBlocks']['boss']['test-account']['reportedScope'], 'ip')
        with self.assertRaisesRegex(store.StoreError, 'platform-access-blocked'):
            self.guard.preflight()
        with self.assertRaisesRegex(store.StoreError, 'platform-blocked'):
            store.begin(self.root, self.token, {**self.request(), 'accountLabel': 'Other Candidate',
                                              'accountContextId': 'test-account'})

    def test_context_selection_does_not_reset_outbox_dedupe(self):
        action = store.begin(self.root, self.token, self.request())
        store.resolve(self.root, self.token, action['id'], 'unknown', 'Fixture unknown receipt')
        original = deepcopy(store.load_state(self.root)['actions'])
        self.guard.observe(self.page(body='访问受限'))
        self.select_new()
        self.configure_new_policy()
        with self.assertRaisesRegex(store.StoreError, 'duplicate-or-unresolved-action'):
            store.begin(self.root, self.token, {**self.request(), 'accountLabel': 'Other Candidate',
                                              'accountContextId': 'test-account'})
        fresh = store.begin(self.root, self.token, {**self.request('boss:new-job'), 'accountLabel': 'Other Candidate',
                                                  'accountContextId': 'test-account'})
        self.assertTrue(store.check_action(self.root, self.token, fresh['id'])['ready'])
        self.assertEqual(store.load_state(self.root)['actions'][action['id']], original[action['id']])

    def test_explicit_shared_scope_is_not_reclassified_as_an_account_block(self):
        self.guard.observe(self.page(body='访问受限'))
        store.update(self.root, self.token, 'blocks', 'boss', {
            'applicability': 'all-contexts', 'scopeEvidence': 'Verified restriction applies to this shared network'})
        with self.assertRaisesRegex(store.StoreError, 'shared-scope-restriction'):
            self.select_new()
        self.assertNotIn('accountContextId', store.load_state(self.root)['blocks']['boss'])

    def test_selected_context_is_not_implicitly_reused_in_a_different_session(self):
        self.select_new()
        self.restart()
        with self.assertRaisesRegex(store.StoreError, 'context-session-required'):
            self.guard.preflight()


class ScriptedTransport:
    """An exhausted script fails the test; it can never fall back to a browser."""
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, action, args):
        self.calls.append((action, deepcopy(args)))
        if not self.responses:
            raise AssertionError('Unexpected browser call beyond offline fixture responses')
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, dict) and 'outcome' in value:
            return value
        return {'outcome': 'returned', 'result': {'data': {'value': json.dumps(value, ensure_ascii=False)}}}

    @property
    def mutation_calls(self):
        return [call for call in self.calls if call[1]['code'] != boss_page.observation_script()]


class BrowsingEngineTests(BrowsingFixture):
    def test_hover_filter_uses_one_pointer_move_and_no_outbox(self):
        self.guard.start_query(self.query)
        page = self.page(controls=[{'id': 'degree-menu', 'kind': 'filter-menu', 'filter': 'degree', 'label': '学历要求'}])
        target = {'status': 'hover-target', 'id': 'degree-menu', 'x': 60, 'y': 25}
        transport = ScriptedTransport(page, target, {'outcome': 'returned', 'result': {'ok': True, 'data': {}}}, page)
        result = self.engine(transport).execute('control', {'id': 'degree-menu', 'interaction': 'hover'})
        self.assertEqual(result['step']['status'], 'hovered')
        self.assertEqual([action for action, _ in transport.calls], ['evaluate', 'evaluate', 'cdp', 'evaluate'])
        self.assertEqual(transport.calls[2][1], {'method': 'Input.dispatchMouseEvent', 'params': {'type': 'mouseMoved', 'x': 60, 'y': 25}})
        self.assertIsNone(self.guard.status()['flow']['pending'])
        self.assertEqual(store.load_state(self.root)['actions'], {})

    def test_unknown_hover_preserves_step_without_retry(self):
        self.guard.start_query(self.query)
        page = self.page(controls=[{'id': 'degree-menu', 'kind': 'filter-menu', 'filter': 'degree', 'label': '学历要求'}])
        transport = ScriptedTransport(page, {'status': 'hover-target', 'x': 60, 'y': 25}, {'outcome': 'unknown'})
        with self.assertRaisesRegex(store.StoreError, 'browser-outcome-unknown'):
            self.engine(transport).execute('control', {'id': 'degree-menu', 'interaction': 'hover'})
        self.assertEqual(len(transport.calls), 3)
        self.assertIsNotNone(self.guard.status()['flow']['pending'])
        self.assertEqual(store.load_state(self.root)['actions'], {})

    def request(self, key='boss:job-a'):
        return {**super().request(key), 'contentMode': 'platform-default', 'content': None}

    def engine(self, transport):
        return Engine(self.root, self.token, 'boss', self.guard.session, transport=transport)

    def ready_to_submit(self):
        self.batch(('boss:job-a',))
        self.open_detail()
        self.guard.review_detail({'key': 'boss:job-a', 'decision': 'apply',
                                  'evidence': 'Fixture complete JD and actual tenure reviewed',
                                  'eligibilityPassed': True})
        return self.page(detail={'key': 'boss:job-a', 'text': 'Fixture full backend JD', 'complete': True})

    def test_profile_change_after_query_blocks_detail_without_click_or_outbox(self):
        profile = self.root / 'profile.md'
        profile.write_text('Formal experience: four months. Target: Python backend.\n', encoding='utf-8')
        self.batch()
        self.screen()
        profile.write_text('Formal experience: one year. Target: data platform backend.\n', encoding='utf-8')
        transport = ScriptedTransport(self.page(), {'status': 'performed'},
                                      self.page(detail={'key': 'boss:job-a', 'text': 'Fixture full backend JD'}))
        with self.assertRaisesRegex(store.StoreError, 'profile-changed'):
            self.engine(transport).execute('open-detail', {'key': 'boss:job-a'})
        self.assertEqual(transport.mutation_calls, [])
        self.assertEqual(store.load_state(self.root)['actions'], {})
        self.assertIsNone(self.guard.status()['flow']['activeKey'])
        self.assertIsNone(self.guard.status()['flow']['pending'])

    def test_profile_change_after_review_blocks_submit_without_click_or_outbox(self):
        profile = self.root / 'profile.md'
        profile.write_text('Formal experience: four months. Target: Python backend.\n', encoding='utf-8')
        page = self.ready_to_submit()
        profile.write_text('Formal experience: one year. Target: data platform backend.\n', encoding='utf-8')
        receipt = deepcopy(page)
        receipt['body'] = '已向BOSS发送消息'
        transport = ScriptedTransport(page, {'status': 'performed'}, receipt)
        with self.assertRaisesRegex(store.StoreError, 'profile-changed'):
            self.engine(transport).execute('submit', {'request': self.request()})
        self.assertEqual(transport.mutation_calls, [])
        self.assertEqual(store.load_state(self.root)['actions'], {})
        self.assertIsNone(self.guard.status()['flow']['pending'])

    def test_new_profile_and_policy_reopen_old_skips_but_preserve_contact_deduplication(self):
        profile = self.root / 'profile.md'
        profile.write_text('Formal experience: four months. Target: Python backend.\n', encoding='utf-8')
        for key, status in [('boss:held-success', 'succeeded'), ('boss:held-unknown', 'unknown')]:
            action = store.begin(self.root, self.token, self.request(key))
            store.resolve(self.root, self.token, action['id'], status, 'Fixture earlier contact evidence')
        prior_actions = deepcopy(store.load_state(self.root)['actions'])
        keys = ('boss:old-skip', 'boss:old-defer', 'boss:held-success', 'boss:held-unknown')
        self.batch(keys)
        self.screen('boss:old-skip', 'skipped')
        self.screen('boss:old-defer', 'deferred')
        # Change each source independently: a policy-only implementation must
        # not accidentally make a profile-only invalidation test pass.
        for changed_source in ('profile', 'policy'):
            with self.subTest(changed_source=changed_source):
                if changed_source == 'profile':
                    profile.write_text('Formal experience: one year. Target: FastAPI data platform.\n', encoding='utf-8')
                else:
                    policy = store.load_policy(self.root)
                    policy['targets']['keywords'] = ['FastAPI']
                    policy['search']['experienceFilter']['allowedLabels'] = ['1-3年']
                    store.write_json(self.root / 'policy.json', policy)
                    self.query['keyword'] = 'FastAPI'
                self.guard.start_query(self.query)
                self.guard.observe(self.page(keys))
                candidates = self.guard.capture_list()['candidates']
                for key in ('boss:old-skip', 'boss:old-defer'):
                    self.assertEqual(candidates[key]['decision'], 'unreviewed')
                for key in ('boss:held-success', 'boss:held-unknown'):
                    self.assertEqual(candidates[key]['decision'], 'skipped')
                    self.assertEqual(candidates[key]['evidence'], 'already-contacted-or-unresolved')
                self.assertEqual(store.load_state(self.root)['actions'], prior_actions)
                self.screen('boss:old-skip', 'skipped')
                self.screen('boss:old-defer', 'deferred')

    def test_changed_profile_can_locally_defer_old_shortlist_before_restarting_query(self):
        profile = self.root / 'profile.md'
        profile.write_text('Formal experience: four months. Target: Python backend.\n', encoding='utf-8')
        self.batch()
        self.screen('boss:job-a')
        self.screen('boss:job-b')
        profile.write_text('Formal experience: one year. Target: data platform backend.\n', encoding='utf-8')
        transport = ScriptedTransport(self.page())
        engine = self.engine(transport)
        with self.assertRaisesRegex(store.StoreError, 'profile-changed'):
            engine.execute('open-detail', {'key': 'boss:job-a'})
        self.assertEqual(transport.mutation_calls, [])
        local = ScriptedTransport()
        local_engine = self.engine(local)
        self.assertEqual(local_engine.execute('screen', {
            'key': 'boss:job-a', 'decision': 'deferred', 'evidence': 'Profile changed; review again in new context'})['decision'], 'deferred')
        self.assertEqual(local_engine.execute('screen', {
            'key': 'boss:job-b', 'decision': 'skipped', 'evidence': 'Previous shortlist invalidated by new profile'})['decision'], 'skipped')
        self.assertEqual(local.calls, [])
        self.guard.start_query(self.query)
        self.guard.observe(self.page())
        self.assertEqual({c['decision'] for c in self.guard.capture_list()['candidates'].values()}, {'unreviewed'})
        self.assertEqual(store.load_state(self.root)['actions'], {})

    def test_legacy_pending_without_profile_fingerprint_requires_reconciliation_not_submit(self):
        (self.root / 'profile.md').write_text('Fixture current profile.\n', encoding='utf-8')
        action = store.begin(self.root, self.token, self.request())
        state = store.load_state(self.root)
        state['actions'][action['id']].pop('profileFingerprint', None)
        store.write_json(self.root / 'state.json', state)
        with self.assertRaises(store.StoreError):
            store.check_action(self.root, self.token, action['id'])
        self.assertEqual(store.load_state(self.root)['actions'][action['id']]['status'], 'pending')
        transport = ScriptedTransport(self.page())
        result = self.engine(transport).execute('reconcile', {'actionId': action['id']})
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(transport.mutation_calls, [])
        self.assertEqual(len(store.load_state(self.root)['actions']), 1)

    def test_existing_access_block_causes_zero_transport_calls(self):
        self.guard.observe(self.page(body='访问受限'))
        transport = ScriptedTransport()
        engine = self.engine(transport)
        for operation, data in [('capture-list', {}), ('open-detail', {'key': 'boss:job-a'}),
                                ('scroll', {}), ('control', {'id': 'keyword-input', 'value': 'Django'}),
                                ('submit', {'request': self.request()}), ('dismiss-receipt', {})]:
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(store.StoreError, 'platform-access-blocked'):
                    engine.execute(operation, data)
        self.assertEqual(transport.calls, [])

    def test_newly_observed_restriction_stops_before_action_and_next_inspect(self):
        self.batch()
        self.screen()
        transport = ScriptedTransport(self.page(body='当前IP存在异常行为'))
        engine = self.engine(transport)
        with self.assertRaisesRegex(store.StoreError, 'platform-access-blocked'):
            engine.execute('open-detail', {'key': 'boss:job-a'})
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.mutation_calls, [])
        with self.assertRaisesRegex(store.StoreError, 'platform-access-blocked'):
            engine.execute('scroll')
        self.assertEqual(len(transport.calls), 1)

    def test_explicit_passive_inspect_is_allowed_while_access_blocked(self):
        self.guard.observe(self.page(body='访问受限'))
        before = store.load_state(self.root)
        transport = ScriptedTransport(self.page(body='访问受限'))
        self.assertEqual(self.engine(transport).execute('inspect')['classification'], 'access-restricted')
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.mutation_calls, [])
        after = store.load_state(self.root)
        self.assertEqual(after['actions'], before['actions'])
        self.assertEqual(after['scheduler'], before['scheduler'])

    def test_unreviewed_candidate_cannot_reach_mutating_transport(self):
        self.batch()
        transport = ScriptedTransport(self.page())
        with self.assertRaisesRegex(store.StoreError, 'list-screen-required-before-detail'):
            self.engine(transport).execute('open-detail', {'key': 'boss:job-a'})
        self.assertEqual(transport.mutation_calls, [])

    def test_async_detail_is_not_retried_and_later_passive_read_resolves_it(self):
        self.batch()
        self.screen()
        loading = self.page((), body='加载中', controls=[], loading=True)
        transport = ScriptedTransport(self.page(), {'status': 'performed'}, loading)
        self.engine(transport).execute('open-detail', {'key': 'boss:job-a'})
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(len(transport.mutation_calls), 1)
        flow = self.guard.status()['flow']
        self.assertEqual(flow['pending']['operation'], 'open-detail')
        self.assertEqual(flow['phase'], 'detail-opening')
        later = ScriptedTransport(self.page(detail={'key': 'boss:job-a', 'text': 'Fixture complete JD'}))
        self.engine(later).execute('inspect')
        self.assertEqual(self.guard.status()['flow']['phase'], 'detail')
        self.assertIsNone(self.guard.status()['flow']['pending'])
        self.assertEqual(later.mutation_calls, [])

    def test_unknown_open_preserves_intent_and_restart_does_not_repeat_click(self):
        self.batch()
        self.screen()
        transport = ScriptedTransport(self.page(), {'outcome': 'unknown'})
        with self.assertRaisesRegex(store.StoreError, 'browser-outcome-unknown'):
            self.engine(transport).execute('open-detail', {'key': 'boss:job-a'})
        pending = self.guard.status()['flow']['pending']
        self.restart()
        retry = ScriptedTransport(self.page())
        with self.assertRaises(store.StoreError):
            self.engine(retry).execute('open-detail', {'key': 'boss:job-a'})
        self.assertEqual(retry.mutation_calls, [])
        self.assertEqual(self.guard.status()['flow']['pending'], pending)

    def test_explicit_unsupported_no_click_releases_only_that_step(self):
        self.batch()
        self.screen()
        transport = ScriptedTransport(self.page(), {'status': 'unsupported', 'reason': 'Missing unique visible target'})
        self.assertEqual(self.engine(transport).execute('open-detail', {'key': 'boss:job-a'})['status'], 'unsupported')
        flow = self.guard.status()['flow']
        self.assertIsNone(flow['pending'])
        self.assertIsNone(flow['activeKey'])
        self.assertEqual(flow['phase'], 'batch')
        self.assertEqual(flow['candidates']['boss:job-a']['decision'], 'shortlisted')
        self.assertEqual(len(transport.calls), 2)

    def test_unknown_submit_is_durable_and_cannot_be_sent_again(self):
        page = self.ready_to_submit()
        transport = ScriptedTransport(page, {'outcome': 'unknown'})
        result = self.engine(transport).execute('submit', {'request': self.request()})
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(len(transport.mutation_calls), 1)
        self.assertEqual(store.load_state(self.root)['actions'][result['id']]['status'], 'unknown')
        self.assertIsNone(self.guard.status()['flow']['pending'])
        self.assertIsNone(self.guard.status()['flow']['activeKey'])
        self.restart()
        retry = ScriptedTransport(page)
        with self.assertRaises(store.StoreError):
            self.engine(retry).execute('submit', {'request': self.request()})
        self.assertEqual(retry.mutation_calls, [])
        self.assertEqual(len(store.load_state(self.root)['actions']), 1)

    def test_matching_receipt_is_required_not_a_changed_button(self):
        page = self.ready_to_submit()
        weak = deepcopy(page)
        weak['detail']['button'] = '继续沟通'
        weak['body'] = '继续沟通'
        transport = ScriptedTransport(page, {'status': 'performed'}, weak)
        result = self.engine(transport).execute('submit', {'request': self.request()})
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(len(transport.mutation_calls), 1)
        self.assertEqual(store.load_state(self.root)['scheduler']['cityDailyPlan']['confirmedCount'], 0)
        receipt = deepcopy(page)
        receipt['body'] = '已向BOSS发送消息'
        late = ScriptedTransport(receipt)
        resolved = self.engine(late).execute('reconcile', {'actionId': result['id']})
        self.assertEqual(resolved['status'], 'succeeded')
        self.assertEqual(store.load_state(self.root)['scheduler']['cityDailyPlan']['confirmedCount'], 1)
        self.assertEqual(late.mutation_calls, [])
        self.assertEqual(len(store.load_state(self.root)['actions']), 1)

    def test_platform_default_uses_new_matching_receipt_without_claiming_text(self):
        page = self.ready_to_submit()
        request = {**self.request(), 'contentMode': 'platform-default', 'content': None}
        receipt = deepcopy(page)
        receipt['body'] = '已向BOSS发送消息\nUnrelated visible text is not the opener.'
        transport = ScriptedTransport(page, {'status': 'performed'}, receipt)
        result = self.engine(transport).execute('submit', {'request': request})
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(len(transport.mutation_calls), 1)
        action = store.load_state(self.root)['actions'][result['id']]
        self.assertIsNone(action['content'])
        self.assertIsNone(action['observedContent'])
        before = json.loads((self.root / action['browsingContext']['submitObservation']).read_text(encoding='utf-8'))
        self.assertNotIn('已向BOSS发送消息', before['page']['body'])
        evidence_path = action['evidence'].split(': matching receipt')[0]
        after = json.loads((self.root / evidence_path).read_text(encoding='utf-8'))
        self.assertEqual(after['page'], receipt)
        self.assertEqual(store.load_state(self.root)['scheduler']['cityDailyPlan']['confirmedCount'], 1)

    def test_platform_default_rejects_preexisting_receipt_before_any_click(self):
        page = self.ready_to_submit()
        page['body'] = '已向BOSS发送消息'
        transport = ScriptedTransport(page)
        with self.assertRaisesRegex(store.StoreError, 'dismiss-previous-receipt'):
            self.engine(transport).execute('submit', {'request': {
                **self.request(), 'contentMode': 'platform-default', 'content': ''}})
        self.assertEqual(transport.mutation_calls, [])
        self.assertEqual(store.load_state(self.root)['actions'], {})

    def test_platform_default_weak_result_stays_unknown_and_late_receipt_reconciles_once(self):
        page = self.ready_to_submit()
        request = {**self.request(), 'contentMode': 'platform-default', 'content': None}
        weak = deepcopy(page)
        weak['body'] = '继续沟通'
        weak['detail']['button'] = '继续沟通'
        transport = ScriptedTransport(page, {'status': 'performed'}, weak)
        result = self.engine(transport).execute('submit', {'request': request})
        self.assertEqual(result['status'], 'unknown')
        with self.assertRaisesRegex(store.StoreError, 'duplicate-or-unresolved'):
            store.begin(self.root, self.token, request)
        receipt = deepcopy(page)
        receipt['body'] = '已向BOSS发送消息'
        late = ScriptedTransport(receipt)
        resolved = self.engine(late).execute('reconcile', {'actionId': result['id']})
        self.assertEqual(resolved['status'], 'succeeded')
        self.assertEqual(late.mutation_calls, [])
        self.assertIsNone(store.load_state(self.root)['actions'][result['id']]['observedContent'])

    def test_platform_default_requires_saved_preclick_baseline(self):
        page = self.ready_to_submit()
        request = {**self.request(), 'contentMode': 'platform-default', 'content': None}
        action = store.begin(self.root, self.token, request)
        receipt = deepcopy(page)
        receipt['body'] = '已向BOSS发送消息'
        result = self.engine(ScriptedTransport(receipt)).execute('reconcile', {'actionId': action['id']})
        self.assertEqual(result['status'], 'unknown')

    def test_platform_default_wrong_account_or_job_receipt_stays_unknown(self):
        page = self.ready_to_submit()
        request = {**self.request(), 'contentMode': 'platform-default', 'content': None}
        wrong = deepcopy(page)
        wrong.update(accountLabel='Other Candidate', body='已向BOSS发送消息')
        result = self.engine(ScriptedTransport(page, {'status': 'performed'}, wrong)).execute('submit', {'request': request})
        self.assertEqual(result['status'], 'unknown')
        wrong_job = deepcopy(page)
        wrong_job['body'] = '已向BOSS发送消息'
        wrong_job['detail']['key'] = 'boss:job-b'
        resolved = self.engine(ScriptedTransport(wrong_job)).execute('reconcile', {'actionId': result['id']})
        self.assertEqual(resolved['status'], 'unknown')

    def test_custom_text_is_rejected_before_any_browser_call_or_outbox(self):
        self.ready_to_submit()
        for mode in ('text', None):
            request = {**self.request(), 'content': 'Custom authorized opener'}
            if mode is None:
                request.pop('contentMode')
            else:
                request['contentMode'] = mode
            transport = ScriptedTransport()
            with self.subTest(mode=mode), self.assertRaisesRegex(store.StoreError, 'platform-default-only'):
                self.engine(transport).execute('submit', {'request': request})
            self.assertEqual(transport.calls, [])
            self.assertEqual(store.load_state(self.root)['actions'], {})
        self.assertEqual(self.guard.status()['flow']['candidates']['boss:job-a']['decision'], 'apply')

    def test_platform_default_with_supplied_text_is_rejected_without_browser_calls(self):
        transport = ScriptedTransport()
        with self.assertRaisesRegex(store.StoreError, 'platform-default-content-must-be-unobserved'):
            self.engine(transport).execute('submit', {'request': {
                **self.request(), 'content': 'Custom authorized opener'}})
        self.assertEqual(transport.calls, [])
        self.assertEqual(store.load_state(self.root)['actions'], {})

    def test_legacy_custom_text_in_page_body_cannot_prove_delivery(self):
        page = self.ready_to_submit()
        request = {**self.request(), 'contentMode': 'text', 'content': 'Custom authorized opener'}
        action = store.begin(self.root, self.token, request)
        receipt = deepcopy(page)
        receipt['body'] = '已向BOSS发送消息\n' + request['content']
        transport = ScriptedTransport(receipt)
        result = self.engine(transport).execute('reconcile', {'actionId': action['id']})
        self.assertEqual(result['status'], 'unknown')
        self.assertIn('message-level verification unsupported', result['evidence'])
        self.assertEqual(transport.mutation_calls, [])
        self.assertEqual(store.load_state(self.root)['actions'][action['id']]['status'], 'unknown')

    def test_wrong_account_receipt_cannot_mark_submit_successful(self):
        page = self.ready_to_submit()
        wrong = deepcopy(page)
        wrong.update(accountLabel='Other Candidate', body='已向BOSS发送消息')
        transport = ScriptedTransport(page, {'status': 'performed'}, wrong)
        result = self.engine(transport).execute('submit', {'request': self.request()})
        self.assertEqual(result['status'], 'unknown')

    def test_wrong_job_receipt_cannot_mark_submit_successful(self):
        page = self.ready_to_submit()
        wrong = deepcopy(page)
        wrong['detail']['key'] = 'boss:job-b'
        wrong['body'] = '已向BOSS发送消息'
        transport = ScriptedTransport(page, {'status': 'performed'}, wrong)
        result = self.engine(transport).execute('submit', {'request': self.request()})
        self.assertEqual(result['status'], 'unknown')

    def test_preclick_recheck_failure_leaves_no_stranded_submit_intent(self):
        page = self.ready_to_submit()
        transport = ScriptedTransport(page)
        with patch.object(store, 'check_action', side_effect=store.StoreError('Fixture pre-click policy race')):
            with self.assertRaisesRegex(store.StoreError, 'pre-click policy race'):
                self.engine(transport).execute('submit', {'request': self.request()})
        self.assertEqual(transport.mutation_calls, [])
        actions = list(store.load_state(self.root)['actions'].values())
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['status'], 'failed')
        self.assertIsNone(self.guard.status()['flow']['pending'])
        self.assertIsNone(self.guard.status()['flow']['activeKey'])

    def test_defer_interrupted_detail_requires_fresh_read_then_makes_no_browser_call(self):
        self.batch()
        self.screen('boss:job-a')
        self.screen('boss:job-b')
        self.guard.reserve('open-detail', {'key': 'boss:job-a'})
        transport = ScriptedTransport()
        engine = self.engine(transport)
        data = {'key': 'boss:job-a', 'evidence': 'Detail remained unavailable after passive inspection'}
        with self.assertRaisesRegex(store.StoreError, 'new-passive-observation-required-before-defer'):
            engine.execute('defer-detail', data)
        self.guard.observe(self.page())
        self.assertEqual(engine.execute('defer-detail', data)['decision'], 'deferred')
        self.assertEqual(transport.calls, [])
        flow = self.guard.status()['flow']
        self.assertIsNone(flow['pending'])
        self.assertIsNone(flow['activeKey'])
        self.assertEqual(flow['candidates']['boss:job-a']['decision'], 'deferred')
        self.assertEqual(self.guard.reserve('open-detail', {'key': 'boss:job-b'})['operation'], 'open-detail')

    def test_defer_cannot_discard_an_unresolved_submit(self):
        page = self.ready_to_submit()
        action = store.begin(self.root, self.token, self.request())
        self.guard.reserve('submit', {'key': 'boss:job-a', 'actionId': action['id']})
        self.guard.observe(page)
        pending = deepcopy(self.guard.status()['flow']['pending'])
        transport = ScriptedTransport()
        with self.assertRaisesRegex(store.StoreError, 'only-interrupted-detail-read-can-be-deferred'):
            self.engine(transport).execute('defer-detail', {'key': 'boss:job-a', 'evidence': 'Submission outcome unknown'})
        self.assertEqual(transport.calls, [])
        self.assertEqual(self.guard.status()['flow']['pending'], pending)
        self.assertEqual(store.load_state(self.root)['actions'][action['id']]['status'], 'pending')

    def test_begin_then_crash_before_browser_reserve_reconciles_unknown_and_releases_active(self):
        page = self.ready_to_submit()
        flow = self.guard.status()['flow']
        candidate = flow['candidates']['boss:job-a']
        request = {**self.request(), 'browsingContext': {
            'batchId': flow['batch']['id'], 'listEvidence': candidate['listEvidence'],
            'detailEvidence': candidate['detail']['evidence'], 'reviewEvidence': candidate['reviewEvidence']}}
        action = store.begin(self.root, self.token, request)
        self.assertIsNone(self.guard.status()['flow']['pending'])
        self.restart()
        transport = ScriptedTransport(page)
        result = self.engine(transport).execute('reconcile', {'actionId': action['id']})
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(transport.mutation_calls, [])
        self.assertEqual(len(transport.calls), 1)
        flow = self.guard.status()['flow']
        self.assertIsNone(flow['activeKey'])
        self.assertIsNone(flow['pending'])
        self.assertEqual(flow['candidates']['boss:job-a']['decision'], 'unknown')
        self.assertEqual(flow['candidates']['boss:job-a']['actionId'], action['id'])

    def test_orphan_with_wrong_evidence_cannot_clear_current_candidate(self):
        page = self.ready_to_submit()
        flow = self.guard.status()['flow']
        candidate = flow['candidates']['boss:job-a']
        action = store.begin(self.root, self.token, {**self.request(), 'browsingContext': {
            'batchId': 'unrelated-batch', 'listEvidence': candidate['listEvidence'],
            'detailEvidence': candidate['detail']['evidence']}})
        transport = ScriptedTransport(page)
        self.assertEqual(self.engine(transport).execute('reconcile', {'actionId': action['id']})['status'], 'unknown')
        self.assertEqual(self.guard.status()['flow']['activeKey'], 'boss:job-a')
        self.assertEqual(self.guard.status()['flow']['candidates']['boss:job-a'], candidate)
        self.assertEqual(transport.mutation_calls, [])

    def test_late_receipt_updates_old_candidate_without_disturbing_new_active(self):
        page = self.ready_to_submit()
        unknown = self.engine(ScriptedTransport(page, {'outcome': 'unknown'})).execute('submit', {'request': self.request()})
        self.guard.start_query(self.query)
        self.guard.observe(self.page(('boss:job-b',)))
        self.guard.capture_list()
        self.screen('boss:job-b')
        self.guard.reserve('open-detail', {'key': 'boss:job-b'})
        before = self.guard.status()['flow']
        receipt = deepcopy(page)
        receipt['body'] = '已向BOSS发送消息'
        transport = ScriptedTransport(receipt)
        result = self.engine(transport).execute('reconcile', {'actionId': unknown['id']})
        self.assertEqual(result['status'], 'succeeded')
        after = self.guard.status()['flow']
        self.assertEqual(after['candidates']['boss:job-a']['decision'], 'succeeded')
        for field in ('activeKey', 'pending', 'phase', 'batch'):
            self.assertEqual(after[field], before[field])
        self.assertEqual(after['candidates']['boss:job-b'], before['candidates']['boss:job-b'])
        self.assertEqual(transport.mutation_calls, [])

    def test_control_cannot_change_keyword_city_or_experience_outside_query(self):
        self.guard.start_query(self.query)
        controls = [{'id': 'keyword-input', 'kind': 'keyword', 'label': '搜索职位、公司'},
                    {'id': 'city-shanghai', 'kind': 'filter-option', 'label': '上海', 'filter': 'city'},
                    {'id': 'exp-senior', 'kind': 'filter-option', 'label': '3-5年', 'filter': 'experience'}]
        page = self.page(controls=controls)
        transport = ScriptedTransport(page, page, page)
        engine = self.engine(transport)
        for data, message in [({'id': 'keyword-input', 'value': 'Django'}, 'keyword-control-must-match-query'),
                              ({'id': 'city-shanghai'}, 'city-control-must-match-query'),
                              ({'id': 'exp-senior'}, 'experience-control-must-match-query')]:
            with self.subTest(data=data):
                with self.assertRaisesRegex(store.StoreError, message):
                    engine.execute('control', data)
                self.assertIsNone(self.guard.status()['flow']['pending'])
        self.assertEqual(transport.mutation_calls, [])

    def test_control_can_deselect_existing_extra_experience_then_select_requested_value(self):
        self.guard.start_query(self.query)
        controls = [{'id': 'exp-senior', 'kind': 'filter-option', 'label': '3-5年', 'filter': 'experience'},
                    {'id': 'exp-junior', 'kind': 'filter-option', 'label': '1-3年', 'filter': 'experience'}]
        page = self.page(controls=controls)
        page['filters']['experience'] = ['3-5年']
        self.guard.observe(page)
        self.assertEqual(self.guard.reserve('control', {'id': 'exp-senior'})['operation'], 'control')
        page['filters']['experience'] = []
        self.guard.observe(page)
        self.assertEqual(self.guard.reserve('control', {'id': 'exp-junior'})['operation'], 'control')

    def test_daily_confirmed_target_blocks_submit_before_outbox_or_click(self):
        policy = store.load_policy(self.root)
        policy['search']['dailyTarget'] = 1
        store.write_json(self.root / 'policy.json', policy)
        action = store.begin(self.root, self.token, self.request('boss:already-confirmed'))
        store.resolve(self.root, self.token, action['id'], 'succeeded', 'Fixture confirmed receipt')
        page = self.ready_to_submit()
        transport = ScriptedTransport(page)
        with self.assertRaisesRegex(store.StoreError, 'daily-confirmed-target-reached'):
            self.engine(transport).execute('submit', {'request': self.request()})
        self.assertEqual(len(store.load_state(self.root)['actions']), 1)
        self.assertEqual(transport.mutation_calls, [])
        self.assertIsNone(self.guard.status()['flow']['pending'])


if __name__ == '__main__':
    unittest.main()
