import importlib.util
import json
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.sip_lab_voice import QWEN_HOST_URL, LocalVoiceClient
from app.sip_lab_voice_session import VoiceMediaSession

ROOT = Path(__file__).resolve().parents[2]


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'scripts' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


reporter = module('sip-lab-report')


def summary():
    cases = ['greeting', 'confirm', 'numbers_date', 'negation', 'confirm', 'stop_pending', 'stop']
    observations = [{'request_number': index + 1, 'kind': 'greeting' if index == 0 else 'turn', 'completed': True,
                     'interrupted': index == 3, 'test_case': case, 'end': case == 'stop',
                     'stop_control': 'confirmed' if case == 'stop' else 'requested' if case == 'stop_pending' else 'none',
                     'input_audio_ms': 1800, 'llm_end_ignored': False,
                     'first_audio_received_ms': 1000 + index * 100, 'first_reply_rtp_ms': 1020 + index * 100}
                    for index, case in enumerate(cases)]
    return {'event': 'media_session_summary', 'mode': 'qwen_token_plan_voice_lab',
            'voice_test_metadata_version': 1, 'session_limit_seconds': 180, 'end_reason': 'dialogue_ended',
            'stop_confirmation_required': True,
            'received_packets': 1200, 'decoded_samples': 192000, 'sent_packets': 1700,
            'reply_rtp_packets': 400, 'invalid_packets': 0, 'foreign_packets': 0, 'queue_drops': 0,
            'local_turns_submitted': 7, 'local_turns_completed': 7, 'interruptions': 1, 'cloud_provider_calls': 16,
            'local_voice_failed': False, 'voice_session_failed': False,
            'provider_request_state': 'completed', 'provider_error': None,
            'provider': {'kind': 'qwen_token_plan', 'models': {'asr': 'qwen-audio-3.0-asr-flash',
                         'llm': 'qwen3.8-flash', 'tts': 'qwen-audio-3.0-tts-plus'},
                         'voice': 'longanhuan_v3.6', 'sample_rate': 8000}, 'turn_observations': observations,
            'turn_metrics': [{'request_number': row['request_number'], 'elapsed_ms': 2000,
                             'first_audio_ms': 900, 'stage_ms': {'source_tts': 0, 'asr': 400,
                                                               'llm': 350, 'reply_tts': 1250}} for row in observations]}


def test_measurements_require_separate_operator_hearing_attestation():
    event = summary()
    report = reporter.make_report(event)
    assert report['assessment'] == 'needs_operator_review'
    accepted = reporter.make_report(event, {key: True for key in reporter.ATTESTATIONS})
    assert accepted['assessment'] == 'passed_with_operator_attestation'
    assert accepted['phone_audio_verified'] is False and accepted['business_ready'] is False
    assert all(value == 'operator_reported_pass' for value in accepted['operator_attestations'].values())
    assert accepted['latency']['first_reply_rtp'] == {'samples': 6, 'min_ms': 1120, 'median_ms': 1370,
                                                    'p95_ms': 1620, 'max_ms': 1620}


def test_barge_in_has_own_request_and_can_cancel_before_completion():
    event = summary()
    for rows in (event['turn_observations'], event['turn_metrics']):
        for row in rows[4:]:
            row['request_number'] += 1
    event['turn_observations'][3]['interrupted'] = False
    event['turn_observations'].insert(4, {'request_number': 5, 'kind': 'turn', 'completed': False,
                                        'interrupted': True, 'test_case': None, 'end': None,
                                        'first_audio_received_ms': 1400, 'first_reply_rtp_ms': 1420})
    event['local_turns_submitted'] = 8
    event['cloud_provider_calls'] = 18
    report = reporter.make_report(event, {key: True for key in reporter.ATTESTATIONS})
    assert report['assessment'] == 'passed_with_operator_attestation'
    assert report['latency']['incomplete_phone_turns'] == 1
    assert report['latency']['first_reply_rtp']['samples'] == 6
    assert report['observed_cases']['negation'] is True
    # Even an incomplete interrupted request needs a physically possible playback timeline.
    event['turn_observations'][4]['first_audio_received_ms'] = 1500
    rejected = reporter.make_report(event, {key: True for key in reporter.ATTESTATIONS})
    assert rejected['measured_media_checks']['playback_interruption_recovered'] is False
    assert rejected['assessment'] == 'needs_retest'


@pytest.mark.parametrize('fault', ['counter_only', 'greeting_only', 'before_playback', 'no_recovery',
                                  'recovery_unmatched', 'recovery_interrupted'])
