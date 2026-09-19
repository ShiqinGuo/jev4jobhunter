"""Business steps after model review; no autonomous semantic decisions or send retries."""
from __future__ import annotations

import store
import boss_chat
import boss_page
import condition_wait

OPERATIONS=('open-conversation-and-wait','open-detail-and-wait','reply-and-verify','wait-chat-receipt','ensure-page')


def expected_account(engine):
    state=store.load_state(engine.safety.root)
    context=store.active_account_context(state,'boss')
    return context.get('accountLabel') or next((p.get('accountLabel') for p in
        store.load_policy(engine.safety.root).get('platforms',[]) if p.get('id')=='boss'),None)


def wait_conversation(engine, who, timeout):
    return condition_wait.observe(engine,boss_chat.observation_script(),r'''
if(page.url!=='https://www.zhipin.com/web/geek/chat'||page.accountLabel!==args.account) return false;
const c=page.chat;
return c?.recipient?.name===args.recipient.name && c.recipient.company===args.recipient.company &&
  c.job && c.messages.length ? [c.recipient,c.job,c.messages.map(m=>[m.id,m.direction,m.text,m.body])] : false;
''',{'recipient':who,'account':expected_account(engine)},timeout)


def wait_receipt(engine, action, timeout):
    # Match only the prepared reply or explicit attachment receipt/request. No sends.
    result=condition_wait.observe(engine,boss_chat.observation_script(),r'''
const c=page.chat;
if(page.url!=='https://www.zhipin.com/web/geek/chat'||page.accountLabel!==args.accountLabel||
   c?.recipient?.name!==args.chatContext.recipient.name||c.recipient.company!==args.chatContext.recipient.company) return false;
const fresh=c.messages.filter(m=>m.id&&!args.chatContext.baselineIds.includes(m.id)&&!m.failed);
const receipts=args.kind==='reply' ? fresh.filter(m=>m.direction==='out'&&m.text===args.content&&m.delivered) :
  fresh.filter(m=>m.direction==='system'&&(/附件简历请求已发送|您的附件简历.*已发送给Boss/s).test(m.body));
return receipts.length ? receipts.map(m=>m.id) : (c.resumeWaiting || args.deliveryState==='awaiting-recipient-consent' ? 'waiting-consent' : false);
''',action,timeout)
    settled=boss_chat.reconcile(engine,action,observed=result['observation'])
    return {**settled,'observation':result['observation'],'observations':result['observations'],
            'polls':result['polls'],'nextAction':'none' if settled['status']=='succeeded' else 'reconcile-chat'}


def execute(engine,operation,data):
    timeout=data.get('waitMs',4000)
    if type(timeout) is not int or not 0 <= timeout <= 10000:
        raise store.StoreError('waitMs-must-be-integer-between-0-and-10000')
    if operation=='ensure-page':
        if set(data)-{'page','waitMs'} or data.get('page') not in ('jobs','chat','resume'):
            raise store.StoreError('known-boss-page-required')
        engine.safety.preflight()
        wanted='https://www.zhipin.com/web/geek/'+data['page']
        observation={'jobs':boss_page.observation_script,'chat':boss_chat.observation_script,
                     'resume':boss_chat.resume_observation_script}[data['page']]()
        current=engine.safety.observe(engine._call(observation,read_only=True))
        reused=current['page'].get('url')==wanted and current['page'].get('accountLabel')==expected_account(engine)
        if not reused:
            engine.safety.preflight()
            engine.execute('open-page',{'page':data['page']})
        wait=condition_wait.observe(engine,observation,
            'return page.url===args.url && page.accountLabel===args.account ? [page.url,page.accountLabel] : false;',
            {'url':wanted,'account':expected_account(engine)},timeout)
        return {'status':'ready' if wait['ready'] else 'pending','reused':reused,**wait,
                'nextAction':{'jobs':'restore-filters','chat':'inspect-chat','resume':'inspect-resume'}[data['page']]}
    if operation=='open-conversation-and-wait':
        if set(data)-{'recipient','waitMs'}:
            raise store.StoreError('recipient-and-wait-only')
        step=engine.execute('open-conversation',{'recipient':data['recipient']})
        if step.get('step',{}).get('status')!='clicked':
            return {**step,'status':'unsupported'}
        wait=wait_conversation(engine,data['recipient'],timeout)
        if wait['ready']:
            boss_chat.verify(engine,wait['observation'],data['recipient'])
        return {'status':'ready' if wait['ready'] else 'pending',**wait,'nextAction':'model-review-conversation' if wait['ready'] else 'inspect-chat'}
    if operation=='open-detail-and-wait':
        if set(data)-{'key','waitMs'}:
            raise store.StoreError('candidate-key-and-wait-only')
        step=engine.execute('open-detail',{'key':data['key']})
        if step.get('status')=='unsupported':
            return step
        wait=condition_wait.observe(engine,boss_page.observation_script(),r'''
return page.accountLabel===args.account && page.detail?.key===args.key && !page.detail.loading && page.detail.text ?
  [page.detail.key,page.detail.text] : false;
''',{'key':data['key'],'account':expected_account(engine)},timeout)
        if wait['ready']:
            engine.safety._verify(engine.safety.status()['flow'],wait['observation']['page'])
        return {'status':'ready' if wait['ready'] else 'pending',**wait,'nextAction':'model-review-detail' if wait['ready'] else 'inspect'}
    if operation=='wait-chat-receipt':
        if set(data)-{'actionId','waitMs'}:
            raise store.StoreError('action-id-and-wait-only')
        action=store.load_state(engine.safety.root)['actions'][data['actionId']]
        if not action.get('chatContext') or action.get('kind') not in ('reply','share_resume'):
            raise store.StoreError('chat-action-required')
        if action['status'] in ('succeeded','failed') or not action.get('chatAttempted'):
            return boss_chat.reconcile(engine,action)
        return wait_receipt(engine,action,timeout)
    if operation=='reply-and-verify':
        if set(data)-{'request','actionId','waitMs'} or ('request' in data)==('actionId' in data):
            raise store.StoreError('one-reviewed-request-or-action-id-required')
        if 'actionId' in data:
            action=store.load_state(engine.safety.root)['actions'][data['actionId']]
            if action.get('kind')!='reply' or not action.get('chatContext'):
                raise store.StoreError('prepared-reply-required')
            if action.get('chatAttempted') or action['status'] in ('succeeded','failed','unknown'):
                return execute(engine,'wait-chat-receipt',{'actionId':action['id'],'waitMs':timeout})
            observed=None
        else:
            if data['request'].get('kind')!='reply':
                raise store.StoreError('reviewed-reply-only')
            engine.safety.preflight()
            observed=boss_chat.inspect(engine)
            prepared=boss_chat.prepare(engine,{'request':data['request']},observed)
            action=store.load_state(engine.safety.root)['actions'][prepared['id']]
        try:
            sent=boss_chat.execute(engine,'send-chat',{'actionId':action['id']},observed=observed,defer_receipt=True)
        except (ValueError,OSError) as error:
            action=store.load_state(engine.safety.root)['actions'][action['id']]
            if not action.get('chatAttempted'):
                raise
            if engine.measurement:
                engine.measurement.recoveries.append({'kind':'passive-reconcile-after-submit-error','error':str(error)})
            return wait_receipt(engine,action,timeout)
        if sent['status']=='failed':
            return sent
        action=store.load_state(engine.safety.root)['actions'][action['id']]
        return wait_receipt(engine,action,timeout)
    raise store.StoreError('unsupported-business-workflow')
