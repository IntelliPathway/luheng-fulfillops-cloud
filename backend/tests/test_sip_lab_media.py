from struct import pack, unpack

import pytest

from app.sip_lab_media import MediaPacketError, ReceiveStream, decode_packet


def packet(payload=b'\xff' * 160, *, pt=0, sequence=1, timestamp=160, ssrc=42, flags=128, extra=b''):
    return pack('!BBHII', flags, pt, sequence, timestamp, ssrc) + extra + payload


@pytest.mark.parametrize('pt,values,expected', [
    (0, [255, 127, 0, 128], [0, 0, -32124, 32124]),
    (8, [213, 85, 42, 170], [8, -8, -32256, 32256]),
])
def test_known_g711_vectors_and_pcm_format(pt, values, expected):
    frame = decode_packet(packet(bytes(values), pt=pt), payload_type=pt, ssrc=42)
    assert list(unpack('<4h', frame.pcm_s16le)) == expected
    assert frame.sample_count == 4 and frame.sample_rate == 8000 and frame.channels == 1
    assert 'pcm_s16le' not in repr(frame)


def test_all_codewords_match_independent_standard_library_reference():
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', DeprecationWarning)
        audioop = pytest.importorskip('audioop')
    payload = bytes(range(256))
    for pt, reference in [(0, audioop.ulaw2lin), (8, audioop.alaw2lin)]:
        frame = decode_packet(packet(payload, pt=pt), payload_type=pt, ssrc=42)
        assert frame.pcm_s16le == reference(payload, 2)


def test_csrc_extension_padding_and_marker_are_not_audio():
    extra = pack('!IHHI', 999, 0xBEDE, 1, 0)
    frame = decode_packet(packet(b'\xff\xff\x00\x02', flags=128 | 32 | 16 | 1,
                                 pt=128, extra=extra), payload_type=0, ssrc=42)
    assert frame.sample_count == 2 and frame.pcm_s16le == b'\x00' * 4


@pytest.mark.parametrize('data,error', [
    (b'\x00' * 11, 'invalid_packet_size'),
    (packet(flags=64), 'invalid_rtp_version'),
    (packet(pt=8), 'stream_binding_mismatch'),
    (packet(ssrc=43), 'stream_binding_mismatch'),
    (packet(b'', flags=129), 'truncated_csrc'),
    (packet(b'', flags=144), 'truncated_extension'),
    (packet(b'\x00' * 4, flags=144, extra=pack('!HH', 0, 100)), 'truncated_extension'),
    (packet(b'\x00', flags=160), 'invalid_padding'),
    (packet(b'\xff\x03', flags=160), 'invalid_padding'),
    (packet(b''), 'invalid_audio_payload_size'),
    (packet(b'\xff' * 961), 'invalid_audio_payload_size'),
    (b'x' * 4097, 'invalid_packet_size'),
])
def test_malformed_or_wrong_stream_packets_fail_without_content(data, error):
    with pytest.raises(MediaPacketError, match='^' + error + '$'):
        decode_packet(data, payload_type=0, ssrc=42)


def test_sequence_and_timestamp_wrap_loss_and_no_duplicate_audio():
    stream = ReceiveStream(payload_type=0, ssrc=42)
    stream.consume(packet(sequence=65535, timestamp=0xFFFFFF60))
    stream.consume(packet(sequence=0, timestamp=0))
    with pytest.raises(MediaPacketError, match='duplicate_or_out_of_order'):
        stream.consume(packet(sequence=0, timestamp=0))
    stream.consume(packet(sequence=2, timestamp=320))
    assert stream.summary()['missing_packets'] == 1
    assert stream.summary()['samples_received'] == 480
    assert not stream.summary()['network_adapter_ready']


@pytest.mark.parametrize('sequence,timestamp,error', [
    (0, 320, 'duplicate_or_out_of_order'),
    (2, 160, 'invalid_timestamp_progress'),
    (2, 161, 'invalid_timestamp_progress'),
    (2, 9000, 'invalid_timestamp_progress'),
])
def test_rejected_frames_do_not_advance_stream(sequence, timestamp, error):
    stream = ReceiveStream(payload_type=0, ssrc=42)
    stream.consume(packet())
    before = stream.summary()
    with pytest.raises(MediaPacketError, match=error):
        stream.consume(packet(sequence=sequence, timestamp=timestamp))
    assert stream.summary() == before
    stream.consume(packet(sequence=2, timestamp=320))


def test_budget_and_codec_binding_cannot_be_bypassed():
    stream = ReceiveStream(payload_type=0, ssrc=42, max_samples=160)
    stream.consume(packet())
    with pytest.raises(MediaPacketError, match='sample_budget_exhausted'):
        stream.consume(packet(sequence=2, timestamp=320))
    with pytest.raises(MediaPacketError, match='stream_binding_mismatch'):
        stream.consume(packet(sequence=2, timestamp=320, pt=8))
    assert stream.samples_received == 160


@pytest.mark.parametrize('options', [dict(payload_type=101, ssrc=42), dict(payload_type=True, ssrc=42),
                                     dict(payload_type=0, ssrc=-1), dict(payload_type=0, ssrc=42, max_samples=480001)])
def test_invalid_negotiation_or_budget_is_rejected(options):
    with pytest.raises(MediaPacketError):
        ReceiveStream(**options)