def test_interrupt_counter_cannot_substitute_for_playback_and_recovery(fault):
    event = summary()
    interrupted, following = event['turn_observations'][3:5]
    if fault == 'counter_only':
        interrupted['interrupted'] = False
    elif fault == 'greeting_only':
        interrupted['interrupted'] = False
        event['turn_observations'][0]['interrupted'] = True
    elif fault == 'before_playback':
        interrupted['first_reply_rtp_ms'] = None
    elif fault == 'no_recovery':
        following['completed'] = False
        event['local_turns_completed'] -= 1
    elif fault == 'recovery_unmatched':
        following['test_case'] = 'out_of_scope'
    else:
        following['interrupted'] = True
    report = reporter.make_report(event, {key: True for key in reporter.ATTESTATIONS})
    assert report['measured_media_checks']['interruption_detected'] is True
    assert report['measured_media_checks']['playback_interruption_recovered'] is False
    assert report['assessment'] == 'needs_retest'


@pytest.mark.parametrize('fault', ['interrupted_prompt', 'intervening_turn', 'intervening_cancelled_turn'])
def test_confirmation_needs_immediately_preceding_uninterrupted_prompt(fault):
    event = summary()
    if fault == 'interrupted_prompt':
        event['turn_observations'][-2]['interrupted'] = True
    else:
        event['turn_observations'][-1]['request_number'] = 8
        event['turn_metrics'][-1]['request_number'] = 8
        inserted = dict(event['turn_observations'][1], request_number=7,
                        completed=fault != 'intervening_cancelled_turn',
                        interrupted=fault == 'intervening_cancelled_turn')
        event['turn_observations'].insert(-1, inserted)
        event['local_turns_submitted'] = 8
        event['local_turns_completed'] += int(inserted['completed'])
        if inserted['completed']:
            event['turn_metrics'].insert(-1, dict(event['turn_metrics'][1], request_number=7))
    report = reporter.make_report(event, {key: True for key in reporter.ATTESTATIONS})
    assert report['measured_media_checks']['confirmed_stop_observed'] is False
    assert report['assessment'] == 'needs_retest'


@pytest.mark.parametrize('fault', ['missing_metrics', 'missing_asr', 'missing_llm', 'missing_tts',
                                  'missing_elapsed', 'first_audio_after_completion', 'rtp_before_audio', 'request_gap'])
def test_incomplete_or_inconsistent_timing_cannot_pass_phone_acceptance(fault):
    event = summary()
    metric = event['turn_metrics'][1]
    if fault == 'missing_metrics':
        event['turn_metrics'] = []
    elif fault.startswith('missing_'):
        key = fault.removeprefix('missing_')
        if key == 'elapsed':
            metric.pop('elapsed_ms')
        else:
            metric['stage_ms'].pop('reply_tts' if key == 'tts' else key)
    elif fault == 'first_audio_after_completion':
        metric['first_audio_ms'] = metric['elapsed_ms'] + 1
    elif fault == 'rtp_before_audio':
        event['turn_observations'][1]['first_reply_rtp_ms'] = 1
    else:
        event['turn_observations'][-1]['request_number'] = 8
        event['turn_metrics'][-1]['request_number'] = 8
    report = reporter.make_report(event, {key: True for key in reporter.ATTESTATIONS})
    assert report['assessment'] == 'needs_retest'


def test_deterministic_stop_and_negation_do_not_require_invented_llm_timings():
    event = summary()
    for observation, metric in zip(event['turn_observations'], event['turn_metrics'], strict=True):
        if observation['test_case'] in {'negation', 'stop_pending', 'stop'}:
            metric['stage_ms'].pop('llm')
    report = reporter.make_report(event, {key: True for key in reporter.ATTESTATIONS})
    assert report['assessment'] == 'passed_with_operator_attestation'
    assert report['latency']['host_stage_ms']['llm']['samples'] == 3


def test_diagnostics_show_failed_evidence_and_stage_scope_without_untrusted_text(capsys):
    event = summary()
    event['turn_metrics'] = []
    event['secret'] = 'SYNTHETIC_DIAGNOSTIC_SECRET'
    report = reporter.make_report(event, {'barge_in_worked': False})
    report['measured_media_checks']['SYNTHETIC_DIAGNOSTIC_SECRET'] = False
    reporter.describe(report)
    output = capsys.readouterr().out
    assert '宿主阶段时间完整' in output and '操作者报告的问题' in output
    assert 'SYNTHETIC_DIAGNOSTIC_SECRET' not in output
    reporter.describe(reporter.make_report(summary()))
    output = capsys.readouterr().out
    assert '中位数最高：TTS' in output and '不能据此认定首音频瓶颈' in output
    assert '首回复RTP样本：6；未完成电话请求：0' in output


