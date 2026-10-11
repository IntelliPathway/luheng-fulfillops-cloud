import importlib.util
import json
import subprocess
from pathlib import Path
from struct import pack

import pytest

from app.sip_lab_bridge import BridgeError, MediaSession
from app.sip_lab_voice import VoiceError
from app.sip_lab_voice_session import VoiceMediaSession

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('sip_lab_launcher', ROOT / 'scripts/sip-lab.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


@pytest.fixture
def lab(tmp_path):
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'deploy/sip-lab').mkdir(parents=True)
    for name in ('sip-lab-config', 'sip-lab-address-update', 'sip-lab-qwen-voice-config', 'sip-lab-media-local-build', 'sip-lab-speech-config'):
        (tmp_path / 'scripts' / (name + '.py')).write_bytes((ROOT / 'scripts' / (name + '.py')).read_bytes())
    instance = launcher.Lab(tmp_path)
    instance.configure('192.168.50.10')
    return instance


def completed(arguments=(), stdout='', returncode=0):
    return subprocess.CompletedProcess(arguments, returncode, stdout, '')


def test_existing_credentials_and_journal_survive_repeated_start_configuration(lab, monkeypatch):
    directory = lab.root / 'deploy/sip-lab/generated'
    (directory / 'private-journal').write_text('synthetic durable resource intent')
    original = {path.name: path.read_bytes() for path in directory.iterdir()}
    monkeypatch.setenv('SIP_LAB_BIND_ADDRESS', '192.168.50.99')
    monkeypatch.setenv('SIP_LAB_MEDIA_MODE', 'voice')
    monkeypatch.setenv('QWEN_TOKEN_PLAN_API_KEY', 'synthetic-secret-must-not-propagate')
    lab.configure()
    assert original == {path.name: path.read_bytes() for path in directory.iterdir()}
    assert lab.environment['SIP_LAB_BIND_ADDRESS'] == '192.168.50.10'
    assert lab.environment['SIP_LAB_MEDIA_MODE'] == 'qwen'
    assert 'QWEN_TOKEN_PLAN_API_KEY' not in lab.environment


def test_second_launcher_cannot_restart_or_mutate_first_launcher_and_lock_releases(lab):
    lab.configure(exclusive=True)
    contender = launcher.Lab(lab.root)
    try:
        with pytest.raises(launcher.LabError, match='已有启动器正在运行'):
            contender.configure('192.168.50.20', exclusive=True)
        assert launcher.read_environment(lab.root)['SIP_LAB_BIND_ADDRESS'] == '192.168.50.10'
    finally:
        lab.close()
    contender.configure(exclusive=True)
    contender.close()


@pytest.mark.parametrize('failure', ('shell', 'mode', 'symlink', 'duplicate'))
def test_private_config_is_data_only_and_never_executes_shell(lab, failure, tmp_path):
    path = lab.root / 'deploy/sip-lab/generated/lab.env'
    if failure == 'shell':
        path.write_text(path.read_text() + 'export SHELL_VALUE=$(touch should-not-exist)\n')
    elif failure == 'mode':
        path.chmod(0o644)
    elif failure == 'symlink':
        other = tmp_path / 'outside'
        other.write_bytes(path.read_bytes())
        other.chmod(0o600)
        path.unlink()
        path.symlink_to(other)
    else:
        path.write_text(path.read_text() + 'export APP_ENV=development\n')
    with pytest.raises((launcher.LabError, ValueError, OSError)):
        launcher.read_environment(lab.root)
    assert not (lab.root / 'should-not-exist').exists()


def test_busy_gateway_blocks_address_mutation_and_media_restart(lab, monkeypatch):
    original = (lab.root / 'deploy/sip-lab/generated/lab.env').read_bytes()
    monkeypatch.setattr(lab, 'compose', lambda *args, **kwargs: completed(stdout=(
        'asterisk\nmedia\n' if args[0] == 'ps' else '1 active channels\n')))
    with pytest.raises(launcher.LabError, match='通道仍在使用'):
        lab.configure('192.168.50.20')
    assert original == (lab.root / 'deploy/sip-lab/generated/lab.env').read_bytes()
    monkeypatch.setattr(lab, 'dependencies', lambda **kwargs: None)
    monkeypatch.setattr(lab, 'run', lambda *args, **kwargs: completed())
    monkeypatch.setattr(lab, 'images', lambda: pytest.fail('must not build/restart during call'))
    with pytest.raises(launcher.LabError, match='通道仍在使用'):
        lab.start()


