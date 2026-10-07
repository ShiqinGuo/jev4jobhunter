"""Mobile protocol contracts and incident regressions; no live platform."""
import pytest
import json
from pathlib import Path
from unittest.mock import patch
from android_domain import ProtocolError
from android_receipts import ReceiptFeed
import boss_protocol as protocol
import store
pytestmark = [pytest.mark.android, pytest.mark.protocol]

@pytest.mark.parametrize('body,stage,reason',[
    (b'', 'payload-decode', 'empty-or-missing-response-body'),
    (b'{broken json', 'payload-decode', 'invalid-json-body'),
    (b'[]', 'business-envelope', 'response-object-required'),
],ids=['empty-body','corrupt-json','non-object-envelope'])
def test_invalid_response_stays_a_classified_diagnostic(body,stage,reason):
    with pytest.raises(ProtocolError,match=reason) as raised:
        protocol.decode_payload(body)
    assert raised.value.stage==stage

def test_real_observed_keyed_greet_prefix_is_diagnostic_and_never_a_business_success(case):
    wire = bytes.fromhex('e97a4416571b7c6a140c40ae46743266')
    with pytest.raises(ProtocolError, match='unsupported-key-or-wire-format') as raised:
        protocol.decode_payload(wire, {'zp-encoding': '0', 'zp-compressing': '2', 'zp-encrypting': '1'})
    assert raised.value.stage == 'payload-decode'

def test_supported_encrypted_bzp_frame_and_corruption_have_distinct_results(case):
    import struct
    import lz4.block
    from cryptography.hazmat.decrepit.ciphers.algorithms import ARC4
    from cryptography.hazmat.primitives.ciphers import Cipher
    envelope = {'code': 0, 'zpData': {'jobId': 1}}
    original = json.dumps(envelope).encode('utf-8')
    compressed = lz4.block.compress(original, store_size=False)
    frame = b'BZPBlock' + struct.pack('<IIII', 0, len(compressed), len(original), len(compressed) ^ len(original)) + compressed
    encryption = Cipher(ARC4(protocol.SALT), mode=None).encryptor()
    wire = encryption.update(frame) + encryption.finalize()
    assert protocol.decode_payload(wire) == envelope
    with pytest.raises(ProtocolError, match='invalid-bzp-frame'):
        protocol.decompress_frame(frame[:-1])

def test_capture_saves_greet_diagnostic_without_request_credentials(case):
    import os
    from types import SimpleNamespace
    import android_capture
    marker = case.app.marker('greet', 'boss:1', 'original-action')

    class BrokenResponse:
        headers = {'content-type': 'application/json', 'Authorization': 'never-save-auth'}
        raw_content = b'opaque-response'
        status_code = 200

        @property
        def content(self):
            raise ValueError('invalid declared HTTP encoding')
    flow = SimpleNamespace(id='observed-flow', metadata={'jobHunter': marker.to_dict()}, request=SimpleNamespace(path=protocol.GREET + '?ignored=never-save-query', host='api.zhipin.com', timestamp_start=marker.at + 1), response=BrokenResponse())
    root = case.root / 'capture'
    root.mkdir()
    with patch.dict(os.environ, {'JOB_HUNTER_CAPTURE_DIR': str(root)}):
        android_capture.response(flow)
    event = store.read_json(root / 'observed-flow-0.json')
    assert (event['type'], event['stage']) == ('diagnostic', 'http-content')
    assert 'never-save' not in json.dumps(event)
    assert Path(event['wireSample']).read_bytes() == b'opaque-response'

def test_capture_unlisted_route_does_not_read_or_store_a_body(case):
    import os
    from types import SimpleNamespace
    import android_capture
    root = case.root / 'capture'
    root.mkdir()
    flow = SimpleNamespace(request=SimpleNamespace(path='/api/auth', host='api.zhipin.com'))
    with patch.dict(os.environ, {'JOB_HUNTER_CAPTURE_DIR': str(root)}):
        android_capture.response(flow)
    assert not list(root.iterdir())

def test_route_specific_wait_accepts_late_receipt_after_unrelated_errors(case):
    feed = ReceiptFeed(case.root / 'responses')
    marker = case.app.marker('greet', 'boss:1', 'action')
    samples = [{'id': 'unrelated', 'type': 'diagnostic', 'source': protocol.DETAIL}, {'id': 'bad-greet', 'type': 'diagnostic', 'source': protocol.GREET}, {'id': 'wrong-target', 'type': 'receipt', 'source': protocol.GREET, 'targetKey': 'boss:2'}, {'id': 'receipt', 'type': 'receipt', 'source': protocol.GREET, 'targetKey': 'boss:1', 'httpStatus': 200}]
    for sample in samples:
        sample.update(marker=marker.to_dict(), at=marker.at + 1)
    with patch.object(feed, 'events', side_effect=[samples[:3], samples]), patch('android_receipts.time.sleep'):
        observation = feed.observe(marker, seconds=1)
    assert observation.evidence.outcome == 'succeeded'
    assert observation.evidence.source_id == 'receipt'
    assert set(observation.diagnostics) == {'unrelated', 'bad-greet'}

def test_business_rejection_and_batched_errors_have_independent_outcomes(case):
    rejection = protocol.extract_events(protocol.GREET, {'code': 71, 'zpData': {'jobId': 1}})[0]
    assert (rejection['type'], rejection['targetKey']) == ('rejected', 'boss:1')
    envelope = {'code': 0, 'zpData': {protocol.DETAIL: {'code': 0, 'zpData': {}}, protocol.GREET: {'code': 0, 'zpData': {'jobId': 1}}, '/api/auth': {'secret': 'never-store'}}}
    events = protocol.extract_events(protocol.BATCH, envelope)
    assert [e['type'] for e in events] == ['diagnostic', 'receipt']
    assert 'never-store' not in json.dumps(events)

def test_list_summary_is_never_full_jd(case):
    row = {'jobId': 1, 'positionName': 'Python', 'company': 'Example', 'jobDesc': {'content': 'Abstract'}}
    envelope = {'code': 0, 'zpData': {'cardList': [{'positionSearchCardList': [row], 'hasMore': True}]}}
    event = protocol.extract_events(protocol.LIST, envelope)[0]
    assert not event['jobs'][0]['complete']
    with pytest.raises(ProtocolError):
        protocol.decode_payload(b'{invalid json')