@pytest.mark.parametrize('fault', ['legacy_rules', 'unknown_rules', 'incomplete_checks', 'failed_check_as_pass'])
def test_saved_report_cannot_claim_current_acceptance_using_old_or_missing_checks(tmp_path, fault):
    generated = tmp_path / 'generated'
    generated.mkdir(mode=0o700)
    report = reporter.make_report(summary(), {key: True for key in reporter.ATTESTATIONS})
    if fault == 'legacy_rules':
        report.pop('acceptance_rules_version')
    elif fault == 'unknown_rules':
        report['acceptance_rules_version'] = True
    elif fault == 'incomplete_checks':
        report['measured_media_checks'].pop('playback_interruption_recovered')
    else:
        report['measured_media_checks']['host_stage_timing_available'] = False
    reporter.save_report(generated, report)
    with pytest.raises(reporter.ReportError):
        reporter.latest_report(generated)


def test_legacy_report_diagnosis_preserves_file_without_calls_or_upgrading_it(tmp_path, monkeypatch, capsys):
    launcher = module('sip-lab')
    generated = tmp_path / 'deploy/sip-lab/generated'
    generated.mkdir(mode=0o700, parents=True)
    report = reporter.make_report(summary(), {key: True for key in reporter.ATTESTATIONS})
    report.pop('acceptance_rules_version')
    path = reporter.save_report(generated, report)
    original = path.read_bytes()
    lab = launcher.Lab(tmp_path)
    monkeypatch.setattr(launcher, 'Lab', lambda **kwargs: lab)
    monkeypatch.setattr(launcher, 'read_environment', lambda _: {})
    monkeypatch.setattr(launcher, 'helper', lambda *args: reporter)
    monkeypatch.setattr(launcher.sys, 'argv', ['sip-lab.py', 'report'])
    monkeypatch.setattr(lab, 'run', lambda *args, **kwargs: pytest.fail('read-only report must not call services'))
    assert launcher.main() == 2
    assert '旧报告已保留' in capsys.readouterr().err
    assert path.read_bytes() == original


def test_report_explains_unmatched_second_turn_without_exposing_asr(capsys):
    event = summary()
    event['turn_observations'][2]['test_case'] = 'out_of_scope'
    event['turn_observations'][2]['transcript'] = 'SYNTHETIC_ASR_SECRET'
    report = reporter.make_report(event)
    assert report['assessment'] == 'needs_retest'
    reporter.describe(report)
    output = capsys.readouterr().out
    assert '数字日期缺失' in output and '存在未匹配短句' in output
    assert 'SYNTHETIC_ASR_SECRET' not in output + json.dumps(report)


@pytest.mark.parametrize('fault', ['legacy_stop', 'unconfirmed_stop', 'missing_prompt', 'model_end_conflict'])
def test_single_step_or_model_end_conflict_cannot_pass_new_confirmation_acceptance(fault):
    event = summary()
    if fault == 'legacy_stop':
        event.pop('stop_confirmation_required')
    elif fault == 'unconfirmed_stop':
        event['turn_observations'][-1]['stop_control'] = 'none'
    elif fault == 'missing_prompt':
        event['turn_observations'][-2]['test_case'] = 'out_of_scope'
    else:
        event['turn_observations'][1]['llm_end_ignored'] = True
    report = reporter.make_report(event, {key: True for key in reporter.ATTESTATIONS})
    assert report['assessment'] == 'needs_retest'


@pytest.mark.parametrize('field,value', [('turns', None), ('turns', [{'kind': 'turn', 'completed': True, 'test_case': []}]),
                                        ('observed_cases', {'confirm': 'SYNTHETIC_ASR_SECRET'})])
def test_signed_report_still_validates_display_fields(tmp_path, field, value):
    generated = tmp_path / 'generated'
    generated.mkdir(mode=0o700)
    report = reporter.make_report(summary())
    report[field] = value
    reporter.save_report(generated, report)
    with pytest.raises(reporter.ReportError, match='invalid_private_phone_report'):
        reporter.latest_report(generated)


@pytest.mark.parametrize('fault', ['probe', 'no_rtp', 'silence_only', 'missing_case', 'wrong_end', 'no_interrupt',
                                 'truncated', 'legacy', 'missing_timing', 'missing_provider', 'unknown_request', 'bad_counts'])
