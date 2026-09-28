import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/job-hunter/scripts'))
from android_actions import Workflow
from android_device import Device
from android_ui import selected_options
import store


class PreviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        store.initialize(self.root)
        self.token = store.run_lock(self.root, 'acquire')['token']
        self.device = Mock()
        self.w = Workflow(self.root, self.device, self.token)
        self.w.check = lambda: store.load_state(self.root)
        self.w.batch = {'jobs': {f'boss:{i}': {'key': f'boss:{i}', 'complete': False} for i in range(5)},
                        'decisions': {}, 'results': {}}
        self.w.open_job = lambda j: {**j, 'complete': True, 'text': 'Full JD', 'detailEvidence': j['key']}

    def test_limit_stops_details_and_retains_unread_cards(self):
        with patch('android_actions.check_target'):
            self.w.collect(2, excluded=['boss:0'])
        self.assertEqual(self.w.batch['selection'], ['boss:1', 'boss:2'])
        self.assertEqual(self.device.back.call_count, 2)
        self.assertEqual(len(self.w.batch['jobs']), 5)
        self.assertFalse(self.w.batch['jobs']['boss:3']['complete'])
        with patch('android_actions.check_target'):
            self.w.collect(2, excluded=['boss:0'])
        self.assertEqual(self.device.back.call_count, 2)

    def prepare_decision(self):
        self.w.batch['selection'] = ['boss:1', 'boss:2']
        for key in self.w.batch['selection']:
            self.w.batch['jobs'][key].update(complete=True, text='Full JD')
        return {'judgments': [{'key': k, 'apply': True} for k in self.w.batch['selection']],
                'elapsedMs': 10, 'usage': {}, 'providerCalls': 1}

    def test_salary_switch_collects_only_current_pool_and_deduplicates(self):
        self.w.batch['activeKeys'] = ['boss:0', 'boss:1']
        with patch('android_actions.check_target'):
            self.w.collect(1)
            self.w.batch['activeKeys'] = ['boss:0', 'boss:3', 'boss:4']
            self.w.collect(2)
        self.assertEqual(self.w.batch['selection'], ['boss:0', 'boss:3'])
        self.assertFalse(self.w.batch['jobs']['boss:1']['complete'])
        self.assertEqual(self.device.back.call_count, 2)

    def test_evaluation_uses_only_selected_full_jds_and_reuses_success(self):
        result = self.prepare_decision()
        with patch('android_actions.jev.api_key', return_value='fixture'), patch('android_actions.jev.evaluate_jobs', return_value=result) as call:
            self.w.evaluate()
            self.w.evaluate()
        self.assertEqual(call.call_count, 1)
        self.assertEqual([j['key'] for j in call.call_args.args[1]], ['boss:1', 'boss:2'])
        self.assertEqual(self.w.apply()['keys'], ['boss:1', 'boss:2'])

    def test_lost_provider_result_is_not_retried(self):
        self.prepare_decision()
        with patch('android_actions.jev.api_key', return_value='fixture'), patch('android_actions.jev.evaluate_jobs', side_effect=ValueError('connection-lost')) as call:
            with self.assertRaisesRegex(ValueError, 'connection-lost'):
                self.w.evaluate()
            with self.assertRaisesRegex(ValueError, 'do-not-repeat'):
                self.w.evaluate()
        self.assertEqual(call.call_count, 1)
        self.assertEqual(store.read_json(self.w.path)['evaluation']['status'], 'pending')

    def test_saved_result_recovers_after_crash_before_decision_commit(self):
        result = self.prepare_decision()
        pending = [self.w.batch['jobs'][k] for k in self.w.batch['selection']]
        evidence = self.root / 'result.json'
        store.write_json(evidence, result)
        self.w.batch['evaluation'] = {'status': 'pending', 'inputFingerprint': store.fingerprint(pending), 'evidence': str(evidence)}
        with patch('android_actions.jev.evaluate_jobs', side_effect=AssertionError('must not request again')):
            self.assertEqual(self.w.evaluate()['providerCalls'], 0)
        self.assertEqual(len(self.w.batch['decisions']), 2)

    def test_homepage_fill_readback_ignores_false_accessibility_flags(self):
        tree = ET.fromstring('<hierarchy>' + ''.join(
            f'<node bounds="[{x},100][{x+300},210]"><node text="{label}" resource-id="com.hpbr.bosszhipin:id/tv_option" selected="false"/></node>'
            for x, label in [(0, '12-16K'), (300, '16-20K')]) + '</hierarchy>')
        picture = Mock()
        picture.getpixel = lambda xy: (227,246,246) if xy[0] < 300 else (245,245,245)
        self.assertEqual(selected_options(tree, picture, Device), ['12-16K'])


if __name__ == '__main__':
    unittest.main()
