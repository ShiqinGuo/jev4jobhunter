"""Passive BOSS response adapter. Never constructs, changes or replays requests."""
from __future__ import annotations

import json
import os
import struct
import time
from pathlib import Path

import store


def running():
    import asyncio
    from mitmproxy import ctx
    async def stop_when_requested():
        while not (Path(os.environ['JOB_HUNTER_CAPTURE_DIR']) / 'stop').exists():
            await asyncio.sleep(.5)
        ctx.master.shutdown()
    asyncio.create_task(stop_when_requested())

LIST = '/api/zpgeek/app/geek/search/cardlist'
RECOMMEND = '/api/zpgeek/app/geek/recommend/joblist'
DETAIL = '/api/zpgeek/jobapp/geek/job/querydetail'
GREET = '/api/zpgeek/app/friend/add'


def decode(raw):
    if raw.lstrip()[:1] in (b'{', b'['):
        return json.loads(raw)
    # Official APK response framing, independently validated on 14.170.
    # Protocol reference: github.com/1013503897/boss-cli/blob/main/bosscli/yzwg.py
    from cryptography.hazmat.decrepit.ciphers.algorithms import ARC4
    from cryptography.hazmat.primitives.ciphers import Cipher
    import lz4.block
    cipher = Cipher(ARC4(b'a308f3628b3f39f7d35cdebeb6920e21'), mode=None).decryptor()
    body = cipher.update(raw) + cipher.finalize()
    if body[:8] == b'BZPBlock':
        if len(body) < 24:
            raise ValueError('short-response-frame')
        zero, size, original, check = struct.unpack('<IIII', body[8:24])
        if zero or check != original ^ size or original > 20_000_000 or len(body) != size + 24:
            raise ValueError('invalid-response-frame')
        body = lz4.block.decompress(body[24:], uncompressed_size=original)
    return json.loads(body)


def text(value):
    if isinstance(value, dict):
        return str(value.get('name', value.get('content', '')))
    return value if isinstance(value, str) else ''


def job(value, company=None, complete=False):
    identifier = value.get('jobId')
    if not isinstance(identifier, (int, str)) or not str(identifier):
        raise ValueError('job-id-missing')
    description = text(value.get('jobDesc'))
    return {'key': 'boss:' + str(identifier), 'jobId': str(identifier),
            'title': text(value.get('positionName', value.get('jobName'))), 'company': text(company or value.get('company', value.get('brandName'))),
            'city': text(value.get('locationName', value.get('city', value.get('cityName')))),
            'salary': text(value.get('salaryDesc')), 'experience': text(value.get('experienceName', value.get('jobExperience'))),
            'degree': text(value.get('degreeName', value.get('jobDegree'))), 'text': description,
            'complete': bool(complete and description.strip()),
            'publisherType': 'headhunter' if value.get('showHunterJob') in (True, 1) else 'unknown'}


def materials(path, envelope):
    """Extract only job material; auth, chat, profile and request bodies are discarded."""
    if not isinstance(envelope, dict):
        raise ValueError('response-envelope-required')
    code = envelope.get('code')
    if code != 0:
        return [{'type': 'error', 'code': code, 'source': path}]
    data = envelope.get('zpData') or {}
    if path == '/api/batch/requests':
        return [event for route, part in data.items() if route in (LIST, RECOMMEND, DETAIL, GREET)
                for event in materials(route, part)]
    if path == RECOMMEND:
        return [{'type': 'list', 'jobs': [job(row) for row in data.get('jobList', [])],
                 'hasMore': data.get('hasMore', True), 'source': path}]
    if path == LIST:
        groups = [row for row in data.get('cardList', []) if 'positionSearchCardList' in row]
        return [{'type': 'list', 'jobs': [job(row) for group in groups
                                        for row in group['positionSearchCardList']],
                 'hasMore': any(group.get('hasMore', True) for group in groups), 'source': path}]
    if path == DETAIL:
        base = data.get('jobBaseInfo') or {}
        if not base:
            raise ValueError('detail-body-missing')
        return [{'type': 'detail', 'job': job(base, (data.get('brandComInfo') or {}).get('brandName'), True), 'source': path}]
    if path == GREET:
        return [{'type': 'receipt', 'code': code, 'source': path}]
    return []


def request(flow):
    """Tag an existing UI request locally; do not modify the HTTP flow."""
    root = Path(os.environ['JOB_HUNTER_CAPTURE_DIR'])
    marker = root / 'action.json'
    if marker.is_file():
        flow.metadata['jobHunter'] = store.read_json(marker)


def response(flow):
    host = flow.request.host
    path = flow.request.path.split('?', 1)[0]
    if not (host == 'zhipin.com' or host.endswith('.zhipin.com')) or path not in ('/api/batch/requests', LIST, RECOMMEND, DETAIL, GREET):
        return
    context = flow.metadata.get('jobHunter')
    if not context or time.time() - context['at'] > 120:
        return
    root = Path(os.environ['JOB_HUNTER_CAPTURE_DIR'])
    try:
        events = materials(path, decode(flow.response.content))
    except (ValueError, TypeError, KeyError, struct.error, RuntimeError):
        events = [{'type': 'decode-error', 'source': path}]
    for index, event in enumerate(events):
        event = {**event, 'id': flow.id + '-' + str(index), 'at': time.time(),
                 'httpStatus': flow.response.status_code, **{k: context[k] for k in ('contextId', 'actionId', 'action', 'targetKey') if k in context}}
        relative = event['id'] + '.json'
        store.write_json(root / relative, event)
        with (root / 'index.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({k: event[k] for k in ('id', 'at', 'type', 'contextId', 'actionId')}) + '\n')