def test_partial_synthetic_or_unknown_results_cannot_pass_phone_acceptance(fault):
    event = summary()
    if fault == 'probe':
        for row in event['turn_observations']:
            row['kind'] = 'probe'
    elif fault == 'no_rtp':
        event['received_packets'] = 0
    elif fault == 'silence_only':
        event['reply_rtp_packets'] = 0
    elif fault == 'missing_case':
        event['turn_observations'][2]['test_case'] = None
    elif fault == 'wrong_end':
        event['turn_observations'][3]['end'] = True
    elif fault == 'no_interrupt':
        event['interruptions'] = 0
    elif fault == 'truncated':
        event['end_reason'] = 'duration_limit'
    elif fault == 'legacy':
        event.pop('voice_test_metadata_version')
    elif fault == 'missing_timing':
        event['turn_observations'][1]['first_reply_rtp_ms'] = None
    elif fault == 'missing_provider':
        event.pop('provider')
    elif fault == 'unknown_request':
        event['provider_request_state'] = 'unknown'
    else:
        event['local_turns_completed'] = 5
    report = reporter.make_report(event, {key: True for key in reporter.ATTESTATIONS})
    assert report['assessment'] == 'needs_retest'
    assert not report['business_ready']


def test_failed_hearing_and_provider_failure_are_visible():
    event = summary()
    answers = {key: True for key in reporter.ATTESTATIONS}
    answers['barge_in_worked'] = False
    assert reporter.make_report(event, answers)['assessment'] == 'issues_found'
    event['end_reason'] = 'voice_failed'
    assert reporter.make_report(event)['assessment'] == 'issues_found'


@pytest.mark.parametrize('bad', [True, -1, 185001, '100', 1.2])
def test_untrusted_timing_is_not_used_in_latency_statistics(bad):
    event = summary()
    event['turn_observations'][1]['first_reply_rtp_ms'] = bad
    with pytest.raises(reporter.ReportError):
        reporter.make_report(event)


def test_duplicate_or_unassociated_turns_are_rejected():
    event = summary()
    event['turn_observations'][2]['request_number'] = 1
    with pytest.raises(reporter.ReportError):
        reporter.make_report(event)
    event = summary()
    event['turn_metrics'][2]['request_number'] = 8
    with pytest.raises(reporter.ReportError):
        reporter.make_report(event)


def test_reports_are_private_exclusive_digest_checked_and_never_include_unknown_fields(tmp_path):
    generated = tmp_path / 'generated'
    generated.mkdir(mode=0o700)
    event = summary()
    for key in ('transcript', 'audio', 'ip', 'api_key', 'raw_reply'):
        event[key] = 'synthetic-secret-must-not-be-saved'
        event['turn_observations'][1][key] = 'synthetic-secret-must-not-be-saved'
    event['provider']['token'] = 'synthetic-secret-must-not-be-saved'
    report = reporter.make_report(event)
    path = reporter.save_report(generated, report)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert 'synthetic-secret' not in path.read_text()
    assert reporter.latest_report(generated)['report_id'] == report['report_id']
    with pytest.raises(FileExistsError):
        reporter.save_report(generated, report)
    data = json.loads(path.read_text())
    data['assessment'] = 'passed_with_operator_attestation'
    path.write_text(json.dumps(data))
    with pytest.raises(reporter.ReportError, match='digest_mismatch'):
        reporter.latest_report(generated)


def test_report_paths_refuse_symlinks_and_public_permissions(tmp_path):
    generated = tmp_path / 'generated'
    generated.mkdir(mode=0o700)
    outside = tmp_path / 'outside'
    outside.mkdir(mode=0o700)
    directory = generated / 'phone-reports'
    directory.symlink_to(outside)
    with pytest.raises(reporter.ReportError):
        reporter.save_report(generated, reporter.make_report(summary()))
    assert not list(outside.iterdir())
    directory.unlink()
    path = reporter.save_report(generated, reporter.make_report(summary()))
    path.chmod(0o644)
    with pytest.raises(reporter.ReportError):
        reporter.latest_report(generated)
    path.unlink()
    path.symlink_to(outside / 'unrelated')
    with pytest.raises(OSError):
        reporter.latest_report(generated)