def test_confirmed_idle_address_update_preserves_passwords(lab, monkeypatch):
    old = lab.environment.copy()
    monkeypatch.setattr(lab, 'compose', lambda *args, **kwargs: completed(stdout='asterisk\n' if args[0] == 'ps'
                                                                            else '0 active channels\n'))
    lab.configure('192.168.50.20')
    assert lab.environment['SIP_LAB_BIND_ADDRESS'] == '192.168.50.20'
    for key in ('SIP_LAB_INSTANCE_ID', 'SIP_LAB_ARI_PASSWORD', 'SIP_LAB_QWEN_VOICE_TOKEN'):
        assert lab.environment[key] == old[key]


def test_image_revision_cache_rebuilds_only_changed_source_without_private_context(lab, monkeypatch):
    real_helper = launcher.helper
    builder = real_helper(lab.root, 'sip-lab-media-local-build')
    for name in sorted(builder.FILES) + ['deploy/sip-lab/media.Dockerfile', 'deploy/sip-lab/Dockerfile', 'deploy/sip-lab/start.sh']:
        path = lab.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / name).read_bytes())
    labels, builds = {}, []

    def build_media(root, *, source_digest, environment):
        assert 'QWEN_TOKEN_PLAN_API_KEY' not in environment
        builds.append('media')
        labels[builder.IMAGE] = source_digest

    def runner(arguments, **kwargs):
        if arguments[:3] == ['docker', 'image', 'inspect']:
            image = arguments[3]
            return completed(stdout=labels.get(image, '') + '\n', returncode=0 if image in labels or len(arguments) == 4 else 1)
        assert arguments[:3] == ['docker', 'build', '--pull=false']
        assert kwargs['environment']['DOCKER_BUILDKIT'] == '0'
        context = Path(arguments[-1])
        assert {path.name for path in context.iterdir()} == {'Dockerfile', 'start.sh'}
        assert not any('generated' in str(path) for path in context.rglob('*'))
        builds.append('asterisk')
        labels[arguments[4]] = arguments[6].split('=', 1)[1]
        return completed()

    monkeypatch.setattr(builder, 'build', build_media)
    monkeypatch.setattr(launcher, 'helper', lambda root, name: builder if name == 'sip-lab-media-local-build' else real_helper(root, name))
    monkeypatch.setattr(lab, 'run', runner)
    lab.images()
    lab.images()
    assert builds == ['asterisk', 'media']
    source = lab.root / 'backend/app/sip_lab_bridge.py'
    source.write_text(source.read_text() + '\n# synthetic change\n')
    lab.images()
    assert builds == ['asterisk', 'media', 'media']


def test_missing_cached_base_does_not_pull_or_build(lab, monkeypatch):
    for name in ('Dockerfile', 'start.sh'):
        (lab.root / 'deploy/sip-lab' / name).write_bytes((ROOT / 'deploy/sip-lab' / name).read_bytes())
    calls = []

    def runner(arguments, **kwargs):
        calls.append(arguments)
        return completed(returncode=1)

    monkeypatch.setattr(lab, 'run', runner)
    with pytest.raises(launcher.LabError, match='docker pull ubuntu:24.04'):
        lab.images()
    assert len(calls) == 2 and all(call[:3] == ['docker', 'image', 'inspect'] for call in calls)


def test_verified_existing_host_is_reused_and_never_terminated(lab, monkeypatch):
    monkeypatch.setattr(lab, 'doctor', lambda: {'host_ready': True, 'voice_session_limit_seconds': 180,
                                             'voice_test_matcher_version': 3, 'stop_confirmation_required': True})
    monkeypatch.setattr(launcher.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('must reuse host'))
    lab.ensure_host()
    lab.close()
    assert lab.host is None


