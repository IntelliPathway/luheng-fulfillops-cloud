import socket
from struct import pack, unpack

import httpx
import pytest

from app.sip_lab_bridge import (
    APP,
    BridgeError,
    MediaSession,
    ResourceJournal,
    cleanup,
    owned_ids,
    pcmu,
    prepare_session,
    scoped_phone,
)
from app.sip_lab_media import MediaPacketError, decode_packet


def rtp(sequence=1, timestamp=160, ssrc=42):
    return pack('!BBHII', 128, 0, sequence, timestamp, ssrc) + b'\x90' * 160


def test_pcmu_encoder_known_vectors_and_rejects_invalid_frames():
    assert pcmu(pack('<5h', 0, 8, -8, 32124, -32124)) == bytes([255, 254, 126, 128, 0])
    for data in (b'', b'x', b'x' * 1922):
        with pytest.raises(MediaPacketError, match='invalid_pcm_frame'):
            pcmu(data)


def test_actual_udp_roundtrip_decodes_attenuates_and_reencodes_without_persistence():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as program, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as gateway:
        program.bind(('127.0.0.1', 0))
        gateway.bind(('127.0.0.1', 0))
        program.settimeout(1)
        gateway.settimeout(1)
        session = MediaSession(gateway.getsockname(), 0, 99)
        session.tone_index = 50
        gateway.sendto(rtp(), program.getsockname())
        data, peer = program.recvfrom(4097)
        session.receive(data, peer)
        assert session.tick(program, 0.02)
        response, _ = gateway.recvfrom(4097)
        frame = decode_packet(response, payload_type=0, ssrc=99)
        original = decode_packet(rtp(), payload_type=0, ssrc=42)
        expected = unpack('<160h', original.pcm_s16le)[0] // 2
        assert abs(unpack('<160h', frame.pcm_s16le)[0] - expected) < 200
        assert session.summary()['decoded_samples'] == 160
        assert session.summary()['provider_calls'] == 0
        assert not session.summary()['audio_verified']


def test_peer_ssrc_duplicate_budget_and_queue_are_bounded():
    peer = ('127.0.0.1', 10002)
    session = MediaSession(peer, 0, 99)
    session.receive(rtp(), ('127.0.0.1', 10003))
    assert session.stream is None and session.foreign == 1
    session.receive(b'bad', peer)
    assert session.stream is None and session.invalid == 1
    session.receive(rtp(), peer)
    session.receive(rtp(), peer)
    session.receive(rtp(sequence=2, timestamp=320, ssrc=43), peer)
    for index in range(2, 30):
        session.receive(rtp(sequence=index, timestamp=index * 160), peer)
    assert len(session.queue) == 10 and session.queue_drops > 0
    assert session.invalid == 3
    assert session.stream.packets_received == 29


def test_sender_has_twotone_prefix_no_catchup_flood_and_sixty_second_limit():
    class Sender:
        def __init__(self):
            self.sent = []

        def sendto(self, data, peer):
            self.sent.append(data)

    sender = Sender()
    session = MediaSession(('127.0.0.1', 10002), 0, 99)
    for index in range(50):
        assert session.tick(sender, index * 0.021)
    assert session.tone_index == 50 and len(sender.sent) == 50
    assert sender.sent[0][12:] != sender.sent[25][12:]
    assert session.tick(sender, 10)
    assert session.tick(sender, 10.001)
    assert len(sender.sent) == 51
    assert not session.tick(sender, 60)


def event(**changes):
    data = {'type': 'StasisStart', 'application': APP, 'channel': {'id': 'synthetic-channel',
            'name': 'PJSIP/1001-00000001', 'dialplan': {'context': 'lab-echo', 'exten': '1002'}}}
    data.update(changes)
    return data


def test_only_exact_fixed_inbound_test_channel_is_admitted():
    assert scoped_phone(event()) == 'synthetic-channel'
    for data in [event(application='other'), event(type='StasisEnd'), event(channel={}),
                 event(channel={'id': '../secret', 'name': 'PJSIP/1001-1',
                                'dialplan': {'context': 'lab-echo', 'exten': '1002'}}),
                 event(channel={'id': 'other', 'name': 'PJSIP/9999-1',
                                'dialplan': {'context': 'lab-echo', 'exten': '1002'}})]:
        assert scoped_phone(data) is None


def test_journal_restarts_keep_original_intent_and_quota(tmp_path):
    path = tmp_path / 'journal.db'
    journal = ResourceJournal(path, 'a' * 32)
    for index in range(20):
        journal.reserve(f'phone-{index}')
    journal.mark('phone-0', 'closed')
    journal.close()
    journal = ResourceJournal(path, 'a' * 32)
    with pytest.raises(BridgeError, match='existing_intent_cleanup_only'):
        journal.reserve('phone-0')
    with pytest.raises(BridgeError, match='media_daily_reservation_limit'):
        journal.reserve('new-phone')
    assert len(journal.unfinished()) == 19
    assert path.stat().st_mode & 0o777 == 0o600
    journal.close()