def test_clock_measurement_stale_playback_and_interruptions_do_not_invent_latency():
    now = [10.]
    voice = LocalVoiceClient('a' * 64, url=QWEN_HOST_URL, expected_provider='qwen_token_plan', clock=lambda: now[0])
    voice.submit(b'\0' * 3200)
    with voice.lock:
        voice.audio.append(b'\0' * 320)
    now[0] = 11
    assert voice.pop() is not None
    voice.mark_rtp_sent()
    now[0] = 12
    voice.mark_rtp_sent()
    assert voice.summary()['turn_observations'][0]['first_reply_rtp_ms'] == 1000
    voice.submit(b'\0' * 3200)
    voice.mark_rtp_sent()  # Old pop cannot be associated with the newer request.
    assert voice.summary()['turn_observations'][1]['first_reply_rtp_ms'] is None
    voice.interrupt()
    assert voice.summary()['turn_observations'][1]['interrupted']
    assert not any(key in voice.summary()['turn_observations'][0] for key in ('epoch', 'started', 'turn'))
    voice.close()


def test_failed_udp_send_does_not_claim_reply_rtp_playback():
    voice = LocalVoiceClient('a' * 64)
    voice.start = lambda: voice.submit(None, kind='greeting')
    session = VoiceMediaSession(('127.0.0.1', 9999), 0, 1, 'a' * 64, client=voice)
    with voice.lock:
        voice.audio.append(b'\0' * 320)

    class FailedSocket:
        def sendto(self, *_):
            raise OSError('synthetic send failure')

    with pytest.raises(OSError):
        session.tick(FailedSocket(), 1)
    assert session.reply_rtp_packets == 0
    assert voice.summary()['turn_observations'][0]['first_reply_rtp_ms'] is None
    session.close()


@pytest.mark.parametrize('case,end', [('negation', True), ('stop', False), ('raw-transcript-secret', False)])
def test_invalid_case_or_end_metadata_is_rejected_before_completion(case, end):
    from app.sip_lab_qwen_voice import provider_configuration

    events = deque([json.dumps({'kind': 'ready', 'provider': provider_configuration()})])

    class Socket:
        def send(self, command):
            if not isinstance(command, str):
                return
            command = json.loads(command)
            events.append(json.dumps({'kind': 'complete', 'turn': command['turn'], 'end': end,
                                      'reply_digest': 'a' * 64, 'test_case': case,
                                      'elapsed_ms': 1, 'first_audio_ms': 1, 'provider': provider_configuration(),
                                      'cloud_provider_calls': 1, 'provider_request_state': 'completed',
                                      'provider_error': None, 'provider_active_stage': 'tts'}))

        def recv(self, timeout):
            if events:
                return events.popleft()
            raise TimeoutError()

    @contextmanager
    def connect(*args, **kwargs):
        yield Socket()

    voice = LocalVoiceClient('a' * 64, url=QWEN_HOST_URL, expected_provider='qwen_token_plan', connect=connect)
    voice.start()
    until = time.monotonic() + 2
    try:
        while not voice.failed and time.monotonic() < until:
            time.sleep(.005)
        assert voice.failed and voice.completed == 0
        assert 'raw-transcript-secret' not in json.dumps(voice.summary())
    finally:
        voice.close()


def test_verify_preserves_measurements_when_operator_interrupts_review(tmp_path, monkeypatch):
    launcher = module('sip-lab')
    lab = launcher.Lab(tmp_path, verifying=True)
    generated = tmp_path / 'deploy/sip-lab/generated'
    generated.mkdir(mode=0o700, parents=True)
    real_helper = launcher.helper
    monkeypatch.setattr(launcher, 'helper', lambda root, name: reporter if name == 'sip-lab-report' else real_helper(root, name))
    monkeypatch.setattr(launcher.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        lab.handle_event(summary())
    saved = reporter.latest_report(generated)
    assert saved['assessment'] == 'needs_operator_review'


def test_guided_verify_saves_measurement_and_linked_attestation_without_inference(tmp_path, monkeypatch):
    launcher = module('sip-lab')
    lab = launcher.Lab(tmp_path, verifying=True)
    generated = tmp_path / 'deploy/sip-lab/generated'
    generated.mkdir(mode=0o700, parents=True)
    monkeypatch.setattr(launcher, 'helper', lambda *args: reporter)
    monkeypatch.setattr(lab, 'run', lambda *args, **kwargs: pytest.fail('report must not call Docker/models'))
    monkeypatch.setattr(launcher.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda _: 'y')
    assert lab.handle_event(summary()) is True
    assert lab.verification_passed is True
    reports = list((generated / 'phone-reports').iterdir())
    assert len(reports) == 2
    accepted = reporter.latest_report(generated)
    assert accepted['assessment'] == 'passed_with_operator_attestation'
    assert (generated / 'phone-reports' / (accepted['measurement_report_id'] + '.json')).is_file()