def test_live_turn_and_failure_diagnostics_never_display_raw_fields(capsys):
    launcher.display_event({'event': 'voice_turn_diagnostic', 'request_number': 3, 'input_audio_ms': 780,
                            'test_case': 'stop_pending', 'stop_control': 'requested', 'transcript': 'SYNTHETIC_SECRET'})
    launcher.display_event({'event': 'media_session_summary', 'end_reason': 'voice_failed',
                            'local_voice_error': 'numbers_date_mismatch', 'provider_active_stage': 'llm',
                            'transcript': 'SYNTHETIC_SECRET'})
    output = capsys.readouterr().out
    assert '结束待确认' in output and '780ms' in output
    assert 'numbers_date_mismatch' in output and '最后云调用阶段：llm' in output
    assert 'SYNTHETIC_SECRET' not in output


def test_occupied_unready_host_is_not_taken_over(lab, monkeypatch):
    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, timeout):
            pass

        def connect_ex(self, address):
            assert address == ('127.0.0.1', 18092)
            return 0

    monkeypatch.setattr(lab, 'doctor', lambda: {'host_ready': False, 'host_busy': True})
    monkeypatch.setattr(launcher.socket, 'socket', Socket)
    monkeypatch.setattr(launcher.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('must not take over host'))
    with pytest.raises(launcher.LabError, match='不接管'):
        lab.ensure_host()


def test_owned_host_cleanup_runs_even_when_media_stop_fails(lab, monkeypatch):
    class Host:
        terminated = False
        waited = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            self.waited = True

    lab.host = Host()
    lab.media_started = True
    monkeypatch.setattr(lab, 'compose', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('synthetic secret exception')))
    with pytest.raises(OSError):
        lab.close()
    assert lab.host.terminated and lab.host.waited


def test_doctor_does_not_send_credentials_in_command_arguments(lab, monkeypatch):
    def runner(arguments, **kwargs):
        assert arguments[-3:] == ['-m', 'app.sip_lab_qwen_voice', 'doctor']
        assert lab.environment['SIP_LAB_QWEN_VOICE_TOKEN'] not in ' '.join(arguments)
        assert kwargs['environment']['SIP_LAB_QWEN_VOICE_TOKEN'] == lab.environment['SIP_LAB_QWEN_VOICE_TOKEN']
        return completed(stdout=json.dumps({'host_ready': True, 'external_calls': 0}))

    monkeypatch.setattr(lab, 'run', runner)
    assert lab.doctor()['external_calls'] == 0


def test_readable_summary_explains_end_and_discards_raw_audio_text_credentials(capsys):
    text = 'raw SIP trace synthetic-secret\n' + json.dumps({'event': 'media_session_summary',
            'end_reason': 'duration_limit', 'received_packets': 42, 'local_turns_completed': 3,
            'session_limit_seconds': 60,
            'cloud_provider_calls': 7, 'raw_reply': 'synthetic-secret', 'api_key': 'synthetic-secret'})
    for event in launcher.parse_events(text):
        launcher.display_event(event)
    output = capsys.readouterr().out
    assert '60 秒' in output and '42' in output and '3' in output and '7' in output
    assert 'synthetic-secret' not in output and 'SIP trace' not in output


class Voice:
    failed = False
    ended = False
    expected_provider = 'qwen_token_plan'

    def start(self):
        pass

    def finished(self):
        return self.failed or self.ended

    def pop(self):
        return None

    def summary(self):
        return {}

    def submit(self, audio):
        raise VoiceError('voice_turn_budget_exhausted')


def test_duration_explicit_end_failure_and_turn_limit_have_distinct_reasons():
    session = MediaSession(('127.0.0.1', 9999), 10, 1)
    session.mark_ended('channel_ended', 70)
    assert session.summary()['end_reason'] == 'duration_limit'
    session.mark_ended('service_stopped')
    assert session.summary()['end_reason'] == 'duration_limit'
    for failed, ended, reason in [(False, True, 'dialogue_ended'), (True, False, 'voice_failed')]:
        voice = Voice()
        voice.failed, voice.ended = failed, ended
        session = VoiceMediaSession(('127.0.0.1', 9999), 0, 1, 'a' * 64, client=voice)
        assert not session.tick(None, 1)
        assert session.summary()['end_reason'] == reason
    session = VoiceMediaSession(('127.0.0.1', 9999), 0, 1, 'a' * 64, client=Voice())
    session.vad.feed = lambda _: [('utterance', b'\0' * 3200)]
    session.process_audio(b'\0' * 320)
    assert not session.tick(None, 1)
    assert session.summary()['end_reason'] == 'turn_limit'


