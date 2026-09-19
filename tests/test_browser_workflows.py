from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch
import unittest

from test_browsing_safety import BrowsingFixture
from browser_actions import Engine
import boss_chat
import condition_wait
from operation_metrics import compact
import store
import test_boss_page as dom_fixture


class WorkflowTests(BrowsingFixture):
    def setUp(self):
        super().setUp()
        self.who={'name':'HR','company':'Fixture Software'}
        key='boss:chat:'+hashlib.sha256(json.dumps(self.who,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:24]
        self.request={'kind':'reply','platform':'boss','targetKey':key,'accountLabel':'Fixture Candidate',
            'targetFacts':{'company':'Fixture Software'},'inboundId':'in-1','content':'您好，我使用 Python 开发业务后端。',
            'context':'Reviewed fixture conversation','authorizationEvidence':'Explicit fixture authorization'}
        self.page={'url':'https://www.zhipin.com/web/geek/chat','accountLabel':'Fixture Candidate','body':'正常会话','controls':[{'id':'chat-input'}],
            'chat':{'recipient':self.who,'job':'Python backend','editor':'','rows':[],
                    'messages':[{'id':'in-1','direction':'in','text':'您好','body':'您好','failed':False}]}}
        self.calls=[]
        self.clicked=0
        self.deliver=True
        self.click_unknown=False
        self.change_inbound_on_fill=False
        self.engine=Engine(self.root,self.token,'boss','fixture-session-1',self.transport)

    def transport(self,operation,args):
        self.calls.append(operation)
        value=None
        if operation=='fill':
            self.page['chat']['editor']=args['value']
            if self.change_inbound_on_fill:
                self.page['chat']['messages'].append({'id':'in-2','direction':'in','text':'新问题','body':'新问题','failed':False})
            return {'outcome':'returned','result':{'ok':True,'data':{'success':True}}}
        code=args.get('code','')
        if code.startswith('(async'):
            if self.deliver and self.clicked and not any(m['id']=='out-1' for m in self.page['chat']['messages']):
                self.page['chat']['messages'].append({'id':'out-1','direction':'out','text':self.request['content'],
                    'body':self.request['content'],'delivered':True,'failed':False})
            value={'ready':self.deliver,'samples':[deepcopy(self.page),deepcopy(self.page)],'waitMs':200}
        elif 'const args=' in code:
            self.clicked+=1
            if self.click_unknown:
                return {'outcome':'unknown','result':{'ok':False,'error':{'code':'fixture-disconnect','message':'after click'}}}
            value={'status':'clicked'}
        else:
            value=deepcopy(self.page)
        return {'outcome':'returned','result':{'ok':True,'data':{'value':json.dumps(value)}}}

    def test_reviewed_reply_closes_in_one_entry_and_five_browser_calls(self):
        result=self.engine.execute('reply-and-verify',{'request':self.request})
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(result['metrics']['browserCalls'],5)
        self.assertEqual(self.clicked,1)
        self.assertEqual(result['metrics']['waitMs'],200)
        evidence=store.read_json(self.root/result['metrics']['evidence'])
        self.assertEqual(evidence['result']['status'],'succeeded')

    def test_timeout_remains_unknown_and_second_call_never_resends(self):
        self.deliver=False
        first=self.engine.execute('reply-and-verify',{'request':self.request,'waitMs':0})
        self.assertEqual(first['status'],'unknown')
        self.deliver=True
        self.calls=[]
        second=self.engine.execute('reply-and-verify',{'actionId':first['id']})
        self.assertEqual(second['status'],'succeeded')
        self.assertEqual(self.clicked,1)
        self.assertEqual(self.calls,['evaluate'])

    def test_ambiguous_click_is_reconciled_without_repeat(self):
        self.click_unknown=True
        result=self.engine.execute('reply-and-verify',{'request':self.request})
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(self.clicked,1)
        self.assertEqual(result['metrics']['recoveryCount'],1)
        saved=store.read_json(self.root/result['metrics']['evidence'])
        self.assertTrue(any(c.get('error',{}).get('code')=='fixture-disconnect' for c in saved['calls'] if isinstance(c.get('error'),dict)))

    def test_new_inbound_after_fill_stops_before_click(self):
        self.change_inbound_on_fill=True
        with self.assertRaisesRegex(store.StoreError,'latest-inbound'):
            self.engine.execute('reply-and-verify',{'request':self.request})
        self.assertEqual(self.clicked,0)

    def test_authorization_failure_never_fills_or_clicks(self):
        policy=store.load_policy(self.root);policy['authorization']['reply']='draft'
        store.write_json(self.root/'policy.json',policy)
        with self.assertRaises(store.StoreError):
            self.engine.execute('reply-and-verify',{'request':self.request})
        self.assertNotIn('fill',self.calls)
        self.assertEqual(self.clicked,0)

    def test_metrics_write_failure_does_not_mask_completed_send(self):
        with patch('operation_metrics.Measurement.save',side_effect=OSError('disk unavailable')):
            result=self.engine.execute('reply-and-verify',{'request':self.request})
        self.assertEqual(result['status'],'succeeded')
        self.assertEqual(self.clicked,1)
        self.assertIn('metricsWarning',result)
        self.assertEqual(store.load_state(self.root)['actions'][result['id']]['status'],'succeeded')

    def test_ensure_chat_reuses_current_page_with_chat_observer(self):
        scripts=[]
        original=self.engine._call
        def capture(script,**kwargs):
            scripts.append(script)
            return original(script,**kwargs)
        with patch.object(self.engine,'_call',side_effect=capture):
            result=self.engine.execute('ensure-page',{'page':'chat'})
        self.assertTrue(result['ready'])
        self.assertTrue(result['reused'])
        self.assertEqual(self.calls,['evaluate','evaluate'])
        self.assertTrue(all('#chat-input' in script for script in scripts))

    def test_wrong_recipient_cannot_supply_receipt(self):
        self.deliver=False
        first=self.engine.execute('reply-and-verify',{'request':self.request,'waitMs':0})
        self.page['chat']['recipient']={'name':'Other','company':'Other company'}
        self.deliver=True
        with self.assertRaisesRegex(store.StoreError,'recipient-changed'):
            self.engine.execute('reply-and-verify',{'actionId':first['id']})
        self.assertEqual(self.clicked,1)
        self.assertEqual(store.load_state(self.root)['actions'][first['id']]['status'],'unknown')

    def test_wait_restriction_wins_over_later_normal_page(self):
        blocked={**self.page,'body':'访问受限'}
        with patch.object(self.engine,'_call',return_value={'samples':[blocked,self.page],'ready':True,'waitMs':200}):
            result=condition_wait.observe(self.engine,boss_chat.observation_script(),'return true;',{},500)
        self.assertFalse(result['ready'])
        self.assertIsNotNone(self.guard.status()['accessBlock'])
        raw=store.read_json(self.root/result['observations'][0])
        self.assertEqual(len(raw['samples']),2)

    def test_compact_preserves_model_context_and_raw_evidence(self):
        self.page['chat']['rows']=[{'recipient':self.who,'preview':'您好'}]
        result=self.engine.execute('inspect-chat')
        summary=compact(result)
        self.assertNotIn('body',summary['page'])
        self.assertEqual(summary['page']['chat']['messages'],self.page['chat']['messages'])
        self.assertEqual(summary['page']['chat']['rows'],self.page['chat']['rows'])
        self.assertEqual(store.read_json(self.root/result['metrics']['evidence'])['result']['page']['body'],'正常会话')

    def test_policy_cache_invalidates_even_with_same_stat_and_is_not_mutable(self):
        first=store.load_policy(self.root);first['authorization']['reply']='draft'
        self.assertEqual(store.load_policy(self.root)['authorization']['reply'],'allow')
        file=self.root/'policy.json';before=file.stat()
        store.write_json(file,first)
        os.utime(file,ns=(before.st_atime_ns,before.st_mtime_ns))
        self.assertEqual(store.load_policy(self.root)['authorization']['reply'],'draft')


class WaitDOMTests(unittest.TestCase):
    setUpClass=classmethod(dom_fixture.OfflineDOMTests.setUpClass.__func__)
    run_dom=dom_fixture.OfflineDOMTests.run_dom

    def test_dom_wait_reads_delayed_change_without_clicking(self):
        observation="(() => JSON.stringify({value:document.querySelector('#status').textContent}))()"
        setup="(() => {setTimeout(()=>document.querySelector('#status').textContent='ready',60); return JSON.stringify({});})()"
        wait=condition_wait.script(observation,"return page.value==='ready' ? page.value : false;",{},1000)
        result=self.run_dom([setup,wait],html='<div id="status">loading</div>')
        self.assertTrue(result['values'][1]['ready'])
        self.assertEqual(result['values'][1]['samples'][0]['value'],'loading')
        self.assertEqual(result['calls'],[])
