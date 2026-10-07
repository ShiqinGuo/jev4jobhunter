"""ADB binding, live layout observation and locked/blocked UI boundaries."""
import pytest
from types import SimpleNamespace
import subprocess
from unittest.mock import Mock, patch
from android_device import Device
from android_domain import DeviceUnavailable, IdentityChanged, NavigationError, PlatformBlocked
pytestmark = [pytest.mark.android, pytest.mark.device]

@pytest.fixture
def device_case():
    case = SimpleNamespace()
    case.config = {'adb': 'fixture-adb', 'serverPort': 5039, 'serial': 'wireless:5555', 'manufacturer': 'vivo', 'model': 'Fixture model', 'python': 'fixture-python', 'caDir': 'fixture-ca'}
    case.device = Device(case.config)
    case.size, case.version = ('Physical size: 1260x2800', '14.170')

    def adb(*args):
        if args[:2] == ('shell', 'getprop'):
            return {'ro.product.manufacturer': 'vivo', 'ro.product.model': 'Fixture model', 'ro.serialno': 'physical-phone'}[args[2]]
        if args == ('shell', 'wm', 'size'):
            return case.size
        if args == ('shell', 'dumpsys', 'package', 'com.hpbr.bosszhipin'):
            return 'versionName=' + case.version
        raise AssertionError(args)
    case.device.adb = Mock(side_effect=adb)
    return case

def test_fixed_adb_server_and_transport_do_not_become_physical_identity(device_case):
    case = device_case
    assert case.device.prefix == ['fixture-adb', '-P', '5039', '-s', 'wireless:5555']
    assert case.device.observe_runtime().serial == 'physical-phone'

def test_runtime_is_not_cached_and_override_size_is_current_layout(device_case):
    case = device_case
    first = case.device.observe_runtime()
    case.size = 'Physical size: 1260x2800\nOverride size: 1080x2400'
    case.version = '14.171'
    second = case.device.observe_runtime()
    assert first.serial == second.serial
    assert second.layout.key == '1080x2400@14.171'

def test_transport_timeout_has_a_named_error_and_keeps_cause(device_case):
    case = device_case
    device = Device(case.config)
    failure = subprocess.TimeoutExpired('fixture-adb', 20)
    with patch('android_device.subprocess.run', side_effect=failure):
        with pytest.raises(DeviceUnavailable) as raised:
            device.adb('devices')
    assert raised.value.__cause__ is failure

def test_account_change_retains_original_batch_and_pauses_before_click(case):
    import store
    original = case.start(case.prepare())
    policy = store.load_policy(case.root)
    policy['platforms'][0]['accountLabel'] = 'Another account'
    store.write_json(case.root / 'policy.json', policy)
    with pytest.raises(IdentityChanged):
        case.app.prepare_search(case.app.batch['plan'])
    assert case.repo.load()['jobs']['boss:1']['text'] == case.job['text']
    assert store.load_state(case.root)['actions'][original['id']]['submissionStage'] == 'in_flight'
    case.device.tap.assert_not_called()

@pytest.mark.parametrize('package,label,error,reason', [('com.android.systemui', 'Unlock', NavigationError, 'boss-not-foreground'), ('com.hpbr.bosszhipin', '请完成安全验证', PlatformBlocked, '请完成安全验证')], ids=['locked-phone', 'security-challenge'])
def test_unavailable_phone_screen_is_classified_before_any_ui_action(device_case, package, label, error, reason):
    device = device_case.device
    device.adb.side_effect = None
    device.adb.return_value = f'<?xml version="1.0"?><hierarchy><node package="{package}" text="{label}"/></hierarchy>'
    with pytest.raises(error, match=reason):
        device.ui()
    assert device.adb.call_count == 1
