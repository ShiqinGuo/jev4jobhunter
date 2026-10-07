"""Passive addon: snapshot a UI marker and publish whitelisted response events."""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import time
from android_domain import Marker, ProtocolError
from boss_protocol import ROUTES, BATCH, decode_payload, extract_events
import store

def owner_monitor(pid: int):
    """A process handle detects the original Windows owner, including PID reuse."""
    if not pid:
        return lambda: True, lambda: None
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            raise OSError(ctypes.get_last_error(), 'capture-owner-handle-unavailable')
        return lambda: kernel.WaitForSingleObject(handle, 0) == 258, lambda: kernel.CloseHandle(handle)
    def alive():
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True
    return alive, lambda: None

def running():
    from mitmproxy import ctx
    async def control():
        root = Path(os.environ['JOB_HUNTER_CAPTURE_DIR'])
        alive, close = owner_monitor(int(os.environ.get('JOB_HUNTER_OWNER_PID', '0')))
        store.write_json(root / 'ready.json', {'pid': os.getpid(), 'at': time.time(),
                         'session': os.environ['JOB_HUNTER_CAPTURE_SESSION']})
        try:
            while not (root / 'stop').exists():
                if not alive():
                    from android_device import Device
                    from android_receipts import ProxySession
                    saved = store.read_json(root / 'proxy.json')
                    if saved.get('session') == os.environ['JOB_HUNTER_CAPTURE_SESSION']:
                        ProxySession(Device(saved['deviceConfig']), root).stop()
                    break
                await asyncio.sleep(.25)
            ctx.master.shutdown()
        finally:
            close()
    asyncio.create_task(control())

def request(flow):
    root = Path(os.environ['JOB_HUNTER_CAPTURE_DIR'])
    path = flow.request.path.split('?', 1)[0]
    if path not in (*ROUTES, BATCH):
        return
    marker_path = root / 'action.json'
    if marker_path.exists():
        flow.metadata['jobHunter'] = Marker.from_dict(store.read_json(marker_path)).to_dict()

def response(flow):
    path = flow.request.path.split('?', 1)[0]
    host = flow.request.host
    if not (host == 'zhipin.com' or host.endswith('.zhipin.com')) or path not in (*ROUTES, BATCH):
        return
    value = flow.metadata.get('jobHunter')
    if value is None:
        return
    marker = Marker.from_dict(value)
    if flow.request.timestamp_start < marker.at or flow.request.timestamp_start - marker.at > 120:
        return
    root = Path(os.environ['JOB_HUNTER_CAPTURE_DIR'])
    headers = {name: flow.response.headers.get(name, '') for name in
               ('content-type', 'content-encoding', 'zp-encoding', 'zp-compressing', 'zp-encrypting')}
    try:
        content = flow.response.content
    except ValueError as exc:
        events = [{'type': 'diagnostic', 'source': path, 'stage': 'http-content',
                   'exceptionType': type(exc).__name__, 'reason': str(exc)[:160]}]
    else:
        try:
            events = extract_events(path, decode_payload(content, headers))
        except ProtocolError as exc:
            events = [{'type': 'diagnostic', 'source': path, 'stage': exc.stage,
                       'exceptionType': type(exc.__cause__ or exc).__name__, 'reason': str(exc)[:160]}]
    for number, event in enumerate(events):
        identifier = f'{flow.id}-{number}'
        if event['type'] == 'diagnostic' and path == '/api/zpgeek/app/friend/add':
            samples = root / 'diagnostics'
            samples.mkdir(exist_ok=True)
            raw = flow.response.raw_content or b''
            sample = samples / (identifier + '-wire.bin')
            sample.write_bytes(raw[:262144])
            event.update(wireSample=str(sample), wireLength=len(raw), sampleTruncated=len(raw) > 262144,
                         responseHeaders=headers)
        event.update(id=identifier, at=time.time(), httpStatus=flow.response.status_code, marker=value)
        store.write_json(root / (identifier + '.json'), event)
        with (root / 'index.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'id': identifier, 'action_id': marker.action_id}) + '\n')
            stream.flush()
