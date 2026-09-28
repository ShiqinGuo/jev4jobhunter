"""ADB UI control and local proxy lifecycle; configuration stays outside the plugin."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET

import store

PACKAGE = 'com.hpbr.bosszhipin'


class Device:
    def __init__(self, config, directory):
        self.config, self.directory = config, Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.prefix = [config['adb'], '-P', str(config.get('serverPort', 5037)), '-s', config['serial']]
        for field in ('manufacturer', 'model'):
            if self.adb('shell', 'getprop', 'ro.product.' + field).strip() != config[field]:
                raise store.StoreError('android-device-identity-mismatch')

    def adb(self, *args):
        result = subprocess.run([*self.prefix, *map(str, args)], capture_output=True, timeout=25)
        if result.returncode:
            raise store.StoreError('adb-failed:' + result.stderr.decode('utf-8', 'replace')[:200])
        return result.stdout.decode('utf-8', 'replace')

    def signature(self):
        if hasattr(self, '_signature'):
            return self._signature
        sizes = re.findall(r'(\d+)x(\d+)', self.adb('shell', 'wm', 'size'))
        package = self.adb('shell', 'dumpsys', 'package', PACKAGE)
        version = re.search(r'versionName=([^\s]+)', package)
        if not sizes or not version:
            raise store.StoreError('android-app-or-screen-unavailable')
        self._signature = {'serial': self.config['serial'], 'size': list(map(int, sizes[-1])), 'appVersion': version[1]}
        return self._signature

    def mark(self, context, action, target=None):
        marker = {'contextId': context, 'actionId': uuid.uuid4().hex, 'action': action,
                  'targetKey': target, 'at': time.time()}
        store.write_json(self.directory / 'action.json', marker)
        return marker

    def events(self, marker=None):
        index = self.directory / 'index.jsonl'
        if not index.exists():
            return []
        result = []
        for line in index.read_text(encoding='utf-8').splitlines():
            try:
                item = json.loads(line)
            except ValueError:  # A concurrent append can leave the final line incomplete.
                continue
            if marker and (item['contextId'] != marker['contextId'] or item['actionId'] != marker['actionId']):
                continue
            result.append(store.read_json(self.directory / (item['id'] + '.json')))
        return result

    def wait(self, marker, kind, seconds=6):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            for event in self.events(marker):
                if event['type'] in ('error', 'decode-error'):
                    raise store.StoreError('android-response-' + event['type'])
                if event['type'] == kind:
                    return event
            time.sleep(.2)  # Reads only the compact local response index, never a page snapshot.
        return None

    def ui(self):
        raw = self.adb('exec-out', 'uiautomator', 'dump', '/dev/tty')
        start, end = raw.find('<?xml'), raw.rfind('</hierarchy>')
        if start < 0 or end < 0:
            raise store.StoreError('android-ui-unavailable')
        tree = ET.fromstring(raw[start:end + len('</hierarchy>')])
        nodes = list(tree.iter('node'))
        if not any(node.get('package') == PACKAGE for node in nodes):
            raise store.StoreError('boss-not-foreground')
        for node in nodes:
            label = node.get('text', '')
            if len(label) < 120 and re.search('违规操作|访问受限|账号存在异常|账户存在异常|请完成.*验证|滑动验证|登录/注册|今日.*沟通.*上限', label):
                raise store.StoreError('platform-ui-blocked:' + label)
        return tree

    @staticmethod
    def bounds(node):
        values = list(map(int, re.findall(r'\d+', node.get('bounds', ''))))
        if len(values) != 4 or values[2] <= values[0] or values[3] <= values[1]:
            raise store.StoreError('visible-ui-bounds-required')
        return values

    def tap(self, node):
        left, top, right, bottom = self.bounds(node)
        self.adb('shell', 'input', 'tap', (left + right) // 2, (top + bottom) // 2)

    def button(self, label, tree=None):
        matches = [node for node in (tree if tree is not None else self.ui()).iter('node')
                   if node.get('text') == label or node.get('content-desc') == label]
        if len(matches) != 1:
            raise store.StoreError('unique-visible-button-required:' + label)
        return matches[0]

    def cards(self, tree=None):
        tree = tree if tree is not None else self.ui()
        parents = {child: parent for parent in tree.iter() for child in parent}
        cards = []
        for title in tree.iter('node'):
            if title.get('resource-id') != PACKAGE + ':id/tv_position_name':
                continue
            group = parents.get(title)
            while group is not None:
                company = next((node for node in group.iter('node') if node.get('resource-id') == PACKAGE + ':id/tv_company_name'), None)
                if company is not None:
                    cards.append({'title': title.get('text', '').replace(' &@ ', '').strip(),
                                  'company': company.get('text', ''), 'node': title})
                    break
                group = parents.get(group)
        return cards

    def swipe(self, profile, reverse=False):
        x, begin, end = profile['x'], profile['startY'], profile['endY']
        self.adb('shell', 'input', 'swipe', x, end if reverse else begin,
                 x, begin if reverse else end, profile['durationMs'])

    def back(self):
        self.adb('shell', 'input', 'keyevent', 4)

    def return_to_list(self):
        self.back()
        tree = self.ui()
        if not self.cards(tree) and any(n.get('text') in ('立即沟通', '继续沟通') for n in tree.iter('node')):
            self.back()
            tree = self.ui()
        if not self.cards(tree):
            raise store.StoreError('search-list-not-restored-after-send')

    def capture(self, stop=False):
        saved = self.directory / 'proxy.json'
        port = self.config.get('proxyPort', 8877)
        if stop:
            previous = store.read_json(saved)
            # Deleting http_proxy does not clear ProxyTracker's derived host/port.
            restored = ':0' if previous['proxy'] in ('null', '', ':0') else previous['proxy']
            self.adb('shell', 'settings', 'put', 'global', 'http_proxy', restored)
            if restored == ':0':
                host = self.adb('shell', 'settings', 'get', 'global', 'global_http_proxy_host').strip()
                derived_port = self.adb('shell', 'settings', 'get', 'global', 'global_http_proxy_port').strip()
                if host not in ('', 'null') or derived_port not in ('', 'null', '0'):
                    raise store.StoreError('proxy-restore-not-confirmed:keep-capture-running')
            self.adb('reverse', '--remove', 'tcp:' + str(port))
            # Termination is handled by a local control file inside our addon process.
            (self.directory / 'stop').touch()
            return {'status': 'stopped', 'proxyRestored': True}
        if saved.exists() and self.adb('shell', 'settings', 'get', 'global', 'http_proxy').strip() == f'127.0.0.1:{port}':
            raise store.StoreError('capture-already-configured:stop-before-restarting')
        with socket.socket() as check:
            if check.connect_ex(('127.0.0.1', port)) == 0:
                raise store.StoreError('capture-port-in-use')
        (self.directory / 'stop').unlink(missing_ok=True)
        script = str(Path(__file__).with_name('android_capture.py'))
        env = {**os.environ, 'JOB_HUNTER_CAPTURE_DIR': str(self.directory)}
        store.write_json(saved, {'proxy': self.adb('shell', 'settings', 'get', 'global', 'http_proxy').strip()})
        with (self.directory / 'proxy.log').open('ab') as log:
            subprocess.Popen([self.config['python'], '-c', 'from mitmproxy.tools.main import mitmdump; mitmdump()',
                '--listen-host', '127.0.0.1', '--listen-port', str(port), '--set', 'confdir=' + self.config['caDir'],
                '--set', 'flow_detail=0', '--set', 'termlog_verbosity=error', '--allow-hosts', r'(^|\.)(zhipin\.com|bosszhipin\.com)(:443)?$',
                '-s', script], env=env, stdout=log, stderr=log,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        for _ in range(30):
            with socket.socket() as check:
                if check.connect_ex(('127.0.0.1', port)) == 0:
                    try:
                        self.adb('reverse', 'tcp:' + str(port), 'tcp:' + str(port))
                        self.adb('shell', 'settings', 'put', 'global', 'http_proxy', f'127.0.0.1:{port}')
                    except (ValueError, OSError):
                        self.capture(stop=True)
                        raise
                    return {'status': 'capturing'}
            time.sleep(.2)
        (self.directory / 'stop').touch()
        raise store.StoreError('capture-start-failed:inspect-proxy.log')