def test_old_authenticated_host_cannot_silently_truncate_extended_test(lab, monkeypatch):
    monkeypatch.setattr(lab, 'doctor', lambda: {'host_ready': True, 'voice_test_matcher_version': 3,
                                             'stop_confirmation_required': True})
    monkeypatch.setattr(launcher.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('must not take over old host'))
    with pytest.raises(launcher.LabError, match='旧 60 秒'):
        lab.ensure_host()
    lab.session_seconds = 60
    lab.ensure_host()
    assert lab.host is None


@pytest.mark.parametrize('verifying', [False, True])
def test_old_matcher_requires_operator_restart_without_process_takeover(lab, monkeypatch, verifying):
    lab.verifying = verifying
    monkeypatch.setattr(lab, 'doctor', lambda: {'host_ready': True, 'voice_session_limit_seconds': 180,
                                             'voice_test_metadata_version': 1, 'voice_test_matcher_version': 0})
    monkeypatch.setattr(launcher.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('must not take over old host'))
    with pytest.raises(launcher.LabError, match='旧短句匹配'):
        lab.ensure_host()
    assert lab.host is None


def test_extended_qwen_session_continues_past_old_deadline_but_still_stops_at_180():
    class Sender:
        def sendto(self, *args):
            pass

    session = VoiceMediaSession(('127.0.0.1', 9999), 0, 1, 'a' * 64, client=Voice(), session_seconds=180)
    assert session.tick(Sender(), 60)
    assert session.tick(Sender(), 179)
    assert not session.tick(Sender(), 180)
    assert session.summary()['end_reason'] == 'duration_limit'
    assert session.summary()['session_limit_seconds'] == 180
    with pytest.raises(VoiceError, match='invalid_voice_session_limit'):
        VoiceMediaSession(('127.0.0.1', 9999), 0, 1, 'a' * 64, session_seconds=180)


def test_extended_phone_continuous_rtp_decodes_beyond_sixty_seconds_and_keeps_budget():
    peer = ('127.0.0.1', 9999)
    session = VoiceMediaSession(peer, 0, 1, 'a' * 64, client=Voice(), session_seconds=180)
    for index in range(9001):
        session.receive(pack('!BBHII', 128, 0, index, index * 160, 42) + b'\xff' * 160, peer)
    assert session.stream.packets_received == 9000
    assert session.summary()['decoded_samples'] == 180 * 8000
    assert session.invalid == 1  # The packet beyond the new sample budget is still refused.


@pytest.mark.parametrize('limit', (True, 0, 61, 10000, '180'))
def test_unreviewed_or_unbounded_duration_is_rejected(limit):
    with pytest.raises(BridgeError, match='invalid_voice_session_limit'):
        MediaSession(('127.0.0.1', 9999), 0, 1, session_seconds=limit)


