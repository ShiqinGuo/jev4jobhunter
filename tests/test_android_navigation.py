"""Mobile navigation contracts and incident regressions; no live platform."""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET
from android_domain import Layout, LocateStatus
from android_receipts import ReceiptFeed
from android_ui import NativeUI, display_matches
import store
pytestmark = [pytest.mark.android, pytest.mark.navigation]

def test_reordered_message_rows_are_reacquired_before_tapping(case):
    ui = NativeUI(case.device, case.feed, case.root / 'ui')
    tree = ET.fromstring('<hierarchy><node bounds="[0,0][1260,2800]"/></hierarchy>')
    stale = ET.fromstring('<node text="Target HR" bounds="[10,900][200,980]"/>')
    fresh = ET.fromstring('<node text="Target HR" bounds="[10,1300][200,1380]"/>')
    row = {'name': 'Target HR', 'company': case.job['company'], 'position': 'Python', 'node': stale}
    ui.message_home = Mock(return_value=tree)
    ui.message_rows = Mock(side_effect=[[row], [{**row, 'node': fresh}]])
    ui.conversation = Mock(return_value={'company': case.job['company'], 'messages': []})
    case.device.ui.return_value = tree
    ui.open_conversation(case.job)
    case.device.tap.assert_called_once_with(fresh)

def test_opened_neighbour_cannot_be_returned_as_the_intended_conversation(case):
    from android_domain import TargetUnavailable
    ui = NativeUI(case.device, case.feed, case.root / 'ui')
    tree = ET.fromstring('<hierarchy><node bounds="[0,0][1260,2800]"/></hierarchy>')
    node = ET.fromstring('<node text="Target HR"/>')
    row = {'name': 'Target HR', 'company': case.job['company'], 'position': 'Python', 'node': node}
    ui.message_home = Mock(return_value=tree)
    ui.message_rows = Mock(return_value=[row])
    ui.conversation = Mock(return_value={'company': 'Neighbour company', 'messages': []})
    case.device.ui.return_value = tree
    with patch('android_ui.time.sleep'):
        with pytest.raises(TargetUnavailable):
            ui.open_conversation(case.job)
    assert case.device.tap.call_count == 2
    assert ui.message_home.call_count == 2

def test_navigation_contract_rejects_wrong_company_before_binding_digest_to_target(case):
    from android_domain import TargetUnavailable
    case.navigator.open_conversation.return_value = {'company': 'Neighbour company', 'messages': []}
    with pytest.raises(TargetUnavailable, match='target-mismatch'):
        case.app.open_conversation('boss:1')
    assert not store.load_state(case.root)['actions']

def test_return_to_list_checks_the_result_of_the_last_bounded_back(case):
    navigator = NativeUI(case.device, case.feed, case.root / 'ui')
    navigator.cards = Mock(side_effect=[[], [], [], [{'title': 'Python'}]])
    navigator.return_to_list()
    assert case.device.back.call_count == 3

def test_truncated_native_labels_only_prepare_detail_reading(case):
    assert display_matches('杭州云核智科信息...', '杭州云核智科信息技术')
    assert not display_matches('杭州云核', '杭州云核智科信息技术')
    navigator = NativeUI(case.device, case.feed, case.root / 'ui')
    truncated = {'title': 'Python', 'company': 'Fixture com...', 'node': ET.fromstring('<node bounds="[0,0][100,100]"/>')}
    navigator.cards = Mock(return_value=[truncated])
    case.feed.wait_material.return_value = None
    result = navigator.locate_job(case.job, case.app.batch['jobs'], case.app.profile, case.app.marker)
    assert result.status == LocateStatus.DETAIL_MISSING
    assert case.device.tap.call_count == 1

def test_collapsed_real_chat_shape_uses_original_detail_and_same_bubble_delivery(case):
    feed = ReceiptFeed(case.root / 'responses')
    store.write_json(feed.directory / 'detail-1.json', {'type': 'detail', 'job': case.job})
    marker = case.app.marker('greet', 'boss:1', 'original')
    moment = datetime.fromtimestamp(marker.at, timezone(timedelta(hours=8)))
    contact = f'{moment.month}月{moment.day}日 {moment:%H:%M} 由你发起的沟通'
    package = 'com.hpbr.bosszhipin'
    tree = ET.fromstring(f'<hierarchy><node package="{package}" bounds="[0,0][1260,2800]">\n          <node resource-id="{package}:id/tv_company_name" text="Fixture company"/>\n          <node resource-id="{package}:id/tv_contact_time" text="{contact}"/>\n          <node><node resource-id="{package}:id/iv_avatar" bounds="[1120,900][1200,980]"/>\n            <node resource-id="{package}:id/tv_content_text" text="Authorized opener"/>\n            <node resource-id="{package}:id/mMsgTvStatus" text="送达"/></node>\n          <node><node resource-id="{package}:id/iv_avatar" bounds="[10,1500][90,1580]"/>\n            <node resource-id="{package}:id/tv_content_text" text="Other inbound"/>\n            <node resource-id="{package}:id/mMsgTvStatus" text="送达"/></node>\n        </node></hierarchy>')
    device = Mock()
    from android_device import Device
    device.bounds = Device.bounds
    device.ui.return_value = tree
    ui = NativeUI(device, feed, case.root)
    action = {'id': 'original', 'kind': 'greet', 'targetKey': 'boss:1', 'targetFacts': case.job, 'expectedGreeting': 'Authorized opener'}
    proof = ui.delivered_message(action, marker, Layout(1260, 2800, '14.170'))
    assert proof.outcome == 'succeeded'
    status = next((n for n in tree.iter('node') if n.get('resource-id', '').endswith('/mMsgTvStatus')))
    status.set('text', '')
    assert ui.delivered_message(action, marker, Layout(1260, 2800, '14.170')) is None
    status.set('text', '送达')
    detail = {**case.job, 'key': 'boss:2'}
    store.write_json(feed.directory / 'detail-1.json', {'job': detail})
    assert ui.delivered_message(action, marker, Layout(1260, 2800, '14.170')) is None
