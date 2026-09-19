"""Visible BOSS chat adapter. Search filters do not govern existing conversations.

Each send is a prepared outbox action, bound to the current recipient, inbound
message and baseline message IDs. Unknown outcomes can only be reconciled.
"""
from __future__ import annotations

import json
import hashlib
import store
import boss_page

OPERATIONS = ('inspect-chat', 'open-conversation', 'chat-job-detail',
              'prepare-chat-send', 'send-chat', 'reconcile-chat', 'confirm-resume', 'dismiss-resume', 'inspect-resume')

DOM = boss_page._DOM + r'''
const identity = root => {
  if (!root) return null;
  const name = text(root.querySelector('.name-text'));
  const spans = Array.from(root.children).filter(e => e.tagName === 'SPAN');
  const company = text(spans.find(e => !e.classList.contains('name-text')));
  return name && company ? {name, company} : null;
};
const rows = () => shown('.friend-content').map(el => ({el,
  identity: identity(el.querySelector('.name-box')), preview:text(el.querySelector('.last-msg-text'))}));
const recipient = () => {
  const selected = unique(rows().filter(r => r.el.classList.contains('selected')));
  const header = identity(unique(shown('.user-info .base-info')));
  return selected && header && JSON.stringify(selected.identity) === JSON.stringify(header) ? header : null;
};
const messages = () => shown('.im-list .message-item').map(el => ({
  id:el.getAttribute('data-mid'), direction:el.classList.contains('item-myself') ? 'out' :
    el.classList.contains('item-friend') ? 'in' : 'system',
  text:text(el.querySelector('.text-content')), body:text(el),
  delivered:/送达|已读/.test(text(el.querySelector('.message-status'))),
  failed:!!el.querySelector('.icon-send-fail, .send-fail, .send-error')
}));
const same = (a,b) => a && b && a.name === b.name && a.company === b.company;
const resumeRequests = () => shown('.im-list .message-item').filter(el =>
  !el.classList.contains('item-myself') && /请求.*(?:附件简历|发送简历)|我想要一份您的附件简历，您是否同意/.test(text(el))).map(el => ({el,
    id:el.getAttribute('data-mid'), buttons:shown('button, a, .btn, span',el).filter(b=>enabled(b)&&text(b)==='同意'&&!Array.from(b.children).some(c=>text(c)==='同意'))}));
const resumePanels = () => shown('.panel-resume, .dialog-wrap, [role="dialog"]').map(el=>({el,
  stage:text(el).includes('确定向 Boss 发送简历吗？') ? 'resume' :
    (text(el).includes('确定与对方交换简历吗？')&&text(el).includes('发送【简历】') ? 'cross-border-resume' : null)
})).filter(r=>r.stage);
'''


def observation_script():
    return '(() => {' + DOM + r'''
return JSON.stringify({url:currentURL.origin+currentURL.pathname, fullUrl:currentURL.href, accountLabel:accountLabel(), visibility:document.visibilityState,
 body:text(document.body), controls:shown('#chat-input').map(el=>({id:'chat-input'})),
 chat:{recipient:recipient(), rows:rows().map(r=>({recipient:r.identity,preview:r.preview})),
 messages:messages(), job:text(unique(shown('.position-content'))),
 jobControls:shown('.position-content a, .position-content button, .position-content span').map(el=>({tag:el.tagName,classes:el.className,text:text(el),href:el.getAttribute('href')})),
 editor:text(unique(shown('#chat-input'))),
 resumeRequests:resumeRequests().filter(r=>r.id&&r.buttons.length===1).map(r=>({id:r.id,body:text(r.el)})),
 resumeConfirmation:resumePanels().length===1,
 resumeConfirmationStage:unique(resumePanels())?.stage || null,
 resumeWaiting:shown('.chat-controls .toolbar-btn[d-c="62009"]').some(el=>el.getAttribute('aria-label')==='正在请求中，等待对方回复'),
 dialogs:shown('.dialog-wrap, [role="dialog"], .panel-resume').map(el=>text(el))}});
})()'''


