"""Mobile proxy contracts and incident regressions; no live platform."""
import pytest
import sys
from unittest.mock import Mock
from android_receipts import ProxySession
import store
pytestmark = [pytest.mark.android, pytest.mark.proxy]

def test_capture_owner_monitor_detects_exit_of_its_original_process(case):
    import subprocess
    import android_capture
    process = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    alive, close = android_capture.owner_monitor(process.pid)
    try:
        assert alive()
        process.communicate(input=b'finish', timeout=3)
        assert not alive()
    finally:
        close()
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=3)

def test_proxy_restore_explicit_and_listener_survives_failure(case):
    device = Mock(config={})
    directory = case.root / 'responses'
    proxy = ProxySession(device, directory)
    store.write_json(proxy.saved, {'proxy': 'null'})
    device.adb.side_effect = [None, ':0', '', '0', '']
    assert proxy.stop()['proxyRestored']
    device.adb.assert_any_call('shell', 'settings', 'put', 'global', 'http_proxy', ':0')
    (directory / 'stop').unlink()
    device.adb.side_effect = [None, ':0', '127.0.0.1', '8877']
    with pytest.raises(RuntimeError, match='derived-proxy'):
        proxy.stop()
    assert not (directory / 'stop').exists()
