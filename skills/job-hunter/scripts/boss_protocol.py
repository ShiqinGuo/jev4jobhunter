"""BOSS response formats and material whitelist, independent of mitmproxy and ADB."""
from __future__ import annotations
import base64
import binascii
import json
import struct
from android_domain import ProtocolError

LIST = '/api/zpgeek/app/geek/search/cardlist'
RECOMMEND = '/api/zpgeek/app/geek/recommend/joblist'
DETAIL = '/api/zpgeek/jobapp/geek/job/querydetail'
GREET = '/api/zpgeek/app/friend/add'
BATCH = '/api/batch/requests'
ROUTES = {LIST: 'list', RECOMMEND: 'list', DETAIL: 'detail', GREET: 'receipt'}
CODEC_VERSION = 1
SALT = b'a308f3628b3f39f7d35cdebeb6920e21'

def decode_payload(raw: bytes, headers: dict | None = None) -> dict:
    """Decode observed no-secret-key formats. Unknown/keyed formats stay diagnostic."""
    if not isinstance(raw, bytes) or not raw:
        raise ProtocolError('payload-decode', 'empty-or-missing-response-body')
    headers = headers or {}
    body = raw
    if body.lstrip()[:1] not in (b'{', b'['):
        from cryptography.hazmat.decrepit.ciphers.algorithms import ARC4
        from cryptography.hazmat.primitives.ciphers import Cipher
        if headers.get('zp-encoding') == '1':
            try:
                body = base64.b64decode(body.replace(b'-', b'+').replace(b'_', b'/').replace(b'~', b'='), validate=True)
            except binascii.Error as exc:
                raise ProtocolError('payload-decode', 'invalid-declared-base64') from exc
        cipher = Cipher(ARC4(SALT), mode=None).decryptor()
        body = cipher.update(body) + cipher.finalize()
        if body[:8] == b'BZPBlock':
            body = decompress_frame(body)
        elif body.lstrip()[:1] not in (b'{', b'['):
            raise ProtocolError('payload-decode', 'unsupported-key-or-wire-format')
    try:
        envelope = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError('payload-decode', 'invalid-json-body') from exc
    if not isinstance(envelope, dict):
        raise ProtocolError('business-envelope', 'response-object-required')
    return envelope

def decompress_frame(body: bytes) -> bytes:
    import lz4.block
    if len(body) < 24:
        raise ProtocolError('payload-decode', 'short-bzp-frame')
    zero, size, original, checksum = struct.unpack('<IIII', body[8:24])
    if zero or checksum != original ^ size or not 0 < original <= 20_000_000 or len(body) != size + 24:
        raise ProtocolError('payload-decode', 'invalid-bzp-frame')
    try:
        decoded = lz4.block.decompress(body[24:], uncompressed_size=original)
    except lz4.block.LZ4BlockError as exc:
        raise ProtocolError('payload-decode', 'invalid-lz4-block') from exc
    if len(decoded) != original:
        raise ProtocolError('payload-decode', 'bzp-original-length-mismatch')
    return decoded

def field_text(value) -> str:
    if isinstance(value, dict):
        value = value.get('name', value.get('content', ''))
    return value if isinstance(value, str) else ''

def extract_job(row: dict, company=None, complete=False) -> dict:
    if not isinstance(row, dict):
        raise ProtocolError('business-envelope', 'job-object-required')
    identifier = row.get('jobId')
    if isinstance(identifier, bool) or not isinstance(identifier, (int, str)) or not str(identifier):
        raise ProtocolError('business-envelope', 'job-id-required')
    description = field_text(row.get('jobDesc'))
    title = field_text(row.get('positionName', row.get('jobName')))
    employer = field_text(company or row.get('company', row.get('brandName')))
    if not title or not employer:
        raise ProtocolError('business-envelope', 'job-title-company-required')
    return {'key': 'boss:' + str(identifier), 'jobId': str(identifier), 'title': title, 'company': employer,
            'city': field_text(row.get('locationName', row.get('city', row.get('cityName')))),
            'salary': field_text(row.get('salaryDesc')), 'experience': field_text(row.get('experienceName', row.get('jobExperience'))),
            'degree': field_text(row.get('degreeName', row.get('jobDegree'))), 'text': description,
            'complete': complete and bool(description.strip()),
            'publisherType': 'headhunter' if row.get('showHunterJob') in (True, 1) else 'unknown'}

def extract_events(route: str, envelope: dict) -> list[dict]:
    if not isinstance(envelope, dict) or type(envelope.get('code')) is not int:
        raise ProtocolError('business-envelope', 'integer-business-code-required')
    if envelope['code'] != 0:
        data = envelope.get('zpData')
        identifier = data.get('jobId') if isinstance(data, dict) else None
        return [{'type': 'rejected', 'source': route, 'code': envelope['code'],
                 'message': field_text(envelope.get('message')),
                 'targetKey': 'boss:' + str(identifier) if identifier is not None else None}]
    data = envelope.get('zpData', {})
    if not isinstance(data, dict):
        raise ProtocolError('business-envelope', 'business-data-object-required')
    if route == BATCH:
        events = []
        for child_route, part in data.items():
            if child_route in ROUTES:
                try:
                    events.extend(extract_events(child_route, part))
                except ProtocolError as exc:
                    events.append({'type': 'diagnostic', 'source': child_route, 'stage': exc.stage, 'reason': str(exc)})
        return events
    if route == LIST:
        cards = data.get('cardList')
        if not isinstance(cards, list):
            raise ProtocolError('business-envelope', 'card-list-required')
        groups = [row for row in cards if isinstance(row, dict) and 'positionSearchCardList' in row]
        if not groups:
            raise ProtocolError('business-envelope', 'position-card-group-required')
        rows = [row for group in groups for row in group['positionSearchCardList']]
        return [{'type': 'list', 'jobs': [extract_job(row) for row in rows],
                 'hasMore': any(group.get('hasMore', True) for group in groups), 'source': route}]
    if route == RECOMMEND:
        if not isinstance(data.get('jobList'), list):
            raise ProtocolError('business-envelope', 'recommendation-list-required')
        return [{'type': 'list', 'jobs': [extract_job(row) for row in data['jobList']],
                 'hasMore': data.get('hasMore', True), 'source': route}]
    if route == DETAIL:
        return [{'type': 'detail', 'job': extract_job(data.get('jobBaseInfo'),
                  data.get('brandComInfo', {}).get('brandName'), True), 'source': route}]
    if route == GREET:
        identifier = data.get('jobId')
        return [{'type': 'receipt', 'code': 0, 'source': route,
                 'targetKey': 'boss:' + str(identifier) if identifier is not None else None}]
    return []
