from copy import deepcopy
import json
from unittest.mock import patch

from test_browsing_safety import BrowsingFixture, ScriptedTransport
from browser_actions import Engine
from operation_metrics import Measurement, compact
import detail_groups
import jev
import store


class BatchTests(BrowsingFixture):
    def setUp(self):
        super().setUp()
        self.keys=['boss:job-a','boss:job-b']
        self.batch(self.keys)
        self.engine=Engine(self.root,self.token,'boss','fixture-session-1',lambda *args: self.fail('unexpected platform request'))

    def reviews(self):
        return {'batchId':self.guard.status()['flow']['batch']['id'],
                'reviews':[{'key':k,'decision':'shortlisted','evidence':'reviewed card'} for k in self.keys]}

    def test_screen_many_commits_once_and_matches_individual_decisions(self):
        with patch.object(self.guard,'_save',wraps=self.guard._save) as save:
            result=self.guard.screen_many(self.reviews())
        self.assertEqual(save.call_count,1)
        self.assertEqual([r['decision'] for r in result['decisions']],['shortlisted']*2)
        self.assertFalse(store.load_state(self.root)['actions'])

    def test_invalid_batch_item_or_stale_batch_never_partially_commits(self):
        original=(self.root/'state.json').read_bytes()
        for mutation in ('outside','duplicate','stale'):
            data=self.reviews()
            if mutation=='outside': data['reviews'][1]['key']='boss:outside'
            if mutation=='duplicate': data['reviews'][1]['key']=self.keys[0]
            if mutation=='stale': data['batchId']='old-batch'
            with self.assertRaises(store.StoreError): self.guard.screen_many(data)
            self.assertEqual((self.root/'state.json').read_bytes(),original)

    def test_bulk_review_rejects_changed_policy_before_saving_skips(self):
        data=self.reviews()
        for review in data['reviews']: review['decision']='skipped'
        policy=store.load_policy(self.root)
        policy['objective']='new criteria'
        store.write_json(self.root/'policy.json',policy)
        original=(self.root/'state.json').read_bytes()
        with self.assertRaisesRegex(store.StoreError,'policy-changed'):
            self.guard.screen_many(data)
        self.assertEqual((self.root/'state.json').read_bytes(),original)

    def test_detail_business_step_uses_three_calls_and_keeps_complete_evidence(self):
        self.screen(self.keys[0])
        before=self.page(self.keys)
        after=self.page(self.keys,detail={'key':self.keys[0],'text':'Complete backend job description'})
        transport=ScriptedTransport(before,{'status':'clicked'},
                                {'ready':True,'page':after,'polls':2,'waitMs':200})
        engine=Engine(self.root,self.token,'boss','fixture-session-1',transport)
        result=engine.execute('open-detail-and-wait',{'key':self.keys[0]})
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['metrics']['browserCalls'],3)
        summary=compact(result,'open-detail-and-wait')
        self.assertNotIn('cards',summary['page'])
        self.assertEqual(summary['page']['detail']['text'],after['detail']['text'])
        self.assertEqual(store.read_json(self.root/summary['evidence'])['page'],after)

    def test_failed_detail_click_never_runs_wait_or_marks_ready(self):
        self.screen(self.keys[0])
        transport=ScriptedTransport(self.page(self.keys),{'status':'unsupported','reason':'not-found'})
        engine=Engine(self.root,self.token,'boss','fixture-session-1',transport)
        result=engine.execute('open-detail-and-wait',{'key':self.keys[0]})
        self.assertEqual(result['status'],'unsupported')
        self.assertEqual(len(transport.calls),2)

    def test_compact_resume_does_not_repeat_saved_job_bodies(self):
        flow=self.guard.status()['flow']
        flow['detailGroup']={'id':'group','keys':self.keys,'details':{
            self.keys[0]:{'key':self.keys[0],'text':'large JD','evidence':'saved.json'}}}
        view=compact({'flow':flow},'resume-context')
        self.assertNotIn('large JD',json.dumps(view))
        self.assertEqual(view['flow']['detailGroup']['details'][self.keys[0]]['evidence'],'saved.json')
        flow['detailGroup']['reviews']={self.keys[0]:{'decision':'skipped','evidence':'large review text'}}
        state=store.load_state(self.root)
        state['browsing']['boss']=flow
        store.write_json(self.root/'state.json',state)
        resumed=compact(self.engine.execute('resume-context'),'resume-context')
        self.assertEqual(resumed['flow']['detailGroup']['decisions'],{self.keys[0]:'skipped'})
        self.assertNotIn('large review text',json.dumps(resumed))
        self.assertNotIn('candidateCount',resumed['flow'])

    def test_metrics_reference_nested_material_without_copying_it(self):
        result={'status':'ready','group':{'details':{'key':{'text':'large JD','evidence':'logs/browsing/jd.json'}}},
                'step':{'observation':{'page':{'body':'large DOM'},'evidence':'logs/browsing/page.json'}}}
        metrics=Measurement('collect-details','fixture-session-1').save(self.root,result)
        saved=store.read_json(self.root/metrics['evidence'])
        self.assertEqual(saved['result'],{'status':'ready'})
        self.assertEqual(saved['evidence'],['logs/browsing/jd.json','logs/browsing/page.json'])
        self.assertNotIn('large',json.dumps(saved))

    def response(self,payload):
        self.payloads.append(deepcopy(payload))
        answers={key:{'type':'noul','noul':.8} for key in payload['questions']}
        return {'model':'jev-1.13.0','answers':answers,'usage':{'input_tokens':100,'output_tokens':30}}

    def prepare_details(self):
        self.payloads=[]
        self.guard.screen_many(self.reviews())
        state=store.load_state(self.root)
        flow=state['browsing']['boss']
        flow['detailGroup']={'id':'group','batchId':flow['batch']['id'],'keys':self.keys,
            'details':{key:{'key':key,'text':'Full JD','evidence':'fixture'} for key in self.keys},
            'reviews':{},'policyFingerprint':flow['policyFingerprint'],'profileFingerprint':flow['profileFingerprint']}
        store.write_json(self.root/'state.json',state)

    def evaluate(self,data=None,send=None):
        return jev.execute(self.engine,data or {},send or self.response)

    def test_jev_batches_all_complete_jobs_in_one_call_without_sending(self):
        self.prepare_details()
        result=self.evaluate()
        self.assertEqual(result['status'],'evaluated')
        self.assertEqual(len(self.payloads),1)
        self.assertEqual(len(self.payloads[0]['questions']),2)
        self.assertIn('jobs[1]',self.payloads[0]['questions']['decision_1']['instructions'])
        self.assertFalse(store.load_state(self.root)['actions'])
        self.assertEqual(len(self.guard.status()['flow']['detailGroup']['reviews']),2)
        self.assertNotIn('authorization',self.payloads[0]['state'])

    def test_jev_pinned_cache_reuses_only_identical_material_and_alias_keeps_evidence(self):
        self.prepare_details()
        original=store.load_state(self.root)
        # A saved provider result survives a failed local review commit.
        with patch.object(detail_groups,'execute',side_effect=OSError('interrupted review')):
            with self.assertRaises(OSError):
                self.evaluate(data={'model':'jev-1.13.0'})
        second=self.evaluate(data={'model':'jev-1.13.0'})
        self.assertTrue(second['cached'])
        self.assertEqual(second['providerCalls'],0)
        self.assertEqual(second['usage'],{'input_tokens':0,'output_tokens':0})
        self.assertEqual(len(self.payloads),1)
        store.write_json(self.root/'state.json',original)
        alias1=self.evaluate()
        store.write_json(self.root/'state.json',original)
        alias2=self.evaluate()
        self.assertFalse(alias2['cached'])
        self.assertNotEqual(alias1['evidence'],alias2['evidence'])
        original['browsing']['boss']['detailGroup']['details'][self.keys[0]]['text']='Changed full JD'
        store.write_json(self.root/'state.json',original)
        changed=self.evaluate(data={'model':'jev-1.13.0'})
        self.assertFalse(changed['cached'])

    def test_jev_rejects_policy_change_while_waiting(self):
        self.prepare_details()
        def changed(payload):
            result=self.response(payload)
            policy=store.load_policy(self.root)
            policy['objective']='changed'
            store.write_json(self.root/'policy.json',policy)
            return result
        with self.assertRaisesRegex(store.StoreError,'policy-changed'):
            self.evaluate(send=changed)
        self.assertFalse(list((self.root/'logs').glob('jev/*.json')))

    def test_jev_malformed_or_missing_answers_never_become_reviews(self):
        self.prepare_details()
        def missing(payload):
            result=self.response(payload)
            result['answers'].pop('decision_1')
            return result
        result=self.evaluate(send=missing)
        self.assertEqual(result['status'],'unavailable')
        self.assertEqual(result['reason'],'jev-incomplete-answers')
        self.assertNotIn('judgments',result)
        self.assertFalse(store.load_state(self.root)['actions'])

    def test_jev_details_uses_boolean_and_records_batch_decisions(self):
        self.prepare_details()
        result=self.evaluate()
        self.assertEqual(len(self.payloads),1)
        self.assertEqual(len(self.payloads[0]['questions']),2)
        self.assertEqual(len(result['judgments']),2)
        self.assertNotIn('eligibilityPassed',result['judgments'][0])
        self.assertEqual(len(self.guard.status()['flow']['detailGroup']['reviews']),2)
        self.assertTrue(all(row['apply'] for row in result['judgments']))

    def test_review_ack_omits_jd_but_preserves_stored_material(self):
        self.test_jev_details_uses_boolean_and_records_batch_decisions()
        state=store.load_state(self.root)
        state['browsing']['boss']['detailGroup']['reviews']={}
        store.write_json(self.root/'state.json',state)
        result=detail_groups.execute(self.engine,'review-detail-group',{'groupId':'group','reviews':[
            {'key':key,'decision':'skipped','evidence':'reviewed mismatch'} for key in self.keys]})
        self.assertNotIn('Full JD',json.dumps(result))
        self.assertEqual(self.guard.status()['flow']['detailGroup']['details'][self.keys[0]]['text'],'Full JD')
