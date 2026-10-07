"""Local event observation and the owned phone proxy lifecycle."""
from __future__ import annotations
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import uuid
from android_domain import Evidence, Marker, MobileError, Observation
from boss_protocol import ROUTES, GREET
import store

class ReceiptFeed:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def arm(self, marker: Marker):
        store.write_json(self.directory / 'action.json', marker.to_dict())

    def events(self, marker: Marker) -> list[dict]:
        index = self.directory / 'index.jsonl'
        if not index.exists():
            return []
        rows = index.read_text(encoding='utf-8').splitlines(keepends=True)
        events = []
        for number, line in enumerate(rows):
            if number == len(rows) - 1 and not line.endswith('\n'):
                continue
            row = json.loads(line)  # Corrupt complete records are storage failures.
            if row.get('action_id') != marker.action_id:
                continue
            event = store.read_json(self.directory / (row['id'] + '.json'))
            if event.get('marker') == marker.to_dict() and event['at'] >= marker.at:
                events.append(event)
        return events

    def wait_material(self, marker: Marker, kind: str, seconds=6) -> dict | None:
        deadline = time.monotonic() + seconds
        while True:
            for event in self.events(marker):
                if ROUTES.get(event.get('source')) == kind and event['type'] == kind:
                    if kind == 'detail' and event['job']['key'] != marker.target_key:
                        continue
                    return event
            if time.monotonic() >= deadline:
                return None
            time.sleep(.2)

    def observe(self, marker: Marker, seconds=6) -> Observation:
        deadline = time.monotonic() + seconds
        diagnostics = set()
        while True:
            for event in self.events(marker):
                if event['type'] == 'diagnostic':
                    diagnostics.add(event['id'])
                    continue
                if event.get('source') != GREET:
                    continue
                # The wire target must corroborate the marker. Opaque code=0 is insufficient.
                if event.get('targetKey') != marker.target_key:
                    continue
                if event['type'] in ('receipt', 'rejected'):
                    if event['type'] == 'receipt' and event.get('httpStatus') != 200:
                        diagnostics.add(event['id'])
                        continue
                    outcome = 'succeeded' if event['type'] == 'receipt' else 'failed'
                    proof = Evidence(marker.action_id, marker.account_id, marker.account_label,
                                     marker.target_key, event['id'], outcome)
                    return Observation(proof, tuple(sorted(diagnostics)))
            if time.monotonic() >= deadline:
                return Observation(None, tuple(sorted(diagnostics)))
            time.sleep(.2)

class ProxySession:
    def __init__(self, device, directory: Path):
        self.device, self.directory = device, directory
        self.port = device.config.get('proxyPort', 8877)
        self.saved = directory / 'proxy.json'
        self.started = False
        directory.mkdir(parents=True, exist_ok=True)

    def stop(self):
        if not self.saved.exists():
            return {'status': 'not-started'}
        value = store.read_json(self.saved)
        previous = value['proxy']
        restored = ':0' if previous in ('', 'null', ':0') else previous
        self.device.adb('shell', 'settings', 'put', 'global', 'http_proxy', restored)
        actual = self.device.adb('shell', 'settings', 'get', 'global', 'http_proxy').strip()
        if actual != restored:
            raise MobileError('proxy-restore-not-confirmed:keep-listener-running')
        if restored == ':0':
            host = self.device.adb('shell', 'settings', 'get', 'global', 'global_http_proxy_host').strip()
            port = self.device.adb('shell', 'settings', 'get', 'global', 'global_http_proxy_port').strip()
            if host not in ('', 'null') or port not in ('', 'null', '0'):
                raise MobileError('derived-proxy-restore-not-confirmed:keep-listener-running')
        reverse = self.device.adb('reverse', '--list')
        if any(len(row.split()) == 3 and row.split()[1] == 'tcp:' + str(self.port) for row in reverse.splitlines()):
            self.device.adb('reverse', '--remove', 'tcp:' + str(self.port))
        (self.directory / 'stop').touch()
        value.update(restored=True, restoredAt=store.stamp())
        store.write_json(self.saved, value)
        return {'status': 'stopped', 'proxyRestored': True}

    def start(self, watch_owner=True):
        previous = self.device.adb('shell', 'settings', 'get', 'global', 'http_proxy').strip()
        if previous == f'127.0.0.1:{self.port}':
            raise MobileError('owned-proxy-needs-recovery:run-stop')
        with socket.socket() as connection:
            if connection.connect_ex(('127.0.0.1', self.port)) == 0:
                raise MobileError('capture-port-in-use')
        for name in ('stop', 'ready.json', 'action.json'):
            (self.directory / name).unlink(missing_ok=True)
        session = uuid.uuid4().hex
        store.write_json(self.saved, {'proxy': previous, 'restored': False, 'session': session,
                         'deviceConfig': self.device.config})
        self.started = True
        script = Path(__file__).with_name('android_capture.py')
        env = {**os.environ, 'JOB_HUNTER_CAPTURE_DIR': str(self.directory), 'JOB_HUNTER_CAPTURE_SESSION': session,
               'JOB_HUNTER_OWNER_PID': str(os.getpid() if watch_owner else 0)}
        with (self.directory / 'proxy.log').open('ab') as log:
            process = subprocess.Popen([self.device.config['python'], '-c',
                'from mitmproxy.tools.main import mitmdump; mitmdump()', '--listen-host', '127.0.0.1',
                '--listen-port', str(self.port), '--set', 'confdir=' + self.device.config['caDir'],
                '--set', 'flow_detail=0', '--set', 'termlog_verbosity=error',
                '--allow-hosts', r'(^|\.)(zhipin\.com|bosszhipin\.com)(:443)?$', '-s', str(script)],
                env=env, stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        ready = self.directory / 'ready.json'
        for _ in range(40):
            if ready.exists() and store.read_json(ready).get('session') == session:
                self.device.adb('reverse', 'tcp:' + str(self.port), 'tcp:' + str(self.port))
                self.device.adb('shell', 'settings', 'put', 'global', 'http_proxy', f'127.0.0.1:{self.port}')
                return
            if process.poll() is not None:
                raise MobileError('capture-addon-start-failed:inspect-proxy.log')
            time.sleep(.2)
        (self.directory / 'stop').touch()
        raise MobileError('capture-readiness-timeout')

    @contextmanager
    def opened(self):
        try:
            self.start()
            yield self
        finally:
            if self.started:
                self.stop()
