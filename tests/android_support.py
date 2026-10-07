"""Isolated local state and fake phone ports shared by mobile feature tests."""
from pathlib import Path
from unittest.mock import MagicMock, Mock
import xml.etree.ElementTree as ET
from android_application import MobileApplication
from android_domain import Binding, Layout, LocatedJob, LocateStatus, Observation, Runtime, digest, jd_digest
from android_repository import MobileRepository
import store

class AndroidCase:

    def __init__(self, root: Path):
        self.root = root
        store.initialize(self.root)
        self.token = store.run_lock(self.root, 'acquire')['token']
        policy = store.load_policy(self.root)
        policy['platforms'] = [{'id': 'boss', 'accountLabel': 'Fixture'}]
        policy['authorization'].update(greet='allow', reply='allow', evidence='Explicit test authorization')
        policy['dailyLimits']['greet'] = 20
        store.write_json(self.root / 'policy.json', policy)
        self.repo = MobileRepository(self.root, self.token)
        self.repo.create(Binding('phone', 'Fixture', 'Fixture'), {'source': 'search', 'query': 'python', 'city': '杭州', 'count': 2})
        self.device = Mock()
        self.device.observe_runtime.return_value = Runtime('phone', Layout(1260, 2800, '14.170'))
        self.navigator = Mock()
        self.feed = Mock()
        self.app = MobileApplication(self.repo, self.device, self.navigator, self.feed)
        self.app.runtime = self.device.observe_runtime()
        self.app.profile = {'x': 600, 'startY': 2400, 'endY': 700, 'durationMs': 650, 'maxSwipes': 3}
        self.job = {'key': 'boss:1', 'title': 'Python', 'company': 'Fixture company', 'text': 'Full backend JD', 'city': '杭州', 'salary': '10-20K', 'experience': '1-3年', 'degree': '本科', 'publisherType': 'unknown', 'complete': True, 'detailEvidence': 'detail-1'}
        self.app.batch['jobs'] = {'boss:1': self.job}
        self.app.batch['selection'] = ['boss:1']
        self.app.batch['decisions'] = {'boss:1': {'key': 'boss:1', 'apply': True, 'context': self.app.decision_context(), 'jdDigest': jd_digest(self.job)}}
        self.app.save()
        self.navigator.locate_job.return_value = LocatedJob(LocateStatus.FOUND, self.job)
        self.device.ui.return_value = ET.fromstring('<hierarchy><node text="立即沟通"/></hierarchy>')
        self.feed.observe.return_value = Observation()
        self.navigator.delivered_message.return_value = None

    def prepare(self):
        request = {'kind': 'greet', 'platform': 'boss', 'targetKey': 'boss:1', 'accountLabel': 'Fixture', 'accountContextId': 'Fixture', 'contentMode': 'platform-default', 'targetFacts': self.job, 'authorizationEvidence': 'Test authority', 'context': 'Android test'}
        return store.begin(self.root, self.token, request, mobile=True)

    def start(self, action):
        marker = self.app.marker('greet', 'boss:1', action['id'])
        return store.start_mobile_action(self.root, self.token, action['id'], marker.to_dict())

    def reply_request(self):
        snapshot = {'company': self.job['company'], 'messages': []}
        self.navigator.conversation.return_value = snapshot
        native = MagicMock()
        native.__enter__.return_value = native
        native.snapshot = snapshot
        self.navigator.prepare_text_session.return_value = native
        request = {'kind': 'reply', 'platform': 'boss', 'targetKey': 'boss:1', 'targetFacts': self.job, 'inboundId': 'explicit-trial-1', 'content': '您好，有 Python 后端和 Agent 开发经验。', 'conversationDigest': digest(snapshot), 'authorizationEvidence': 'Explicit trial', 'context': 'Full JD and policy reviewed for test', 'oneShotAuthorization': {'kind': 'reply', 'platform': 'boss', 'targetKey': 'boss:1', 'inboundId': 'explicit-trial-1', 'evidence': 'Explicit trial'}}
        return (request, native)
