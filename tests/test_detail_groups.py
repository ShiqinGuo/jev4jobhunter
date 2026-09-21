from copy import deepcopy
from unittest.mock import patch

from test_browsing_safety import BrowsingFixture
from browser_actions import Engine
import detail_groups
import store


class DetailGroupTests(BrowsingFixture):
    def setUp(self):
        super().setUp()
        self.keys = ['boss:job-a', 'boss:job-b']
        self.batch(self.keys)
        for key in self.keys:
            self.screen(key)
        self.engine = Engine(self.root, self.token, 'boss', 'fixture-session-1')
        self.calls = []
        self.changed = False
        self.fail_key = None

    def step(self, operation, data=None):
        self.calls.append((operation, deepcopy(data)))
        if operation == 'open-detail-and-wait':
            key = data['key']
            self.guard.reserve('open-detail', {'key': key})
            if key == self.fail_key:
                return {'status': 'pending'}
            observed = self.guard.observe(self.page(self.keys, detail={
                'key': key, 'text': ('changed' if self.changed else 'Python backend ') + key}))
            return {'status': 'ready', 'observation': observed}
        if operation == 'inspect':
            return self.guard.observe(self.page(self.keys, detail={'key': self.keys[0], 'text': 'Python backend ' + self.keys[0]}))
        if operation == 'submit':
            return {'status': 'fixture-submit-guard-reached'}
        raise AssertionError(operation)

    def collect(self):
        with patch.object(self.engine, 'execute', side_effect=self.step):
            return detail_groups.execute(self.engine, 'collect-details', {'keys': self.keys})

    def review(self, group):
        return detail_groups.execute(self.engine, 'review-detail-group', {'groupId': group['id'],
            'reviews': [{'key': k, 'decision': 'apply', 'eligibilityPassed': True, 'evidence': 'model JD review'} for k in self.keys]})

    def test_collects_sequentially_without_outbound_and_resumes_without_reopen(self):
        group = self.collect()['group']
        self.assertEqual(list(group['details']), self.keys)
        self.assertEqual([c[0] for c in self.calls], ['open-detail-and-wait'] * 2)
        self.assertFalse(store.load_state(self.root)['actions'])
        self.assertIsNone(self.guard.status()['flow']['activeKey'])
        self.collect()
        self.assertEqual(len(self.calls), 2)
        with self.assertRaisesRegex(store.StoreError, 'finish-current-batch'):
            self.guard.reserve('scroll', {})

    def test_cap_and_unscreened_rejected_before_browser(self):
        for keys in [[], self.keys * 3, ['boss:outside'], ['boss:job-a'] * 2]:
            with self.assertRaises(store.StoreError):
                detail_groups.execute(self.engine, 'collect-details', {'keys': keys})
        self.assertFalse(self.calls)

    def test_interruption_preserves_first_detail_and_pending_second(self):
        self.fail_key = self.keys[1]
        result = self.collect()
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(list(result['group']['details']), [self.keys[0]])
        self.assertEqual(self.guard.status()['flow']['pending']['args']['key'], self.keys[1])
        self.assertFalse(store.load_state(self.root)['actions'])

    def test_existing_loaded_detail_is_collected_without_click(self):
        self.guard.reserve('open-detail', {'key': self.keys[0]})
        self.guard.observe(self.page(self.keys, detail={'key': self.keys[0], 'text': 'Python backend ' + self.keys[0]}))
        self.collect()
        self.assertEqual([c[0] for c in self.calls], ['inspect', 'open-detail-and-wait'])

    def test_failed_read_can_be_deferred_without_losing_collected_review(self):
        self.fail_key = self.keys[1]
        group = self.collect()['group']
        self.guard.observe(self.page(self.keys))
        self.guard.defer_detail({'key': self.keys[1], 'evidence': 'fresh observation still lacks matching detail'})
        result = detail_groups.execute(self.engine, 'review-detail-group', {'groupId': group['id'],
            'reviews': [{'key': self.keys[0], 'decision': 'skipped', 'evidence': 'model reviewed mismatch'}]})
        self.assertEqual(result['status'], 'reviewed')
        self.assertTrue(self.guard.batch_done(self.guard.status()['flow']))

    def test_group_review_does_not_send_and_fresh_matching_detail_required(self):
        group = self.collect()['group']
        self.review(group)
        self.assertFalse(store.load_state(self.root)['actions'])
        with patch.object(self.engine, 'execute', side_effect=self.step):
            result = detail_groups.execute(self.engine, 'submit-reviewed-detail', {'request': self.request()})
        self.assertEqual(result['status'], 'fixture-submit-guard-reached')
        self.assertEqual([c[0] for c in self.calls[-2:]], ['open-detail-and-wait', 'submit'])

    def test_changed_jd_stops_before_submit(self):
        self.review(self.collect()['group'])
        self.changed = True
        with patch.object(self.engine, 'execute', side_effect=self.step):
            result = detail_groups.execute(self.engine, 'submit-reviewed-detail', {'request': self.request()})
        self.assertEqual(result['status'], 'review-required')
        self.assertNotIn('submit', [c[0] for c in self.calls])

    def test_restriction_stops_collection(self):
        self.guard.observe(self.page(body='访问受限'))
        with self.assertRaisesRegex(store.StoreError, 'access-blocked'):
            self.collect()
        self.assertFalse(self.calls)

    def test_changed_policy_stops_reviewed_send_before_browser(self):
        self.review(self.collect()['group'])
        policy = store.load_policy(self.root)
        policy['authorization']['greet'] = 'draft'
        store.write_json(self.root / 'policy.json', policy)
        before = len(self.calls)
        with patch.object(self.engine, 'execute', side_effect=self.step):
            with self.assertRaises(store.StoreError):
                detail_groups.execute(self.engine, 'submit-reviewed-detail', {'request': self.request()})
        self.assertEqual(len(self.calls), before)

    def test_incomplete_review_is_atomic(self):
        group = self.collect()['group']
        with self.assertRaises(store.StoreError):
            detail_groups.execute(self.engine, 'review-detail-group', {'groupId': group['id'], 'reviews': []})
        self.assertEqual(self.guard.status()['flow']['detailGroup']['reviews'], {})

    def test_chat_return_reordering_preserves_old_group_without_blocking_new_batch(self):
        old_keys = list(self.keys)
        old_group = self.collect()['group']
        self.review(old_group)
        self.guard.restore_filters({})
        self.keys = ['boss:job-c']
        self.guard.observe(self.page(self.keys))
        self.guard.capture_list()
        self.screen(self.keys[0])
        new_group = self.collect()['group']
        flow = self.guard.status()['flow']
        self.assertNotEqual(new_group['id'], old_group['id'])
        self.assertEqual(flow['retainedDetailGroups'][0]['id'], old_group['id'])
        self.assertTrue(set(old_keys).issubset(flow['backlog']))
        self.assertFalse(store.load_state(self.root)['actions'])
        with self.assertRaisesRegex(store.StoreError, 'reviewed-group-candidate-required'):
            detail_groups.execute(self.engine, 'submit-reviewed-detail', {'request': self.request(old_keys[0])})
