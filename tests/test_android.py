import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/job-hunter/scripts'))
import android_capture as capture
from android_device import Device
from android_actions import Workflow
import jev
import store


class AndroidTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        store.initialize(self.root)
        self.token = store.run_lock(self.root, 'acquire')['token']

    def test_batch_extracts_all_cards_and_never_treats_abstract_as_full_jd(self):
        cards = [{'jobId': i, 'positionName': {'name': 'Python'}, 'company': 'Company',
                  'jobDesc': {'content': 'short abstract'}} for i in range(20)]
        response = {'code': 0, 'zpData': {
            capture.LIST: {'code': 0, 'zpData': {'cardList': [{'hasMore': True, 'positionSearchCardList': cards}]}},
            '/api/zppassport/auth': {'secret': 'MUST NOT LEAK'},
            capture.DETAIL: {'code': 0, 'zpData': {'jobBaseInfo': {'jobId': 1, 'positionName': 'Python', 'jobDesc': 'full job'},
                                                 'geekHomeAddressInfo': 'PRIVATE'}}}}
        events = capture.materials('/api/batch/requests', response)
        self.assertEqual(len(events[0]['jobs']), 20)
        self.assertFalse(events[0]['jobs'][0]['complete'])
        self.assertTrue(events[1]['job']['complete'])
        self.assertNotIn('PRIVATE', json.dumps(events))
        self.assertNotIn('MUST NOT LEAK', json.dumps(events))

    def test_nonzero_code_cannot_be_successful_receipt(self):
        self.assertEqual(capture.materials(capture.GREET, {'code': 7})[0]['type'], 'error')

    def test_proxy_stop_explicitly_clears_derived_android_proxy(self):
        device = object.__new__(Device)
        device.config = {'proxyPort': 8877}
        device.directory = self.root
        store.write_json(self.root / 'proxy.json', {'proxy': 'null'})
        calls = []
        device.adb = lambda *args: calls.append(args) or ''
        device.capture(stop=True)
        self.assertIn(('shell', 'settings', 'put', 'global', 'http_proxy', ':0'), calls)
        self.assertNotIn(('shell', 'settings', 'delete', 'global', 'http_proxy'), calls)
        self.assertTrue((self.root / 'stop').exists())

    def test_proxy_process_is_not_stopped_when_restore_fails(self):
        device = object.__new__(Device)
        device.config, device.directory = {}, self.root
        store.write_json(self.root / 'proxy.json', {'proxy': 'null'})
        device.adb = lambda *args: '127.0.0.1' if args[-1] == 'global_http_proxy_host' else ''
        with self.assertRaisesRegex(ValueError, 'restore-not-confirmed'):
            device.capture(stop=True)
        self.assertFalse((self.root / 'stop').exists())

    def test_booleans_use_one_question_per_jd_and_one_call(self):
        jobs = [{'key': f'boss:{i}', 'title': 'Python', 'text': 'Backend JD', 'complete': True} for i in range(20)]
        calls = []
        def send(payload):
            calls.append(payload)
            return {'model': 'jev-1.13.0', 'answers': {key: {'type': 'noul', 'noul': .8 if i % 2 else .2}
                    for i, key in enumerate(payload['questions'])}}
        result = jev.evaluate_jobs(self.root, jobs, send=send)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0]['questions']), 20)
        self.assertEqual(sum(row['apply'] for row in result['judgments']), 10)
        self.assertNotIn('search', calls[0]['state'])
        self.assertFalse(store.load_state(self.root)['actions'])

    def test_missing_full_jd_or_partial_answers_never_produce_queue(self):
        with self.assertRaisesRegex(ValueError, 'complete-jds'):
            jev.evaluate_jobs(self.root, [{'key': 'boss:1', 'text': 'abstract', 'complete': False}], send=lambda _: self.fail('API call'))
        with self.assertRaisesRegex(ValueError, 'incomplete-answers'):
            jev.evaluate_jobs(self.root, [{'key': 'boss:1', 'text': 'full', 'complete': True}], send=lambda _: {'model': 'jev-1.13.0', 'answers': {}})

    def test_fixed_scroll_stops_on_new_response_and_preserves_unknown_loading(self):
        class FakeDevice:
            count = 0
            def signature(self): return {'serial': 'test', 'size': [1080, 2400], 'appVersion': '14.170'}
            def ui(self): return None
            def cards(self, tree): return ['visible']
            def mark(self, *args): return {'contextId': 'ctx', 'actionId': 'step'}
            def swipe(self, profile): self.count += 1
            def wait(self, marker, kind, seconds):
                if self.count == 3:
                    return {'id': 'list-2', 'contextId': 'ctx', 'type': 'list', 'jobs': [{'key': 'boss:2', 'complete': False}], 'hasMore': True}
        device = FakeDevice()
        workflow = Workflow(self.root, device, self.token)
        workflow.batch = {'contextId': 'ctx', 'signature': device.signature(), 'jobs': {},
                          'policyFingerprint': store.fingerprint(store.load_policy(self.root)),
                          'profileFingerprint': store.profile_fingerprint(self.root)}
        profile = {'maxSwipes': 5}
        self.assertEqual(workflow.scroll_next(profile)['swipes'], 3)
        self.assertEqual(device.count, 3)
        device.count = 20
        self.assertEqual(workflow.scroll_next(profile)['status'], 'no-new-response')
        self.assertTrue(workflow.batch['hasMore'])

    def test_response_index_is_scoped_to_exact_ui_action(self):
        device = object.__new__(Device)
        device.directory = self.root
        for identifier, action in [('old', 'a'), ('new', 'b')]:
            row = {'id': identifier, 'at': 1, 'type': 'list', 'contextId': 'ctx', 'actionId': action}
            store.write_json(self.root / (identifier + '.json'), row)
            with (self.root / 'index.jsonl').open('a') as stream:
                stream.write(json.dumps(row) + '\n')
        self.assertEqual([row['id'] for row in device.events({'contextId': 'ctx', 'actionId': 'b'})], ['new'])

    def test_batch_send_persists_before_tap_and_stops_on_unknown_without_resend(self):
        policy = store.load_policy(self.root)
        policy['platforms'] = [{'id': 'boss', 'accountLabel': 'Fixture'}]
        policy['authorization'].update(greet='allow', evidence='User authorized batch contact')
        store.write_json(self.root / 'policy.json', policy)
        case = self
        class FakeDevice:
            directory = case.root
            taps = 0
            def signature(self): return {'serial': 'fixture'}
            def ui(self): return ET.fromstring('<hierarchy><node text="立即沟通" /></hierarchy>')
            def button(self, label, tree): return tree[0]
            def mark(self, *args): return {'contextId': 'ctx', 'actionId': str(self.taps)}
            def tap(self, node):
                case.assertEqual(sum(a['status'] == 'pending' for a in store.load_state(case.root)['actions'].values()), 1)
                self.taps += 1
            def wait(self, *args):
                return {'id': 'receipt', 'code': 0, 'httpStatus': 200} if self.taps == 1 else None
            def back(self): pass
            def return_to_list(self): pass
        device = FakeDevice()
        workflow = Workflow(self.root, device, self.token)
        jobs = {f'boss:{i}': {'key': f'boss:{i}', 'title': 'Python', 'company': 'Fixture', 'text': 'Full JD',
                             'complete': True, 'detailEvidence': 'detail'} for i in range(3)}
        workflow.batch = {'contextId': 'ctx', 'accountLabel': 'Fixture', 'signature': device.signature(),
                          'policyFingerprint': store.fingerprint(policy), 'profileFingerprint': store.profile_fingerprint(self.root),
                          'jobs': jobs, 'decisions': {key: {'apply': True} for key in jobs}, 'results': {}}
        workflow.open_job = lambda job: job
        self.assertEqual(workflow.apply()['count'], 3)
        self.assertEqual(device.taps, 0)
        self.assertEqual(workflow.apply(dry=False)['status'], 'unknown')
        self.assertEqual(device.taps, 2)
        self.assertEqual([a['status'] for a in store.load_state(self.root)['actions'].values()], ['succeeded', 'unknown'])
        # Re-running a batch with an unresolved send must not advance to its remaining jobs.
        self.assertEqual(workflow.apply(dry=False)['status'], 'unknown')
        self.assertEqual(device.taps, 2)


if __name__ == '__main__':
    unittest.main()
