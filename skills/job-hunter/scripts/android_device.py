"""ADB observations and UI primitives. Business state belongs to the application."""
from __future__ import annotations
from io import BytesIO
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET
from android_domain import DeviceUnavailable, IdentityChanged, Layout, NavigationError, PlatformBlocked, Runtime

PACKAGE = 'com.hpbr.bosszhipin'

class Device:
    def __init__(self, config: dict):
        for name in ('adb', 'serial', 'manufacturer', 'model', 'python', 'caDir'):
            if not isinstance(config.get(name), str) or not config[name]:
                raise DeviceUnavailable('android-config-required:' + name)
        self.config = config
        self.prefix = [config['adb'], '-P', str(config.get('serverPort', 5037)), '-s', config['serial']]

    def adb(self, *args: str) -> str:
        try:
            result = subprocess.run([*self.prefix, *map(str, args)], capture_output=True, timeout=20)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise DeviceUnavailable('adb-transport-unavailable') from exc
        if result.returncode:
            raise DeviceUnavailable('adb-command-failed:' + result.stderr.decode('utf-8', 'replace')[:160])
        return result.stdout.decode('utf-8', 'replace')

    def observe_runtime(self) -> Runtime:
        for field in ('manufacturer', 'model'):
            actual = self.adb('shell', 'getprop', 'ro.product.' + field).strip()
            if actual != self.config[field]:
                raise IdentityChanged('android-device-identity-mismatch:' + field)
        sizes = re.findall(r'(\d+)x(\d+)', self.adb('shell', 'wm', 'size'))
        version = re.search(r'versionName=([^\s]+)', self.adb('shell', 'dumpsys', 'package', PACKAGE))
        if not sizes or not version:
            raise DeviceUnavailable('screen-or-app-version-unavailable')
        width, height = map(int, sizes[-1])
        physical_serial = self.adb('shell', 'getprop', 'ro.serialno').strip()
        if not physical_serial:
            raise IdentityChanged('physical-device-serial-unavailable')
        return Runtime(physical_serial, Layout(width, height, version[1]))

    def ui(self) -> ET.Element:
        raw = self.adb('exec-out', 'uiautomator', 'dump', '/dev/tty')
        begin, end = raw.find('<?xml'), raw.rfind('</hierarchy>')
        if begin < 0 or end < 0:
            raise NavigationError('ui-hierarchy-unavailable')
        try:
            tree = ET.fromstring(raw[begin:end + len('</hierarchy>')])
        except ET.ParseError as exc:
            raise NavigationError('invalid-ui-hierarchy') from exc
        if not any(n.get('package') == PACKAGE for n in tree.iter('node')):
            raise NavigationError('boss-not-foreground:inspect-lock-or-current-app')
        for node in tree.iter('node'):
            label = node.get('text', '')
            if len(label) < 120 and re.search(r'违规操作|访问受限|账号存在异常|账户存在异常|请完成.*验证|滑动验证|登录/注册', label):
                raise PlatformBlocked(label)
        return tree

    @staticmethod
    def bounds(node) -> tuple[int, int, int, int]:
        values = tuple(map(int, re.findall(r'\d+', node.get('bounds', ''))))
        if len(values) != 4 or values[2] <= values[0] or values[3] <= values[1]:
            raise NavigationError('visible-control-bounds-required')
        return values

    def tap(self, node):
        left, top, right, bottom = self.bounds(node)
        self.adb('shell', 'input', 'tap', (left + right) // 2, (top + bottom) // 2)

    def back(self):
        self.adb('shell', 'input', 'keyevent', 4)

    def swipe(self, profile: dict, reverse=False):
        start, end = profile['startY'], profile['endY']
        self.adb('shell', 'input', 'swipe', profile['x'], end if reverse else start,
                 profile['x'], start if reverse else end, profile['durationMs'])

    def button(self, label: str, tree=None):
        tree = self.ui() if tree is None else tree
        matches = [n for n in tree.iter('node') if n.get('text') == label or n.get('content-desc') == label]
        if len(matches) != 1:
            raise NavigationError('unique-visible-control-required:' + label)
        return matches[0]

    def screenshot(self, path: Path):
        from PIL import Image
        try:
            result = subprocess.run([*self.prefix, 'exec-out', 'screencap', '-p'], capture_output=True, timeout=20, check=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            raise DeviceUnavailable('screenshot-unavailable') from exc
        picture = Image.open(BytesIO(result.stdout)).convert('RGB')
        path.parent.mkdir(parents=True, exist_ok=True)
        picture.save(path)
        return picture

    def restart(self):
        lines = self.adb('shell', 'cmd', 'package', 'resolve-activity', '--brief', PACKAGE).strip().splitlines()
        if not lines or not lines[-1].startswith(PACKAGE + '/') or not re.fullmatch(r'[\w./]+', lines[-1]):
            raise NavigationError('boss-launcher-unavailable')
        self.adb('shell', 'am', 'force-stop', PACKAGE)
        self.adb('shell', 'am', 'start', '-W', '-n', lines[-1])

    def input_ascii(self, value: str):
        if not re.fullmatch(r'[A-Za-z0-9 .:/?=&_-]+', value) or '%s' in value:
            raise NavigationError('verified-text-input-required')
        # Quote for the Android shell as well as using an argv on the host.
        self.adb('shell', 'input', 'text', "'" + value.replace(' ', '%s') + "'")
