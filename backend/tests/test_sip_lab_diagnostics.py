import json
import sys

import httpx
import pytest
from test_sip_lab import config as config
from test_sip_lab import generator

from app import sip_lab
from app.sip_lab_diagnostics import configuration_checks


def test_doctor_reports_safe_generated_configuration_without_values(tmp_path):
    directory = tmp_path / 'generated'
    generator().generate(directory, '192.168.1.20')
    result = configuration_checks(directory)
    assert result['configuration_ready']
    text = json.dumps(result)
    assert '192.168.' not in text and 'password' not in text
    assert all(item['passed'] for item in result['checks'])


@pytest.mark.parametrize('before,after,failed', [
    ('media_address=192.168.1.20\nbind_rtp', 'media_address=172.20.0.2\nbind_rtp', 'host_media_advertised'),
    ('bind_rtp_to_media_address=no', 'bind_rtp_to_media_address=yes', 'container_media_binding_disabled'),
    ('media_encryption=no', 'media_encryption=sdes', 'unencrypted_echo_media'),
    ('allow=ulaw,alaw', 'allow=opus', 'compatible_codecs'),
    ('rtp_symmetric=yes', 'rtp_symmetric=no', 'symmetric_media'),
    ('username=1001', 'username=9999', 'fixed_extension_scope'),
])
def test_doctor_blocks_common_media_and_scope_failures(tmp_path, before, after, failed):
    directory = tmp_path / 'generated'
    generator().generate(directory, '192.168.1.20')
    path = directory / 'pjsip.conf'
    path.write_text(path.read_text().replace(before, after))
    result = configuration_checks(directory)
    assert not result['configuration_ready']
    assert {'check': failed, 'passed': False} in result['checks']


def test_missing_or_ambiguous_files_never_echo_input(tmp_path):
    assert not configuration_checks(tmp_path)['configuration_ready']
    generator().generate(tmp_path / 'generated')
    path = tmp_path / 'generated/pjsip.conf'
    path.write_text(path.read_text() + '\npassword=DO_NOT_EXPOSE\npassword=SECOND_SECRET\n')
    result = configuration_checks(tmp_path / 'generated')
    assert not result['configuration_ready']
    assert 'SECRET' not in json.dumps(result) and 'DO_NOT_EXPOSE' not in json.dumps(result)


@pytest.mark.parametrize('state,exit_code', [('online', 0), ('offline', 2)])
def test_doctor_is_read_only_and_cannot_claim_audio(config, tmp_path, monkeypatch, capsys, state, exit_code):
    directory = tmp_path / 'generated'
    generator().generate(directory)
    journal = tmp_path / 'no-journal.db'
    requests = []
    original = sip_lab.SIPLab

    def response(request):
        requests.append((request.method, request.url.path))
        return httpx.Response(200, json={'technology': 'PJSIP', 'resource': '1001', 'state': state})

    monkeypatch.setattr(sip_lab, 'SIPLab', lambda cfg, log: original(cfg, log, httpx.MockTransport(response)))
    monkeypatch.setattr(sys, 'argv', ['sip_lab', 'doctor', '--config-dir', str(directory), '--journal', str(journal)])
    assert sip_lab.main() == exit_code
    result = json.loads(capsys.readouterr().out)
    assert result['ready_for_echo_attempt'] == (state == 'online')
    assert not result['audio_verified'] and not result['ai_dialogue_ready'] and not result['pstn_enabled']
    assert requests == [('GET', '/ari/endpoints/PJSIP/1001')]
    assert not journal.exists()


def test_log_summary_removes_crypto_auth_addresses_and_identifiers():
    from app.sip_lab_diagnostics import log_summary

    text = '''asterisk-1 | a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:DO_NOT_EXPOSE
asterisk-1 | Authorization: Digest password=DO_NOT_EXPOSE
asterisk-1 | c=IN IP4 192.168.1.20
asterisk-1 | Call-ID: DO_NOT_EXPOSE
asterisk-1 | m=audio 10016 RTP/AVP 0 8 101
asterisk-1 | m=audio 58000 RTP/SAVP 0 8
asterisk-1 | SIP/2.0 200 OK
asterisk-1 | SIP/2.0 488 Not Acceptable Here
asterisk-1 | Sent RTP packet to      192.168.1.20:58000 (type 00)
asterisk-1 | Got RTP packet from    192.168.1.20:58000 (type 00)
asterisk-1 | [date] ERROR[92]: Couldn't negotiate stream 0:audio-0:audio:sendrecv (nothing)'''
    result = log_summary(text)
    assert result['received_rtp_packets'] == result['sent_rtp_packets'] == 1
    assert result['sdp_encrypted_audio_offers'] == result['sdp_plain_audio_offers'] == 1
    assert result['audio_negotiation_errors'] == result['sip_unacceptable_responses'] == 1
    assert not result['audio_verified'] and not result['single_call_correlation']
    output = json.dumps(result)
    assert 'DO_NOT_EXPOSE' not in output and '192.168.' not in output


def test_log_summary_refuses_oversized_input():
    from app.sip_lab_diagnostics import log_summary

    assert log_summary('x' * (1024 * 1024 + 1)) == {'error': 'diagnostic_input_too_large'}