def action_script(operation, args):
    if operation not in ('open-conversation', 'chat-job-detail', 'reply', 'share_resume', 'confirm-resume', 'dismiss-resume'):
        raise store.StoreError('unsupported-chat-step')
    return '(() => {' + DOM + '\nconst args=' + json.dumps(args, ensure_ascii=False) + ';' + r'''
const fail = reason => JSON.stringify({status:'unsupported',reason});
if (currentURL.origin !== 'https://www.zhipin.com' || currentURL.pathname !== '/web/geek/chat') return fail('not-chat-page');
''' + (r'''
const matches=rows().filter(r=>same(r.identity,args.recipient));
if(matches.length!==1) return fail('ambiguous-or-missing-conversation');
matches[0].el.click();return JSON.stringify({status:'clicked'});
''' if operation == 'open-conversation' else r'''
if(!same(recipient(),args.recipient) || accountLabel()!==args.accountLabel) return fail('recipient-or-account-changed');
''' + (r'''
const el=unique(shown('.position-content span, .position-content a, .position-content button').filter(el=>text(el)==='查看职位'));
if(!el) return fail('job-control-missing');
const rect=el.getBoundingClientRect();
const x=rect.left+rect.width/2,y=rect.top+rect.height/2;
if(x<0||y<0||x>=innerWidth||y>=innerHeight) return fail('job-control-outside-viewport');
return JSON.stringify({status:'click-target',x,y});
''' if operation == 'chat-job-detail' else (r'''
const panel=unique(shown('.panel-resume').filter(el=>text(el).includes('确定向 Boss 发送简历吗？')));
const cancel=panel&&unique(shown('button, a, span',panel).filter(el=>enabled(el)&&text(el)==='取消'&&!Array.from(el.children).some(c=>text(c)==='取消')));
if(!cancel) return fail('resume-cancel-missing');
cancel.click();return JSON.stringify({status:'clicked'});
''' if operation == 'dismiss-resume' else r'''
const incoming=messages().filter(m=>m.direction==='in');
const request=unique(resumeRequests().filter(r=>r.id===args.inboundId&&r.buttons.length===1));
if(args.resumeMode==='accept-request') {
  if(!request || (incoming.length && incoming[incoming.length-1].id!==args.chatContext.latestInboundId)) return fail('resume-request-or-inbound-changed');
} else if(!incoming.length || incoming[incoming.length-1].id!==args.inboundId) return fail('inbound-changed');
''' + (r'''
const editor=unique(shown('#chat-input'));
const button=unique(shown('.chat-editor .btn-send').filter(enabled));
if(!editor || text(editor)!==args.content || !button) return fail('editor-or-send-control-mismatch');
button.click();return JSON.stringify({status:'clicked'});
''' if operation == 'reply' else (r'''
const panel=unique(resumePanels().filter(r=>r.stage===args.confirmationStage));
const button=panel && unique(shown('button, a, span',panel.el).filter(el=>enabled(el)&&text(el)==='确定'&&!Array.from(el.children).some(c=>text(c)==='确定')));
if(!button) return fail('resume-confirmation-missing');
button.click();return JSON.stringify({status:'clicked'});
''' if operation == 'confirm-resume' else r'''
const button=args.resumeMode==='accept-request' ? request.buttons[0] : unique(shown('.chat-controls .toolbar-btn[d-c="62009"]').filter(el=>enabled(el)&&!el.classList.contains('unable')&&text(el)==='发简历'));
if(!button) return fail('resume-control-missing');
button.click();return JSON.stringify({status:'clicked'});
'''))))) + '})()'


def inspect(engine):
    engine.safety._state()
    return engine.safety.observe(engine._call(observation_script(), read_only=True))


def verify(engine, observed, recipient=None):
    page = observed['page']
    policy = store.load_policy(engine.safety.root)
    expected = next((p.get('accountLabel') for p in policy.get('platforms', []) if p.get('id') == 'boss'), None)
    context = store.active_account_context(store.load_state(engine.safety.root), 'boss')
    expected = context.get('accountLabel') if context else expected
    if observed['classification'] != 'normal' or page.get('url') != 'https://www.zhipin.com/web/geek/chat':
        raise store.StoreError('normal-chat-page-required')
    if not expected or page.get('accountLabel') != expected:
        raise store.StoreError('chat-account-mismatch')
    if recipient is not None and page['chat'].get('recipient') != recipient:
        raise store.StoreError('chat-recipient-changed')
    return page


