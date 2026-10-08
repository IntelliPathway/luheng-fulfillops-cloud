"""Read-only checks for fixed SIP echo setup; never return config values or secrets."""
import configparser
import ipaddress
from pathlib import Path

PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in
                         ('127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


def _sections(path):
    # Asterisk repeats [1001] for endpoint and AOR: preserve each section.
    groups = []
    current = None
    for line in path.read_text().splitlines():
        line = line.split(';', 1)[0].strip()
        if line.startswith('[') and line.endswith(']'):
            current = {'section': line[1:-1]}
            groups.append(current)
        elif current is not None and '=' in line:
            key, value = line.split('=', 1)
            key = key.strip().lower()
            if key in current:
                raise ValueError('duplicate directive')
            current[key] = value.strip()
    return groups


def configuration_checks(directory):
    """Only allowlisted booleans leave the local diagnostic; no addresses/passwords."""
    checks = []

    def check(name, passed):
        checks.append({'check': name, 'passed': bool(passed)})

    try:
        groups = _sections(Path(directory) / 'pjsip.conf')
        transports = [g for g in groups if g.get('type') == 'transport']
        endpoints = [g for g in groups if g.get('type') == 'endpoint']
        aors = [g for g in groups if g.get('type') == 'aor']
        auths = [g for g in groups if g.get('type') == 'auth']
        if len(transports) != 1 or len(endpoints) != 1 or len(aors) != 1 or len(auths) != 1:
            raise ValueError('scope')
        transport, endpoint, aor, auth = transports[0], endpoints[0], aors[0], auths[0]
        binding = (Path(directory) / 'compose.env').read_text().strip()
        if not binding.startswith('SIP_LAB_BIND_ADDRESS=') or '\n' in binding:
            raise ValueError('binding')
        address = binding.split('=', 1)[1]
        parsed = ipaddress.ip_address(address)
        check('private_ipv4_binding', parsed.version == 4 and any(parsed in n for n in PRIVATE_NETWORKS))
        check('fixed_extension_scope', endpoint.get('section') == aor.get('section') == '1001'
              and endpoint.get('aors') == '1001' and endpoint.get('auth') == auth.get('section')
              and auth.get('username') == '1001' and endpoint.get('context') == 'lab-echo')
        check('udp_transport', transport.get('protocol') == 'udp'
              and endpoint.get('transport') == transport.get('section'))
        check('host_media_advertised', endpoint.get('media_address') == address
              and transport.get('external_media_address') == address)
        check('container_media_binding_disabled', endpoint.get('bind_rtp_to_media_address') == 'no')
        check('unencrypted_echo_media', endpoint.get('media_encryption') == 'no')
        check('compatible_codecs', endpoint.get('disallow') == 'all'
              and set(endpoint.get('allow', '').split(',')) == {'ulaw', 'alaw'})
        check('symmetric_media', all(endpoint.get(k) == 'yes' for k in
                                    ('rtp_symmetric', 'force_rport', 'rewrite_contact'))
              and endpoint.get('direct_media') == 'no')
        check('single_contact', aor.get('max_contacts') == '1')
    except (OSError, UnicodeError, ValueError, configparser.Error):
        return {'configuration_ready': False,
                'checks': [{'check': 'local_configuration_readable_and_unambiguous', 'passed': False}]}
    return {'configuration_ready': all(item['passed'] for item in checks), 'checks': checks}


def log_summary(text):
    """Summarize a bounded local log; never expose SIP headers, keys, IPs or IDs."""
    import re

    result = {'received_rtp_packets': 0, 'sent_rtp_packets': 0,
              'sdp_plain_audio_offers': 0, 'sdp_encrypted_audio_offers': 0,
              'audio_negotiation_errors': 0, 'sip_ok_responses': 0,
              'sip_unacceptable_responses': 0, 'audio_verified': False,
              'ai_dialogue_ready': False, 'single_call_correlation': False}
    if len(text) > 1024 * 1024:
        return {'error': 'diagnostic_input_too_large'}
    for line in text.splitlines():
        # Restrict matching to known log/SDP syntax; payload/header values never escape.
        line = line.split('|', 1)[-1].strip()
        if line.startswith('Got RTP packet from'):
            result['received_rtp_packets'] += 1
        elif line.startswith('Sent RTP packet to'):
            result['sent_rtp_packets'] += 1
        elif re.fullmatch(r'm=audio \d+ RTP/AVP(?: \d+)+', line):
            result['sdp_plain_audio_offers'] += 1
        elif re.fullmatch(r'm=audio \d+ (?:RTP/SAVP[F]?|UDP/TLS/RTP/SAVP[F]?)(?: \d+)+', line):
            result['sdp_encrypted_audio_offers'] += 1
        elif "Couldn't negotiate stream" in line and 'audio' in line and 'ERROR[' in line:
            result['audio_negotiation_errors'] += 1
        elif line.startswith('SIP/2.0 200 '):
            result['sip_ok_responses'] += 1
        elif line.startswith('SIP/2.0 488 '):
            result['sip_unacceptable_responses'] += 1
    return result


def main():
    import json
    import sys

    result = log_summary(sys.stdin.read(1024 * 1024 + 1))
    print(json.dumps(result))
    return 2 if 'error' in result else 0


if __name__ == '__main__':
    raise SystemExit(main())
