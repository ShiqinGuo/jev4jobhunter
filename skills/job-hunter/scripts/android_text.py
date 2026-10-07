"""A pinned UIAutomator connection; only a persisted action can grant its sole send click."""
from __future__ import annotations
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid
from android_domain import DeviceUnavailable, NavigationError, SubmissionUncertain
from android_device import PACKAGE

def successful_runner_output(stdout: bytes) -> bool:
    """Runner final code -1 is normal; require the actual method's success report."""
    return (b'OK (1 test)' in stdout
            and b'INSTRUMENTATION_STATUS: class=jobhunter.NativeText' in stdout
            and b'INSTRUMENTATION_STATUS: test=testSetText' in stdout
            and b'INSTRUMENTATION_STATUS_CODE: 0' in stdout
            and b'INSTRUMENTATION_STATUS: stack=' not in stdout
            and b'FAILURES!!!' not in stdout)

class NativeTextSession:
    def __init__(self, device, resource: str, text: str, action_id: str):
        if not re.fullmatch(re.escape(PACKAGE) + r':id/\w+', resource) or not re.fullmatch(r'[a-f0-9]{32}', action_id):
            raise NavigationError('invalid-native-text-session-identity')
        self.device, self.resource, self.text, self.action_id = device, resource, text, action_id
        self.helper = Path(device.config.get('nativeTextJar', ''))
        if not self.helper.is_file() or not 0 < len(text.encode('utf-8')) <= 8192:
            raise NavigationError('native-text-helper-or-message-unavailable')
        identity = uuid.uuid4().hex
        self.diagnostic_path = self.helper.parent / ('native-input-' + identity + '.log')
        self.jar = f'/data/local/tmp/job-hunter-input-{identity}.jar'
        self.message = f'/data/local/tmp/job-hunter-text-{identity}.txt'
        self.ready = f'/data/local/tmp/job-hunter-ready-{identity}.json'
        self.control = f'/data/local/tmp/job-hunter-control-{identity}.txt'
        self.committed = False
        self.process = None

    def __enter__(self):
        self.local = tempfile.TemporaryDirectory(dir=self.helper.parent, prefix='text-input-')
        self.path = Path(self.local.name)
        try:
            message = self.path / 'message.txt'
            message.write_text(self.text, encoding='utf-8')
            self.device.adb('push', self.helper, self.jar)
            self.device.adb('push', message, self.message)
            self.process = subprocess.Popen([*self.device.prefix, 'shell', 'uiautomator', 'runtest', self.jar,
                '/system/framework/android.test.base.jar',
                '-c', 'jobhunter.NativeText', '-e', 'resource', self.resource, '-e', 'textFile', self.message,
                '-e', 'readyFile', self.ready, '-e', 'controlFile', self.control, '-e', 'actionId', self.action_id],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                raw = self.device.adb('shell', f'if [ -f {self.ready} ]; then cat {self.ready}; fi')
                if raw.strip():
                    try:
                        value = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise NavigationError('native-ready-invalid-json') from exc
                    if not isinstance(value,dict) or value.get('actionId') != self.action_id or not isinstance(value.get('company'), str) or not isinstance(value.get('messages'), list):
                        raise NavigationError('native-ready-contract-mismatch')
                    for row in value['messages']:
                        if not isinstance(row.get('text'), str) or type(row.get('outbound')) is not bool or type(row.get('delivered')) is not bool:
                            raise NavigationError('native-message-contract-mismatch')
                    self.snapshot = {key: value[key] for key in ('company', 'messages')}
                    return self
                if self.process.poll() is not None:
                    stdout, stderr = self.process.communicate()
                    self.record(stdout, stderr)
                    raise NavigationError('native-draft-preparation-failed:' + str(self.diagnostic_path))
                time.sleep(.2)
            raise NavigationError('native-draft-readiness-timeout')
        except BaseException:
            try:
                self.close()
            except (DeviceUnavailable, OSError):
                pass
            raise

    def command(self, value: str):
        path = self.path / 'control.txt'
        path.write_text(value + ':' + self.action_id, encoding='utf-8')
        self.device.adb('push', path, self.control)

    def record(self, stdout: bytes, stderr: bytes):
        # Diagnostic failure never changes permission or the durable delivery result.
        try:
            self.diagnostic_path.write_bytes(stdout + b'\nSTDERR\n' + stderr)
        except OSError:
            pass

    def commit(self):
        if self.committed:
            raise NavigationError('native-send-permission-already-issued')
        self.committed = True
        try:
            self.command('commit')
            stdout, stderr = self.process.communicate(timeout=20)
        except (subprocess.TimeoutExpired, DeviceUnavailable, OSError) as exc:
            raise SubmissionUncertain('native-send-completion-unknown:no-resend') from exc
        self.record(stdout, stderr)
        if not successful_runner_output(stdout):
            raise SubmissionUncertain('native-send-result-unknown:' + str(self.diagnostic_path))

    def close(self):
        try:
            if self.process is not None and self.process.poll() is None and not self.committed:
                self.command('cancel')
                try:
                    stdout, stderr = self.process.communicate(timeout=5)
                    self.record(stdout, stderr)
                except subprocess.TimeoutExpired:
                    # The phone helper expires its permission wait after 30 seconds.
                    return
            if self.process is None or self.process.poll() is not None:
                self.device.adb('shell', 'rm', '-f', self.jar, self.message, self.ready, self.ready+'.tmp', self.control)
        finally:
            self.local.cleanup()

    def __exit__(self, kind, value, traceback):
        try:
            self.close()
        except (DeviceUnavailable, OSError):
            if kind is None:
                raise
