from test_browsing_safety import BrowsingFixture
import store
from browser_actions import Engine
import json


class RetainedCandidateTests(BrowsingFixture):
    def test_foreign_login_does_not_create_boss_block(self):
        result = self.guard.observe({'url': 'https://docs.qq.com/doc/example',
                                     'body': '登录腾讯文档'})
        self.assertEqual(result['classification'], 'wrong-site')
        self.assertIsNone(self.guard.status()['accessBlock'])

    def test_blocked_recovery_only_reselects_existing_tab(self):
        self.guard.observe(self.page(body='访问受限'))
        original = store.load_state(self.root)['blocks']
        calls = []
        url = 'https://www.zhipin.com/web/geek/jobs?query=Python'
        def transport(action, args):
            calls.append(action)
            return {'outcome': 'returned', 'result': {'data': {
                'success': True, 'tabs': [{'url': url}], 'url': url}}}
        Engine(self.root, self.token, 'boss', 'fixture-session-1', transport).execute('recover-page')
        self.assertEqual(calls, ['list_tabs', 'find_tab'])
        self.assertEqual(store.load_state(self.root)['blocks'], original)

    def test_blocked_recovery_cannot_open_new_page(self):
        self.guard.observe(self.page(body='访问受限'))
        calls = []
        def transport(action, args):
            calls.append(action)
            return {'outcome': 'returned', 'result': {'data': {'success': True, 'tabs': []}}}
        with self.assertRaisesRegex(store.StoreError, 'platform-access-blocked'):
            Engine(self.root, self.token, 'boss', 'fixture-session-1', transport).execute('recover-page')
        self.assertEqual(calls, ['list_tabs'])

    def test_policy_change_discards_restore_pointer_and_preserves_outbox(self):
        original = self.batch()['batch']
        self.screen(decision='deferred')
        self.screen('boss:job-b', 'deferred')
        self.guard.restore_filters({})
        state = store.load_state(self.root)
        state['actions']['receipt-a'] = {'targetKey': 'boss:job-a', 'status': 'succeeded'}
        state['actions']['receipt-b'] = {'targetKey': 'boss:job-b', 'status': 'unknown'}
        state['browsing']['boss']['retainedBatches'] = [original]
        state['browsing']['boss']['backlog'] = ['boss:job-a']
        store.write_json(self.root / 'state.json', state)
        policy = store.load_policy(self.root)
        policy['objective'] = 'Changed screening policy'
        store.write_json(self.root / 'policy.json', policy)
        self.guard.start_query(self.query)
        self.guard.observe(self.page())
        result = self.guard.capture_list()
        for candidate in result['candidates'].values():
            self.assertEqual(candidate['decision'], 'skipped')
            self.assertEqual(candidate['evidence'], 'already-contacted-or-unresolved')
        current = store.load_state(self.root)
        self.assertEqual(current['actions'], state['actions'])
        self.assertEqual(current['browsing']['boss']['backlog'], [])
        self.assertEqual(current['browsing']['boss']['retainedBatches'], [])
        self.assertNotIn('restoringBatch', current['browsing']['boss'])
        self.assertTrue(current['browsing']['boss']['archives'])

    def test_start_query_recovers_already_stale_restore_pointer(self):
        original = self.batch()['batch']
        state = store.load_state(self.root)
        state['browsing']['boss'].update(batch=None, candidates={}, seen=[],
                                        phase='configuring', restoringBatch=original)
        store.write_json(self.root / 'state.json', state)
        self.guard.start_query(self.query)
        self.guard.observe(self.page())
        result = self.guard.capture_list()
        self.assertEqual(result['candidates']['boss:job-a']['decision'], 'unreviewed')
        self.assertNotIn('restoringBatch', self.guard.status()['flow'])

    def test_focus_emulation_is_released_before_page_switch(self):
        calls=[]
        def transport(action,args):
            calls.append((action,args))
            value=self.page(visibility='visible') if action=='evaluate' else {}
            return {'outcome':'returned','result':{'ok':True,'data':{
                'value':json.dumps(value),'success':True,'url':'https://www.zhipin.com/web/geek/chat'}}}
        engine=Engine(self.root,self.token,'boss','fixture-session-1',transport)
        self.assertTrue(engine.execute('focus-page')['focusEmulated'])
        self.assertTrue(self.guard.status()['flow']['focusEmulated'])
        engine.execute('open-page',{'page':'chat'})
        self.assertEqual(calls[-2],('cdp',{'method':'Emulation.setFocusEmulationEnabled','params':{'enabled':False}}))
        self.assertFalse(self.guard.status()['flow']['focusEmulated'])

    def test_open_jobs_preserves_observed_query_in_navigation(self):
        calls=[]
        url='https://www.zhipin.com/web/geek/jobs?query=Python&city=fixture'
        def transport(action,args):
            calls.append((action,args))
            return {'outcome':'returned','result':{'data':{'success':True,'tabs':[{'url':url}],'url':url}}}
        Engine(self.root,self.token,'boss','fixture-session-1',transport).execute('open-page',{'page':'jobs'})
        self.assertEqual(calls[-1],('navigate',{'url':url}))

    def test_collapsed_previously_observed_option_reopens_once(self):
        self.guard.start_query(self.query)
        menu={'id':'menu','kind':'filter-menu','filter':'experience'}
        option={'id':'option','kind':'filter-option','filter':'experience','label':'1-3年'}
        closed=self.page(controls=[menu])
        opened=self.page(controls=[menu,option])
        self.guard.observe(opened)
        values=iter([closed,closed,{'status':'hover-target','x':10,'y':10},opened,{'status':'clicked'},opened])
        calls=[]
        def transport(action,args):
            calls.append(action)
            value=next(values) if action=='evaluate' else {}
            return {'outcome':'returned','result':{'ok':True,'data':{'value':json.dumps(value)}}}
        result=Engine(self.root,self.token,'boss','fixture-session-1',transport).execute('control',{'id':'option'})
        self.assertEqual(result['step']['status'],'clicked')
        self.assertEqual(calls.count('cdp'),1)
        self.assertIsNone(self.guard.status()['flow']['pending'])

    def test_select_uses_exact_inventory_url_and_active_borrowed_only(self):
        calls=[]
        url='https://www.zhipin.com/job_detail/abc.html?securityId=fixture'
        def transport(action,args):
            calls.append((action,args))
            return {'outcome':'returned','result':{'data':{'success':True,
                'tabs':[{'url':url,'borrowed':True,'active':True}],'url':url}}}
        engine=Engine(self.root,self.token,'boss','fixture-session-1',transport)
        engine.execute('select-page',{'page':'detail','key':'boss:abc'})
        self.assertEqual(calls[1],('find_tab',{'url':url,'active':True}))

    def test_wrong_returned_page_rejected(self):
        def transport(action,args):
            return {'outcome':'returned','result':{'data':{'success':True,
                'tabs':[{'url':'https://www.zhipin.com/web/geek/chat'}],
                'url':'https://www.zhipin.com/web/geek/jobs'}}}
        engine=Engine(self.root,self.token,'boss','fixture-session-1',transport)
        with self.assertRaisesRegex(store.StoreError,'wrong-page'):
            engine.execute('select-page',{'page':'chat'})

    def test_same_list_restore_preserves_original_batch(self):
        original=self.batch()['batch']
        self.screen()
        self.guard.restore_filters({})
        result=self.guard.capture_list()
        self.assertEqual(result['batch'],original)
        self.assertEqual(self.guard.status()['flow']['candidates']['boss:job-a']['decision'],'shortlisted')

    def test_changed_list_retains_missing_work_and_rejoins_when_visible(self):
        original=self.batch()['batch']
        self.screen()
        self.guard.restore_filters({})
        self.guard.observe(self.page(('boss:job-c',)))
        result=self.guard.capture_list()
        self.assertEqual(set(result['retainedUnfinished']),set(original['keys']))
        self.assertEqual(self.guard.status()['flow']['retainedBatches'],[original])
        with self.assertRaisesRegex(store.StoreError,'no-longer-in-rendered-list'):
            self.guard.revisit_candidate({'key':'boss:job-a','evidence':'restored'})
        self.guard.observe(self.page(('boss:job-c','boss:job-a')))
        self.guard.revisit_candidate({'key':'boss:job-a','evidence':'visible again'})
        flow=self.guard.status()['flow']
        self.assertIn('boss:job-a',flow['batch']['keys'])
        self.assertNotIn('boss:job-a',flow['backlog'])
        self.assertEqual(flow['candidates']['boss:job-a']['decision'],'unreviewed')

    def test_deferred_requires_fresh_screen_and_detail(self):
        self.batch()
        self.screen(decision='deferred')
        self.guard.revisit_candidate({'key':'boss:job-a','evidence':'temporary issue resolved'})
        with self.assertRaisesRegex(store.StoreError,'list-screen-required'):
            self.guard.reserve('open-detail',{'key':'boss:job-a'})
        self.screen()
        self.guard.reserve('open-detail',{'key':'boss:job-a'})
