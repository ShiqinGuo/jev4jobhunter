"""The real Java send selector and the native runner permission/readback contract."""
import pytest
import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest.mock import Mock, patch
from android_domain import NavigationError
from android_text import NativeTextSession
pytestmark = [pytest.mark.android, pytest.mark.native]
SUCCESS = b'INSTRUMENTATION_STATUS: class=jobhunter.NativeText\nINSTRUMENTATION_STATUS: test=testSetText\nINSTRUMENTATION_STATUS_CODE: 0\nOK (1 test)\nINSTRUMENTATION_STATUS_CODE: -1\n'

def native_fixture(case, ready=None, stopped=False):
    helper = case.root / 'native-text.jar'
    helper.write_bytes(b'Owned test jar; no execution')
    case.device.config = {'nativeTextJar': str(helper)}
    case.device.prefix = ['fixture-adb', '-P', '5039', '-s', 'phone']
    action_id = 'a' * 32
    ready = ready if ready is not None else json.dumps({'actionId': action_id, 'company': case.job['company'], 'messages': []})
    case.controls = []

    def adb(*args):
        if args[0] == 'push' and 'job-hunter-control-' in str(args[-1]):
            case.controls.append(Path(args[1]).read_text(encoding='utf-8'))
        return ready if len(args) == 2 and str(args[1]).startswith('if [ -f ') else ''
    case.device.adb.side_effect = adb
    process = Mock()
    process.poll.return_value = 0 if stopped else None

    def complete(**kwargs):
        process.poll.return_value = 0
        return (SUCCESS, b'')
    process.communicate.side_effect = complete
    return (NativeTextSession(case.device, 'com.hpbr.bosszhipin:id/editText_with_scrollbar', '有 Agent 开发经验。', action_id), process)

def test_base_runner_dependency_is_explicit_and_permission_can_only_be_issued_once(case):
    native, process = native_fixture(case)
    with patch('android_text.subprocess.Popen', return_value=process) as spawn:
        with native:
            assert not native.committed
            native.commit()
            with pytest.raises(NavigationError, match='permission-already-issued'):
                native.commit()
    command = spawn.call_args.args[0]
    assert '/system/framework/android.test.base.jar' in command
    assert process.communicate.call_count == 1
    assert case.controls == ['commit:' + 'a' * 32]

def test_cancel_without_permission_cannot_reach_a_send_click(case):
    native, process = native_fixture(case)
    with patch('android_text.subprocess.Popen', return_value=process):
        with native:
            assert not native.committed
    assert not native.committed
    assert process.communicate.call_count == 1
    assert case.controls == ['cancel:' + 'a' * 32]

def test_corrupt_native_readiness_has_a_named_error_and_no_commit(case):
    native, process = native_fixture(case, ready='{broken json')
    with patch('android_text.subprocess.Popen', return_value=process):
        with pytest.raises(NavigationError, match='ready-invalid-json') as raised:
            native.__enter__()
    assert isinstance(raised.value.__cause__, json.JSONDecodeError)
    assert case.controls == ['cancel:' + 'a' * 32]

def test_wrong_native_action_identity_is_rejected_before_permission(case):
    native, process = native_fixture(case, ready=json.dumps({'actionId': 'b' * 32, 'company': case.job['company'], 'messages': []}))
    with patch('android_text.subprocess.Popen', return_value=process):
        with pytest.raises(NavigationError, match='ready-contract-mismatch'):
            native.__enter__()
    assert case.controls == ['cancel:' + 'a' * 32]

def test_non_object_native_readiness_is_rejected_before_permission(case):
    native, process = native_fixture(case, ready='[]')
    with patch('android_text.subprocess.Popen', return_value=process):
        with pytest.raises(NavigationError, match='ready-contract-mismatch'):
            native.__enter__()
    assert case.controls == ['cancel:' + 'a' * 32]

def test_missing_real_method_execution_keeps_complete_diagnostic(case):
    native, process = native_fixture(case, ready='', stopped=True)
    process.communicate.side_effect = None
    process.communicate.return_value = (b'ClassNotFoundException: android.test.RepetitiveTest\nOK (1 test)', b'')
    with patch('android_text.subprocess.Popen', return_value=process):
        with pytest.raises(NavigationError, match='draft-preparation-failed'):
            native.__enter__()
    assert 'ClassNotFoundException' in native.diagnostic_path.read_text(encoding='utf-8')
    assert not native.committed

def test_real_java_selector_handles_the_observed_unnamed_send_button(case):
    configured = os.environ.get('JOB_HUNTER_TEST_JDK') or os.environ.get('JAVA_HOME')
    javac = Path(configured) / 'bin' / ('javac.exe' if os.name == 'nt' else 'javac') if configured else None
    if not javac or not javac.is_file():
        found = shutil.which('javac')
        javac = Path(found) if found else None
    if javac is None:
        pytest.skip('Native selector requires JDK; pass --jdk to tests/run_android.py')
    java = javac.with_name('java.exe' if os.name == 'nt' else 'java')
    repo = Path(__file__).resolve().parents[1]
    source = repo / 'skills/job-hunter/scripts/android/SendTarget.java'
    fixture = repo / 'tests/fixtures/android/NativeSendTargetCase.java'
    subprocess.run([str(javac), '--release', '8', '-encoding', 'UTF-8', '-d', str(case.root), str(source), str(fixture)], check=True, capture_output=True, timeout=30)
    result = subprocess.run([str(java), '-cp', str(case.root), 'jobhunter.NativeSendTargetCase'], check=True, capture_output=True, text=True, timeout=10)
    assert 'PASS: unlabeled icon' in result.stdout
