"""Regression checks for normal chat capability and send identity boundaries."""
from copy import deepcopy
from unittest.mock import patch
from test_browsing_safety import BrowsingFixture
from browser_actions import Engine
import boss_chat
import store
import hashlib
import json
import unittest
import test_boss_page as dom_fixture


class ChatTests(BrowsingFixture):
    def setUp(self):
        super().setUp()
        self.who = {'name':'Recruiter', 'company':'Fixture Software'}
        self.key = 'boss:chat:' + hashlib.sha256(json.dumps(self.who,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:24]
        self.observed = {'classification':'normal','evidence':'logs/browsing/chat-fixture.json','page':{
            'url':'https://www.zhipin.com/web/geek/chat', 'accountLabel':'Fixture Candidate',
            'chat':{'recipient':self.who,'job':'Python backend','editor':'','messages':[
                {'id':'in-1','direction':'in','text':'请发简历','body':'请发简历','failed':False}]}}}
        self.engine = Engine(self.root,self.token,'boss','fixture-session-1')
        self.req = {'kind':'reply','platform':'boss','targetKey':self.key,
            'accountLabel':'Fixture Candidate','targetFacts':{'company':'Fixture Software'},
            'inboundId':'in-1','content':'您好，我主要做 Python 后端开发。',
            'context':'read complete conversation','authorizationEvidence':'explicit authorization'}

    def prepare(self):
        return boss_chat.prepare(self.engine,{'request':self.req},self.observed)

    def attempted(self, action_id):
        state=store.load_state(self.root)
        state['actions'][action_id]['chatAttempted']=store.stamp()
        store.write_json(self.root/'state.json',state)
        return state['actions'][action_id]

    def test_chat_does_not_require_search_batch(self):
        self.assertEqual(self.prepare()['status'],'pending')

    def test_unattempted_action_cannot_claim_new_bubble(self):
        action_id=self.prepare()['id']
        action=store.load_state(self.root)['actions'][action_id]
        with patch.object(boss_chat,'inspect') as inspect:
            result=boss_chat.reconcile(self.engine,action)
        self.assertFalse(result['submissionAttempted'])
        inspect.assert_not_called()

    def test_alias_handover_between_prepare_and_send(self):
        action_id=self.prepare()['id']
        action=store.load_state(self.root)['actions'][action_id]
        store.update(self.root,self.token,'threads','boss:alias',
            {'company':self.who['company'],'hrName':self.who['name'],'humanTakenOver':True})
        with self.assertRaisesRegex(store.StoreError,'thread-paused'):
            boss_chat.check_current(self.engine,action,self.observed['page'])
        action['oneShotHandover']=True
        boss_chat.check_current(self.engine,action,self.observed['page'])

    def test_new_manual_outbound_and_new_inbound_stop_prepared_send(self):
        action_id=self.prepare()['id']
        action=store.load_state(self.root)['actions'][action_id]
        page=deepcopy(self.observed['page'])
        page['chat']['messages'].append({'id':'manual','direction':'out'})
        with self.assertRaisesRegex(store.StoreError,'new-outbound'):
            boss_chat.check_current(self.engine,action,page)
        page['chat']['messages'][-1]['direction']='in'
        with self.assertRaisesRegex(store.StoreError,'latest-inbound'):
            boss_chat.check_current(self.engine,action,page)

    def test_restart_keeps_consent_wait_even_if_toolbar_no_longer_waiting(self):
        action=self.attempted(self.prepare()['id'])
        action.update(kind='share_resume',resumeFilename='resume.pdf',deliveryState='awaiting-recipient-consent')
        state=store.load_state(self.root)
        state['actions'][action['id']]=action
        store.write_json(self.root/'state.json',state)
        store.run_lock(self.root,'release',self.token)
        token=store.run_lock(self.root,'acquire')['token']
        engine=Engine(self.root,token,'boss','fixture-session-1')
        with patch.object(boss_chat,'inspect',return_value=self.observed):
            result=boss_chat.reconcile(engine,action)
        self.assertEqual(result['status'],'pending')
        self.assertEqual(result['deliveryState'],'awaiting-recipient-consent')

    def test_confirm_requires_owned_toolbar_stage(self):
        action=self.attempted(self.prepare()['id'])
        action.update(kind='share_resume',resumeFilename='resume.pdf')
        state=store.load_state(self.root); state['actions'][action['id']]=action
        store.write_json(self.root/'state.json',state)
        observed=deepcopy(self.observed); observed['page']['chat']['resumeConfirmation']=True
        with patch.object(boss_chat,'inspect',return_value=observed), patch.object(boss_chat,'check_current'), patch.object(self.engine,'_call') as call:
            with self.assertRaisesRegex(store.StoreError,'confirmation-already'):
                boss_chat.execute(self.engine,'confirm-resume',{'actionId':action['id']})
        call.assert_not_called()

    def test_platform_resume_changed_after_prepare(self):
        action=self.attempted(self.prepare()['id'])
        action.update(kind='share_resume')
        action['chatContext']['platformResume']={'filenames':['old.pdf'],'resumeSha256':'old'}
        with self.assertRaisesRegex(store.StoreError,'platform-resume-version'):
            boss_chat.check_current(self.engine,action,self.observed['page'])

    def test_changed_inbound_rejected(self):
        self.req['inboundId']='stale'
        with self.assertRaisesRegex(store.StoreError,'latest-inbound'):
            self.prepare()

    def test_account_and_recipient_must_match(self):
        self.req['targetFacts']['company']='Other'
        with self.assertRaisesRegex(store.StoreError,'company-mismatch'):
            self.prepare()
        with self.assertRaisesRegex(store.StoreError,'recipient-changed'):
            boss_chat.verify(self.engine,self.observed,{'name':'Someone else','company':'Fixture Software'})

    def test_unresolved_alias_cannot_replay(self):
        self.prepare()
        self.req['targetKey']='boss:alias'
        with self.assertRaisesRegex(store.StoreError,'duplicate-or-unresolved-inbound'):
            self.prepare()

    def test_old_bubble_and_cleared_editor_are_not_receipts(self):
        action_id=self.prepare()['id']
        action=self.attempted(action_id)
        with patch.object(boss_chat,'inspect',return_value=self.observed):
            self.assertEqual(boss_chat.reconcile(self.engine,action)['status'],'unknown')

    def test_new_matching_outbound_verifies_delayed_send(self):
        action_id=self.prepare()['id']
        action=self.attempted(action_id)
        after=deepcopy(self.observed)
        after['page']['chat']['messages'].append({'id':'out-2','direction':'out','text':self.req['content'],'body':self.req['content'],'failed':False,'delivered':True})
        with patch.object(boss_chat,'inspect',return_value=after):
            self.assertEqual(boss_chat.reconcile(self.engine,action)['status'],'succeeded')

    def test_failed_bubble_does_not_verify(self):
        action_id=self.prepare()['id']
        action=self.attempted(action_id)
        after=deepcopy(self.observed)
        after['page']['chat']['messages'].append({'id':'out-2','direction':'out','text':self.req['content'],'body':self.req['content'],'failed':True})
        with patch.object(boss_chat,'inspect',return_value=after):
            self.assertEqual(boss_chat.reconcile(self.engine,action)['status'],'unknown')

    def test_resume_request_is_pending_not_attachment_delivered(self):
        action_id=self.prepare()['id']
        action=self.attempted(action_id)
        action.update(kind='share_resume',resumeFilename='resume.pdf')
        after=deepcopy(self.observed)
        after['page']['chat']['resumeWaiting']=True
        with patch.object(boss_chat,'inspect',return_value=after):
            result=boss_chat.reconcile(self.engine,action)
        self.assertEqual(result['status'],'pending')
        self.assertFalse(result['attachmentDelivered'])

    def test_request_receipt_without_waiting_toolbar_is_not_delivered(self):
        action=self.attempted(self.prepare()['id'])
        action.update(kind='share_resume',resumeFilename='resume.pdf')
        after=deepcopy(self.observed)
        after['page']['chat']['messages'].append({'id':'request-2','direction':'system','text':'',
            'body':'附件简历请求已发送','failed':False})
        with patch.object(boss_chat,'inspect',return_value=after):
            result=boss_chat.reconcile(self.engine,action)
        self.assertEqual(result['deliveryState'],'awaiting-recipient-consent')
        with self.assertRaisesRegex(store.StoreError,'reconcile-only'):
            boss_chat.check_current(self.engine,action,after['page'])

    def test_truncated_resume_receipt_needs_verified_platform_copy(self):
        action_id=self.prepare()['id']
        action=self.attempted(action_id)
        action.update(kind='share_resume',resumeFilename='candidate-resume.pdf')
        after=deepcopy(self.observed)
        after['page']['chat']['messages'].append({'id':'receipt-2','direction':'system','text':'',
            'body':'您的附件简历 candid... 已发送给Boss点击查看附件','failed':False})
        with patch.object(boss_chat,'inspect',return_value=after):
            self.assertEqual(boss_chat.reconcile(self.engine,action)['status'],'unknown')
        action['chatContext']['platformResume']={'filenames':['candidate-resume.pdf']}
        with patch.object(boss_chat,'inspect',return_value=after):
            self.assertEqual(boss_chat.reconcile(self.engine,action)['status'],'succeeded')


class ChatDOMTests(unittest.TestCase):
    setUpClass=classmethod(dom_fixture.OfflineDOMTests.setUpClass.__func__)
    run_dom=dom_fixture.OfflineDOMTests.run_dom

    def test_accept_resume_request_does_not_accept_phone_or_other_message(self):
        html='''<div class="nav-figure"><span class="label">Candidate</span></div>
        <div class="friend-content selected"><div class="name-box"><span class="name-text">HR</span><span>Company</span></div></div>
        <div class="user-info"><div class="base-info"><div><span class="name-text">HR</span></div><span>Company</span></div></div>
        <ul class="im-list">
        <li class="message-item" data-mid="phone">Boss请求交换电话<a id="phone">同意</a></li>
        <li class="message-item" data-mid="request1">Boss请求您的附件简历<a id="resume">同意</a></li></ul>'''
        args={'recipient':{'name':'HR','company':'Company'},'accountLabel':'Candidate',
              'inboundId':'request1','resumeMode':'accept-request','chatContext':{'latestInboundId':None}}
        output=self.run_dom([boss_chat.observation_script(),boss_chat.action_script('share_resume',args)],html=html,url='https://www.zhipin.com/web/geek/chat')
        self.assertEqual(output['values'][0]['chat']['resumeRequests'][0]['id'],'request1')
        self.assertEqual(output['calls'],[{'kind':'click','id':'resume'}])
        args['inboundId']='phone'
        output=self.run_dom([boss_chat.action_script('share_resume',args)],html=html,url='https://www.zhipin.com/web/geek/chat')
        self.assertEqual(output['calls'],[])

        html=html.replace('Boss请求您的附件简历<a id="resume">同意</a>',
            '我想要一份您的附件简历，您是否同意<span><span id="resume">同意</span></span>')
        args['inboundId']='request1'
        output=self.run_dom([boss_chat.observation_script(),boss_chat.action_script('share_resume',args)],html=html,url='https://www.zhipin.com/web/geek/chat')
        self.assertEqual(output['values'][0]['chat']['resumeRequests'][0]['id'],'request1')
        self.assertEqual(output['calls'],[{'kind':'click','id':'resume'}])

    def test_scoped_send_ignores_hidden_duplicate_and_wrong_recipient(self):
        html='''<div class="nav-figure"><span class="label">Candidate</span></div>
        <div class="friend-content selected"><div class="name-box"><span class="name-text">HR</span><span>Company</span></div></div>
        <div class="user-info"><div class="base-info"><div><span class="name-text">HR</span></div><span>Company</span></div></div>
        <ul class="im-list"><li class="message-item item-friend" data-mid="in1"><span class="text-content">您好</span></li></ul>
        <div class="chat-editor"><div id="chat-input" contenteditable="true">您好</div>
        <button id="visible-send" class="btn-send">发送</button>
        <button id="hidden-send" class="btn-send" style="display:none">发送</button></div>'''
        args={'recipient':{'name':'HR','company':'Company'},'accountLabel':'Candidate','inboundId':'in1','content':'您好'}
        output=self.run_dom([boss_chat.observation_script(),boss_chat.action_script('reply',args)],html=html,url='https://www.zhipin.com/web/geek/chat')
        self.assertEqual(output['values'][0]['chat']['recipient'],args['recipient'])
        self.assertEqual(output['calls'],[{'kind':'click','id':'visible-send'}])
        args['recipient']['name']='Other'
        output=self.run_dom([boss_chat.action_script('reply',args)],html=html,url='https://www.zhipin.com/web/geek/chat')
        self.assertEqual(output['calls'],[])
        self.assertEqual(output['values'][0]['status'],'unsupported')

    def test_resume_confirm_cannot_click_phone_confirmation(self):
        html='''<div class="nav-figure"><span class="label">Candidate</span></div>
        <div class="friend-content selected"><div class="name-box"><span class="name-text">HR</span><span>Company</span></div></div>
        <div class="user-info"><div class="base-info"><div><span class="name-text">HR</span></div><span>Company</span></div></div>
        <ul class="im-list"><li class="message-item item-friend" data-mid="in1">简历</li></ul>
        <div class="panel-contact">确认交换电话<span class="btn-sure-v2" id="phone">确定</span></div>'''
        args={'recipient':{'name':'HR','company':'Company'},'accountLabel':'Candidate','inboundId':'in1'}
        output=self.run_dom([boss_chat.action_script('confirm-resume',args)],html=html,url='https://www.zhipin.com/web/geek/chat')
        self.assertEqual(output['calls'],[])

    def test_resume_specific_extra_confirmation_is_scoped(self):
        html='''<div class="nav-figure"><span class="label">Candidate</span></div>
        <div class="friend-content selected"><div class="name-box"><span class="name-text">HR</span><span>Company</span></div></div>
        <div class="user-info"><div class="base-info"><div><span class="name-text">HR</span></div><span>Company</span></div></div>
        <ul class="im-list"><li class="message-item item-friend" data-mid="in1">简历</li></ul>
        <div class="dialog-wrap">确定与对方交换简历吗？发送【简历】<button id="consent">确定</button></div>'''
        args={'recipient':{'name':'HR','company':'Company'},'accountLabel':'Candidate','inboundId':'in1','confirmationStage':'cross-border-resume'}
        output=self.run_dom([boss_chat.observation_script(),boss_chat.action_script('confirm-resume',args)],html=html,url='https://www.zhipin.com/web/geek/chat')
        self.assertEqual(output['values'][0]['chat']['resumeConfirmationStage'],'cross-border-resume')
        self.assertEqual(output['calls'],[{'kind':'click','id':'consent'}])
        args['confirmationStage']='resume'
        output=self.run_dom([boss_chat.action_script('confirm-resume',args)],html=html,url='https://www.zhipin.com/web/geek/chat')
        self.assertEqual(output['calls'],[])

    def test_detail_skeleton_is_not_full_jd(self):
        html='<div class="job-detail-container"><div class="skeleton-box">Footer</div></div>'
        output=self.run_dom([boss_chat.boss_page.observation_script()],html=html)
        self.assertTrue(output['values'][0]['detail']['loading'])
        self.assertEqual(output['values'][0]['detail']['text'],'')