def test_single_entry_waits_for_dialplan_host_and_media_before_phone_ready(lab, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(lab, 'dependencies', lambda **kwargs: calls.append('dependencies'))
    monkeypatch.setattr(lab, 'run', lambda *args, **kwargs: completed())
    monkeypatch.setattr(lab, 'images', lambda: calls.append('images'))
    monkeypatch.setattr(lab, 'ensure_host', lambda: calls.append('authenticated_host'))

    def compose(*arguments, **kwargs):
        calls.append(arguments)
        if arguments[0] == 'ps':
            return completed(stdout='asterisk\n')
        if arguments[0] == 'exec':
            if arguments[-1] == 'core show channels count':
                return completed(stdout='0 active channels\n')
            return completed(stdout='Stasis(repayguard_media_lab) TIMEOUT(absolute)=180')
        if arguments[0] == 'logs':
            assert 'authenticated_host' in calls
            assert lab.media_started
            return completed(stdout=json.dumps({'event': 'awaiting_linphone_1003'}))
        return completed()

    monkeypatch.setattr(lab, 'compose', compose)

    def logs_start(*args, **kwargs):
        assert '测试已就绪' in capsys.readouterr().out
        raise KeyboardInterrupt()

    monkeypatch.setattr(launcher.subprocess, 'Popen', logs_start)
    with pytest.raises(KeyboardInterrupt):
        lab.start()
    lab.close()
    assert calls[-1] == ('stop', 'media')
    media = next(value for value in calls if isinstance(value, tuple) and value[-1] == 'media' and value[0] == 'up')
    assert '--no-deps' in media and '--no-build' in media and '--force-recreate' in media
    assert calls.index('authenticated_host') < calls.index(media)


def test_media_failure_never_announces_ready_and_cleans_owned_service(lab, monkeypatch, capsys):
    config = launcher.helper(ROOT, 'sip-lab-speech-config')
    original = config.save(lab.root / 'deploy/sip-lab/generated', config.payload('baseline')).read_bytes()
    lab.imported_speech = config.payload('warm_female')
    lab.speech_profile = 'warm_female'
    monkeypatch.setattr(lab, 'dependencies', lambda **kwargs: None)
    monkeypatch.setattr(lab, 'run', lambda *args, **kwargs: completed())
    monkeypatch.setattr(lab, 'images', lambda: None)
    monkeypatch.setattr(lab, 'ensure_host', lambda: None)
    monkeypatch.setattr(lab, 'ensure_idle', lambda: None)
    stopped = []

    def compose(*args, **kwargs):
        if args[0] == 'exec':
            return completed(stdout='Stasis(repayguard_media_lab) TIMEOUT(absolute)=180')
        if args[0] == 'logs':
            return completed(stdout=json.dumps({'event': 'media_lab_unavailable', 'stage': 'ari_connect',
                                               'error': 'ari_auth_failed'}))
        if args[0] == 'stop':
            stopped.append(args[-1])
        return completed()

    monkeypatch.setattr(lab, 'compose', compose)
    with pytest.raises(launcher.LabError, match='媒体桥未就绪'):
        lab.start()
    lab.close()
    assert stopped == ['media']
    assert (lab.root / 'deploy/sip-lab/generated/speech-settings.json').read_bytes() == original
    output = capsys.readouterr().out
    assert 'ari_auth_failed' in output and '测试已就绪' not in output


def test_voice_switch_refuses_to_take_over_existing_authenticated_host(lab, monkeypatch):
    lab.speech_profile = 'warm_female'
    monkeypatch.setattr(lab, 'doctor', lambda: {'host_ready': True, 'provider': {'speech_profile': 'clear_male'}})
    monkeypatch.setattr(launcher.subprocess, 'Popen', lambda *a, **kw: pytest.fail('must not restart old host'))
    with pytest.raises(launcher.LabError, match='音色不同'):
        lab.ensure_host()
    assert lab.host is None


def test_offline_dialogue_entry_needs_no_private_configuration_or_credentials(monkeypatch, capsys):
    monkeypatch.setattr(launcher.sys, 'argv', ['sip-lab.py', 'dialogue'])
    monkeypatch.setattr(launcher, 'read_environment', lambda *a: pytest.fail('must not read private config'))
    monkeypatch.setattr(launcher, 'Lab', lambda **kw: pytest.fail('must not start lab'))
    assert launcher.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report['passed'] and len(report['scenarios']) == 12 and report['external_calls'] == report['writes'] == 0


def test_phone_report_preserves_reviewed_voice_configuration_and_rejects_mixed_profile():
    from app.sip_lab_qwen_voice import provider_configuration
    reporter = launcher.helper(ROOT, 'sip-lab-report')
    for name in ['baseline', 'warm_female', 'clear_male']:
        provider = provider_configuration(name)
        report = reporter.make_report({'event': 'media_session_summary', 'mode': 'qwen_token_plan_voice_lab',
                                       'provider': provider, 'session_limit_seconds': 180})
        assert report['provider']['voice'] == provider['voice']
        assert report['provider'].get('rate') == provider.get('rate')
        if name != 'baseline':
            report = reporter.make_report({'event': 'media_session_summary', 'mode': 'qwen_token_plan_voice_lab',
                                           'provider': provider | {'pitch': True}, 'session_limit_seconds': 180})
            assert report['provider'] is None


@pytest.mark.parametrize('profile', ['baseline', 'warm_female', 'clear_male'])
def test_product_export_is_valid_and_matches_host_voice_catalog(lab, profile):
    from app.sip_lab_qwen_voice import provider_configuration
    config = launcher.helper(ROOT, 'sip-lab-speech-config')
    product = subprocess.run(['node', '--input-type=module', '-e',
                             "import {speechDownload} from './src/personal-speech.js';"
                             "console.log(speechDownload(process.argv[1]).content)", profile],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    exported = lab.root / 'download.json'
    exported.write_text(product.stdout)
    assert config.read(exported) == config.payload(profile)
    catalog = json.loads((ROOT / 'src/sip-lab-speech-catalog.json').read_text())[profile]
    provider = provider_configuration(profile)
    assert catalog['voice'] == provider['voice']
    assert catalog['rate'] == provider.get('rate', 1.0) and catalog['pitch'] == provider.get('pitch', 1.0)


def test_product_import_is_remembered_only_after_success_and_explicit_override_is_temporary(lab):
    config = launcher.helper(ROOT, 'sip-lab-speech-config')
    path = lab.root / 'download.json'
    path.write_text(json.dumps(config.payload('warm_female')))
    imported = launcher.Lab(lab.root, speech_config=path)
    imported.configure(exclusive=True)
    private = lab.root / 'deploy/sip-lab/generated'
    assert imported.speech_profile == 'warm_female' and not (private / config.FILENAME).exists()
    # Production invokes this only after authenticated host and media readiness.
    imported.remember_speech()
    imported.close()
    assert (private / config.FILENAME).stat().st_mode & 0o777 == 0o600
    restart = launcher.Lab(lab.root)
    restart.configure()
    assert restart.speech_profile == 'warm_female'
    override = launcher.Lab(lab.root, speech_profile='baseline')
    override.configure()
    assert override.speech_profile == 'baseline'
    assert config.read(private / config.FILENAME, private=True)['speech_profile'] == 'warm_female'


@pytest.mark.parametrize('content', ['{"speech_profile":"warm_female"}',
                                    '{"schema_version":1,"schema_version":1,"usage_scope":"personal_qwen_phone_lab","speech_profile":"warm_female"}',
                                    '{"schema_version":true,"usage_scope":"personal_qwen_phone_lab","speech_profile":"warm_female"}',
                                    '{"schema_version":1,"usage_scope":"personal_qwen_phone_lab","speech_profile":"warm_female","api_key":"synthetic-secret"}'])
def test_invalid_product_configuration_never_reaches_services_or_prints_content(lab, content, monkeypatch, capsys):
    path = lab.root / 'bad.json'
    path.write_text(content)
    monkeypatch.setattr(launcher, 'ROOT', lab.root)
    monkeypatch.setattr(launcher.sys, 'argv', ['sip-lab.py', 'start', '--speech-config', str(path)])
    monkeypatch.setattr(launcher.Lab, 'configure', lambda *a, **kw: pytest.fail('must reject before configuration'))
    assert launcher.main() == 2
    assert '产品音色配置无效' in capsys.readouterr().err
    assert not (lab.root / 'deploy/sip-lab/generated/speech-settings.json').exists()


def test_saved_product_configuration_rejects_symlinks_permissions_and_nonfiles(lab):
    config = launcher.helper(ROOT, 'sip-lab-speech-config')
    private = lab.root / 'deploy/sip-lab/generated'
    path = config.save(private, config.payload('clear_male'))
    path.chmod(0o644)
    with pytest.raises(launcher.LabError, match='权限或内容无效'):
        launcher.Lab(lab.root).configure()
    path.chmod(0o600)
    original = path.read_bytes()
    path.unlink()
    elsewhere = lab.root / 'external.json'
    elsewhere.write_bytes(original)
    elsewhere.chmod(0o600)
    path.symlink_to(elsewhere)
    with pytest.raises(launcher.LabError, match='权限或内容无效'):
        launcher.Lab(lab.root).configure()
    with pytest.raises(OSError):
        config.read(private)
    assert elsewhere.read_bytes() == original