def prepare(engine, data, observed):
    request = data['request']
    page = verify(engine, observed)
    chat = page['chat']
    who = chat.get('recipient')
    if not who or request.get('kind') not in ('reply', 'share_resume'):
        raise store.StoreError('selected-conversation-and-supported-kind-required')
    incoming = [m for m in chat['messages'] if m['direction'] == 'in']
    mode = request.get('resumeMode', 'share')
    if mode not in ('share', 'accept-request') or (request['kind'] != 'share_resume' and mode != 'share'):
        raise store.StoreError('unsupported-resume-mode')
    matching_request = [r for r in chat.get('resumeRequests', []) if r['id'] == request.get('inboundId')]
    if mode == 'accept-request' and len(matching_request) != 1:
        raise store.StoreError('visible-recruiter-resume-request-required')
    if mode != 'accept-request' and (not incoming or not incoming[-1].get('id') or incoming[-1]['id'] != request.get('inboundId')):
        raise store.StoreError('latest-inbound-required')
    if request.get('accountLabel') != page['accountLabel'] or request.get('platform') != 'boss':
        raise store.StoreError('chat-account-mismatch')
    state = store.load_state(engine.safety.root)
    check_handover(state, who, request)
    # A different local target alias must never permit replay of the same inbound.
    for old in state['actions'].values():
        if (request['kind']=='share_resume' and old.get('kind')=='share_resume' and
                old.get('platform')=='boss' and old.get('status') in ('pending','unknown') and
                old.get('chatContext',{}).get('recipient')==who):
            raise store.StoreError('recipient-has-unresolved-resume-share')
        if (old.get('platform') == 'boss' and old.get('inboundId') == request['inboundId']
                and old.get('kind') == request['kind'] and old.get('status') in store.HELD):
            raise store.StoreError('duplicate-or-unresolved-inbound')
    if request.get('targetFacts', {}).get('company') != who['company']:
        raise store.StoreError('chat-company-mismatch')
    if request['kind'] == 'share_resume' and not request.get('resumeFilename'):
        raise store.StoreError('authorized-platform-resume-filename-required')
    if request['kind'] == 'share_resume':
        copy = state.get('platformResume', {}).get('boss', {})
        policy = store.load_policy(engine.safety.root)
        if (copy.get('accountLabel') != page['accountLabel'] or copy.get('filenames') != [request['resumeFilename']]
                or copy.get('resumeSha256') != policy.get('resume', {}).get('sha256')):
            raise store.StoreError('inspect-authorized-single-platform-resume-first')
        from pathlib import PureWindowsPath
        if PureWindowsPath(policy.get('resume',{}).get('path','')).name != request['resumeFilename']:
            raise store.StoreError('platform-resume-filename-outside-authorized-version')
    identity = hashlib.sha256(json.dumps(who, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
    key = request.get('targetKey')
    thread = state['threads'].get(key, {})
    if key != 'boss:chat:' + identity and not (thread.get('company') == who['company'] and thread.get('hrName') == who['name']):
        raise store.StoreError('target-key-not-bound-to-recipient')
    request = {**request, 'accountContextId': store.active_account_context(state,'boss').get('id'),
               'chatContext': {'recipient':who, 'job':chat['job'], 'session':engine.safety.session,
                 'baselineIds':[m['id'] for m in chat['messages']], 'resumeWaiting':chat.get('resumeWaiting',False),
                 'latestInboundId':incoming[-1]['id'] if incoming else None,
                 'resumeConfirmation':chat.get('resumeConfirmation',False),
                 'platformResume':state.get('platformResume',{}).get('boss') if request['kind']=='share_resume' else None,
                 'observation':observed['evidence']}}
    if request['kind']=='share_resume' and chat.get('resumeWaiting'):
        raise store.StoreError('resume-already-awaiting-recipient')
    if request['kind']=='share_resume' and chat.get('resumeConfirmation'):
        raise store.StoreError('unowned-resume-confirmation-present')
    return store.begin(engine.safety.root, engine.safety.token, request)


def check_handover(state, who, action):
    for thread in state['threads'].values():
        if thread.get('company') == who['company'] and thread.get('hrName') == who['name'] and (
                thread.get('needsReview') or (thread.get('humanTakenOver') and not action.get('oneShotHandover'))):
            raise store.StoreError('thread-paused')


def check_current(engine, action, page):
    state = store.load_state(engine.safety.root)
    check_handover(state, action['chatContext']['recipient'], action)
    incoming = [m for m in page['chat']['messages'] if m['direction'] == 'in']
    latest = incoming[-1].get('id') if incoming else None
    expected = action['chatContext'].get('latestInboundId', action['inboundId'])
    if latest != expected:
        raise store.StoreError('latest-inbound-changed')
    if any(m['direction'] == 'out' and m.get('id') not in action['chatContext']['baselineIds']
           for m in page['chat']['messages']):
        raise store.StoreError('new-outbound-needs-review')
    if action['kind'] == 'share_resume':
        if (action.get('deliveryState')=='awaiting-recipient-consent' or
                any(m.get('id') not in action['chatContext']['baselineIds'] and m['direction']=='system'
                    and '附件简历请求已发送' in m.get('body','') for m in page['chat']['messages'])):
            raise store.StoreError('resume-already-awaiting-recipient:reconcile-only')
        copy = state.get('platformResume', {}).get('boss', {})
        if (copy != action['chatContext'].get('platformResume') or
                copy.get('resumeSha256') != store.load_policy(engine.safety.root).get('resume', {}).get('sha256')):
            raise store.StoreError('platform-resume-version-not-verified')


def reconcile(engine, action, observed=None):
    if action['status'] in ('succeeded', 'failed'):
        return {'id':action['id'], 'status':action['status'], 'unchanged':True}
    if not action.get('chatAttempted'):
        return {'id':action['id'], 'status':action['status'], 'nextAction':'send-chat', 'submissionAttempted':False}
    observed = observed or inspect(engine)
    page = verify(engine, observed, action['chatContext']['recipient'])
    store.check_account_context(store.load_state(engine.safety.root),action)
    if page['accountLabel'] != action.get('accountLabel'):
        raise store.StoreError('chat-account-mismatch')
    fresh = [m for m in page['chat']['messages'] if m.get('id') and m['id'] not in action['chatContext']['baselineIds'] and not m['failed']]
    if action['kind'] == 'reply':
        matches = [m for m in fresh if m['direction']=='out' and m['text']==action['content'] and m.get('delivered')]
    else:
        import re
        def attachment_receipt(message):
            label=re.search(r'您的附件简历\s+(.+?)\s+已发送给Boss',message['body'])
            if not label or message['direction']!='system':
                return False
            name=label[1].strip()
            filename=action['resumeFilename']
            # BOSS truncates this system receipt. Bind a truncated label only to
            # the single platform attachment verified before this action.
            copy=action['chatContext'].get('platformResume') or {}
            return name==filename or (copy.get('filenames')==[filename] and
                name.endswith(('...','…')) and len(name.rstrip('.…'))>=4 and filename.startswith(name.rstrip('.…')))
        matches = [m for m in fresh if attachment_receipt(m)]
        if not matches and (action.get('deliveryState') == 'awaiting-recipient-consent' or
                any(m['direction']=='system' and '附件简历请求已发送' in m['body'] for m in fresh) or
                (page['chat'].get('resumeWaiting') and not action['chatContext'].get('resumeWaiting'))):
            with store.transaction(engine.safety.root):
                state,_=engine.safety._state()
                saved=state['actions'][action['id']]
                saved.update(status='pending',deliveryState='awaiting-recipient-consent',evidence=observed['evidence'])
                saved['events'].append({'at':store.stamp(),'status':'pending','deliveryState':'awaiting-recipient-consent','evidence':observed['evidence']})
                engine.safety._save(state)
            return {'id':action['id'],'status':'pending','deliveryState':'awaiting-recipient-consent','attachmentDelivered':False,'evidence':observed['evidence']}
    status = 'succeeded' if len(matches)==1 else 'unknown'
    result = store.resolve(engine.safety.root, engine.safety.token, action['id'], status,
                         observed['evidence'] + ': ' + (str(matches[0]['id']) if matches else 'receipt-unconfirmed; do-not-resend'))
    if action['kind'] == 'share_resume' and status == 'succeeded':
        with store.transaction(engine.safety.root):
            state, _ = engine.safety._state()
            state['actions'][action['id']]['deliveryState'] = 'attachment-delivered'
            engine.safety._save(state)
        result.update(deliveryState='attachment-delivered', attachmentDelivered=True)
    return result


def resume_observation_script():
    return '(() => {' + boss_page._DOM + r'''
return JSON.stringify({url:currentURL.origin+currentURL.pathname, fullUrl:currentURL.href, accountLabel:accountLabel(),
body:text(document.body), controls:[], filenames:shown('.basis a[title]').map(el=>el.getAttribute('title')).filter(s=>/\.(pdf|docx?)$/i.test(s))});})()'''


def execute(engine, operation, data, *, observed=None, defer_receipt=False):
    if operation == 'inspect-resume':
        engine.safety._state()
        observed=engine.safety.observe(engine._call(resume_observation_script(),read_only=True))
        page=observed['page']
        policy=store.load_policy(engine.safety.root)
        expected=next((p.get('accountLabel') for p in policy.get('platforms',[]) if p.get('id')=='boss'),None)
        if page['url']!='https://www.zhipin.com/web/geek/resume' or page['accountLabel']!=expected:
            raise store.StoreError('resume-page-account-mismatch')
        with store.transaction(engine.safety.root):
            state,_=engine.safety._state()
            state.setdefault('platformResume',{})['boss']={
                'accountLabel':page['accountLabel'],'filenames':page['filenames'],
                'resumeSha256':policy.get('resume',{}).get('sha256'),'evidence':observed['evidence'],
                'verification':'visible-filename-only; platform-bytes-not-hashed'}
            engine.safety._save(state)
        return observed
    if operation == 'inspect-chat':
        return inspect(engine)
    if operation == 'reconcile-chat':
        action = store.load_state(engine.safety.root)['actions'][data['actionId']]
        if action.get('platform') != 'boss' or not action.get('chatContext'):
            raise store.StoreError('chat-action-required')
        return reconcile(engine, action)
    engine.safety.preflight()
    if operation == 'chat-job-detail':
        engine.execute('focus-page')
    observed = observed or inspect(engine)
    if operation == 'open-conversation' and observed['page'].get('visibility') == 'hidden':
        engine.execute('focus-page')
        observed = inspect(engine)
    page = verify(engine, observed)
    engine.safety.preflight()
    if operation == 'prepare-chat-send':
        return prepare(engine, data, observed)
    if operation in ('open-conversation', 'chat-job-detail', 'dismiss-resume'):
        args = {**data,'accountLabel':page['accountLabel']}
        result=engine._call(action_script(operation,args))
        if result.get('status') == 'click-target':
            for event in ('mousePressed','mouseReleased'):
                response=engine.transport('cdp',{'method':'Input.dispatchMouseEvent','params':{
                    'type':event,'x':result['x'],'y':result['y'],'button':'left','clickCount':1}})
                if response.get('outcome') != 'returned':
                    raise store.StoreError('job-detail-click-unconfirmed:inspect-current-page')
            result['status']='clicked'
        return {'step':result, 'nextAction':'inspect-chat'}
    action = store.load_state(engine.safety.root)['actions'][data['actionId']]
    if action.get('kind') not in ('reply','share_resume') or not action.get('chatContext'):
        raise store.StoreError('prepared-chat-action-required')
    verify(engine, observed, action['chatContext']['recipient'])
    if action['chatContext']['session']!=engine.safety.session or action['chatContext']['job']!=page['chat']['job']:
        raise store.StoreError('chat-session-or-job-changed')
    check_current(engine, action, page)
    if operation == 'confirm-resume':
        stage=page['chat'].get('resumeConfirmationStage')
        if (action['kind']!='share_resume' or action.get('resumeConfirmAttempted') or
                stage in action.get('resumeConfirmationAttempts',{}) or
                not action.get('chatAttempted') or action.get('resumeStage') not in ('confirmation','toolbar-attempted') or
                action['chatContext'].get('resumeConfirmation')):
            raise store.StoreError('resume-confirmation-already-attempted-or-wrong-kind')
        if not page['chat'].get('resumeConfirmation'):
            raise store.StoreError('visible-resume-confirmation-required')
        saved_copy=store.load_state(engine.safety.root).get('platformResume',{}).get('boss',{})
        if saved_copy.get('filenames')!=[action['resumeFilename']] or saved_copy.get('accountLabel')!=page['accountLabel']:
            raise store.StoreError('platform-resume-version-not-verified')
        # The toolbar opened a confirmation, not a submission. Recover only this
        # proven pre-submit stage; never reset an attempted confirmation.
        if action['status']=='unknown' and action.get('chatAttempted'):
            with store.transaction(engine.safety.root):
                state,_=engine.safety._state()
                state['actions'][action['id']].update(status='pending',resumeStage='confirmation')
                state['actions'][action['id']]['events'].append({'at':store.stamp(),'status':'pending',
                    'evidence':observed['evidence']+': unsubmitted native confirmation visible; final button never attempted'})
                engine.safety._save(state)
        store.check_action(engine.safety.root,engine.safety.token,action['id'])
        with store.transaction(engine.safety.root):
            state,_=engine.safety._state()
            saved=state['actions'][action['id']]
            saved.setdefault('resumeConfirmationAttempts',{})[stage]=store.stamp()
            if stage == 'resume':
                saved['resumeConfirmAttempted']=store.stamp()
            engine.safety._save(state)
        try:
            result=engine._call(action_script('confirm-resume',{**action,'recipient':action['chatContext']['recipient'],'confirmationStage':stage}))
            if result.get('status')=='unsupported':
                return store.resolve(engine.safety.root,engine.safety.token,action['id'],'failed','No click: '+result['reason'])
            return reconcile(engine,action)
        except (ValueError,OSError):
            store.resolve(engine.safety.root,engine.safety.token,action['id'],'unknown','Resume confirmation outcome unknown; do not resend')
            raise
    store.check_action(engine.safety.root, engine.safety.token, action['id'])
    if action['chatContext']['session'] != engine.safety.session or action.get('chatAttempted'):
        raise store.StoreError('reconcile-attempted-chat-action-do-not-resend')
    if action['kind'] == 'reply':
        if page['chat']['editor'] and page['chat']['editor'] != action['content']:
            raise store.StoreError('editor-has-different-content')
        result = engine.transport('fill', {'selector':'#chat-input', 'value':action['content']})
        if (result.get('outcome') != 'returned' or result.get('result',{}).get('ok') is False or
                result.get('result',{}).get('data',{}).get('success') is False):
            raise store.StoreError('editor-fill-unconfirmed-no-submit')
        observed = inspect(engine)
        page = verify(engine, observed, action['chatContext']['recipient'])
        check_current(engine, action, page)
        engine.safety.preflight()
        store.check_action(engine.safety.root, engine.safety.token, action['id'])
    with store.transaction(engine.safety.root):
        state = store.load_state(engine.safety.root)
        store.require_token(state, engine.safety.token)
        state['actions'][action['id']]['chatAttempted'] = store.stamp()
        if action['kind'] == 'share_resume':
            state['actions'][action['id']]['resumeStage'] = 'toolbar-attempted'
        store.write_json(engine.safety.root/'state.json', state)
    args = {**action,'recipient':action['chatContext']['recipient']}
    action = store.load_state(engine.safety.root)['actions'][action['id']]
    try:
        result = engine._call(action_script(action['kind'],args))
        if result.get('status') == 'unsupported':
            return store.resolve(engine.safety.root, engine.safety.token, action['id'], 'failed', 'No click: '+result['reason'])
        if defer_receipt and action['kind']=='reply':
            return {'id':action['id'],'status':'pending','nextAction':'wait-chat-receipt'}
        if action['kind']=='share_resume':
            after=inspect(engine)
            verify(engine,after,action['chatContext']['recipient'])
            if after['page']['chat'].get('resumeConfirmation'):
                with store.transaction(engine.safety.root):
                    state, _ = engine.safety._state()
                    state['actions'][action['id']]['resumeStage'] = 'confirmation'
                    engine.safety._save(state)
                return {'id':action['id'],'status':'pending','nextAction':'confirm-resume','evidence':after['evidence']}
        return reconcile(engine, action)
    except (ValueError,OSError):
        store.resolve(engine.safety.root, engine.safety.token, action['id'], 'unknown', 'Chat submit observation unavailable; do not resend')
        raise