def fake_ari(journal, *, wrong_ip=False, fail_create=False):
    calls = []
    media, bridge = owned_ids(journal.instance, 'synthetic-channel')

    def response(request):
        calls.append(request)
        path = request.url.path
        if fail_create and path == '/ari/channels/externalMedia':
            raise httpx.ReadTimeout('synthetic timeout', request=request)
        if path.endswith('/variable'):
            value = '127.0.0.2' if wrong_ip else '127.0.0.1'
            if request.url.params['variable'].endswith('PORT'):
                value = '10002'
            return httpx.Response(200, json={'value': value})
        if path == '/ari/channels/externalMedia':
            return httpx.Response(200, json={'id': media})
        if path == f'/ari/bridges/{bridge}' and request.method == 'POST':
            # Intent must already be durable before any resource creation.
            assert journal.unfinished() == ['synthetic-channel']
            return httpx.Response(200, json={'id': bridge})
        return httpx.Response(204)

    return httpx.Client(base_url='http://asterisk:8088/ari', transport=httpx.MockTransport(response)), calls


def test_ari_creates_fixed_media_and_pins_destination_then_cleans_resources(tmp_path):
    journal = ResourceJournal(tmp_path / 'calls.db', 'a' * 32)
    client, calls = fake_ari(journal)
    session = prepare_session(client, journal, 'synthetic-channel', '127.0.0.1')
    assert session.peer == ('127.0.0.1', 10002)
    external = [r for r in calls if r.url.path.endswith('/externalMedia')][0]
    assert external.url.params['external_host'] == 'media:60000'
    assert external.url.params['format'] == 'ulaw' and external.url.params['direction'] == 'both'
    assert cleanup(client, journal, 'synthetic-channel')
    assert [r.method for r in calls[-3:]] == ['DELETE'] * 3
    assert journal.unfinished() == []
    journal.close()
    client.close()


@pytest.mark.parametrize('failure', ['address', 'timeout'])
def test_unknown_creation_and_foreign_destination_cleanup_without_recreate(tmp_path, failure):
    journal = ResourceJournal(tmp_path / 'calls.db', 'a' * 32)
    client, calls = fake_ari(journal, wrong_ip=failure == 'address', fail_create=failure == 'timeout')
    with pytest.raises((BridgeError, httpx.HTTPError)):
        prepare_session(client, journal, 'synthetic-channel', '127.0.0.1')
    assert journal.unfinished() == ['synthetic-channel']
    before = len(calls)
    assert cleanup(client, journal, 'synthetic-channel')
    assert all(r.method == 'DELETE' for r in calls[before:])
    assert not journal.unfinished()
    journal.close()
    client.close()


def test_cleanup_unknown_stays_durable_and_never_exposes_body(tmp_path):
    journal = ResourceJournal(tmp_path / 'calls.db', 'a' * 32)
    journal.reserve('synthetic-channel')
    with httpx.Client(base_url='http://asterisk:8088/ari', transport=httpx.MockTransport(
            lambda _: httpx.Response(500, text='DO_NOT_EXPOSE'))) as client:
        assert not cleanup(client, journal, 'synthetic-channel')
    assert journal.unfinished() == ['synthetic-channel']
    journal.close()


def test_media_image_context_excludes_generated_credentials():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    ignore = (root / 'deploy/sip-lab/media.Dockerfile.dockerignore').read_text()
    assert ignore.startswith('**\n') and '!deploy/sip-lab/generated' not in ignore
    compose = (root / 'deploy/sip-lab/compose.yml').read_text()
    assert 'profiles: [media]' in compose and 'media-journal:/var/lib/media-lab' in compose
    media = compose.split('  media:', 1)[1]
    assert 'ports:' not in media
    assert 'SIP_LAB_MEDIA_ACKNOWLEDGED:-false' in media


def test_event_loop_duplicate_stasis_and_end_cleanup_are_idempotent(tmp_path, capsys):
    from app.sip_lab_bridge import run_loop

    class Events:
        def __init__(self):
            import json
            self.messages = [json.dumps(event()), json.dumps(event()),
                             json.dumps(event(type='StasisEnd'))]
            self.reads = 0

        def recv(self, timeout):
            result = self.messages[self.reads]
            self.reads += 1
            return result

    class UDP:
        def recvfrom(self, size):
            raise BlockingIOError()

        def sendto(self, data, peer):
            pass

    journal = ResourceJournal(tmp_path / 'calls.db', 'a' * 32)
    client, calls = fake_ari(journal)
    events = Events()
    run_loop(client, events, UDP(), journal, '127.0.0.1', lambda: events.reads == 3)
    assert len([r for r in calls if r.url.path.endswith('/externalMedia')]) == 1
    assert [r.method for r in calls[-3:]] == ['DELETE'] * 3
    assert not journal.unfinished()
    assert 'synthetic-channel' not in capsys.readouterr().out
    client.close()
    journal.close()


def test_recovery_cleanup_unknown_blocks_new_calls_before_reading_events(tmp_path):
    from app.sip_lab_bridge import run_loop

    journal = ResourceJournal(tmp_path / 'calls.db', 'a' * 32)
    journal.reserve('synthetic-channel')
    methods = []
    with httpx.Client(base_url='http://asterisk:8088/ari', transport=httpx.MockTransport(
            lambda r: (methods.append(r.method), httpx.Response(500))[1])) as client:
        with pytest.raises(BridgeError, match='recovery_cleanup_unconfirmed'):
            run_loop(client, None, None, journal, '127.0.0.1', lambda: False)
    assert methods == ['DELETE'] * 3
    assert journal.unfinished() == ['synthetic-channel']
    journal.close()
