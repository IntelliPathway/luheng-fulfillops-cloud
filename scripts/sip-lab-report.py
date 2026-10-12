"""Allowlisted, private phone-test reports. No transcript, audio, address or business writes."""

import hashlib
import json
import math
import os
import re
import stat
import statistics
import uuid
from datetime import UTC, datetime
from pathlib import Path

CASES = ('confirm', 'numbers_date', 'negation', 'stop')
ATTESTATIONS = {'audible_reply': '全程能听清语音回复', 'numbers_date_correct': '测试数字1234及日期10月9日复述正确',
                'negation_continued': '说“不要结束测试”后对话继续',
                'barge_in_worked': '插话后旧播报停止，随后有新的回复',
                'goodbye_completed': '说“结束测试”后未挂断，再说“确认结束测试”才播放结束语并挂断'}
END_REASONS = {'duration_limit', 'turn_limit', 'dialogue_ended', 'voice_failed', 'channel_ended', 'service_stopped'}
COUNTERS = ('received_packets', 'decoded_samples', 'sent_packets', 'reply_rtp_packets', 'invalid_packets',
            'foreign_packets', 'queue_drops', 'local_turns_submitted', 'local_turns_completed', 'interruptions',
            'cloud_provider_calls')
SCOPE = 'vad_utterance_submission_to_first_reply_rtp_send_excludes_endpoint_wait_and_device_playback'
ACCEPTANCE_RULES_VERSION = 1
CHECK_LABELS = {
    'inbound_rtp_observed': '收到手机RTP',
    'confirmed_stop_observed': '相邻结束请求及确认，提示未被打断',
    'no_model_end_conflict': '没有模型结束标记冲突',
    'reply_rtp_sent': '生成回复RTP已发送',
    'four_case_multiturn_observed': '四类固定场景完成',
    'interruption_detected': '插话计数已记录',
    'playback_interruption_recovered': '播音中插话后完成下一句回复',
    'normal_dialogue_end': '确认结束后的正常终止',
    'no_voice_failure_reported': '语音服务没有失败',
    'provider_completed': '云调用完成',
    'current_capture_consistent': '本通请求及完成计数一致',
    'phone_turn_timing_available': '完成请求的首回复RTP时间完整',
    'host_stage_timing_available': '完成请求的宿主阶段时间完整',
}


class ReportError(ValueError):
    pass


def integer(value, maximum=2000000):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ReportError('invalid_phone_report_metric')
    return value


def optional_ms(value):
    return None if value is None else integer(value, 185000)


def distribution(values):
    values = sorted(value for value in values if value is not None)
    if not values:
        return {'samples': 0, 'min_ms': None, 'median_ms': None, 'p95_ms': None, 'max_ms': None}
    return {'samples': len(values), 'min_ms': values[0], 'median_ms': statistics.median(values),
            'p95_ms': values[math.ceil(len(values) * .95) - 1], 'max_ms': values[-1]}


def make_report(event, attestations=None):
    if not isinstance(event, dict) or event.get('event') != 'media_session_summary' or event.get('mode') != 'qwen_token_plan_voice_lab':
        raise ReportError('not_qwen_phone_summary')
    counters = {key: integer(event[key]) if key in event else None for key in COUNTERS}
    for key, maximum in (('local_turns_submitted', 8), ('local_turns_completed', 8), ('cloud_provider_calls', 24)):
        if counters[key] is not None:
            integer(counters[key], maximum)
    reason = event.get('end_reason')
    if not isinstance(reason, str) or reason not in END_REASONS:
        reason = None
    limit = event.get('session_limit_seconds')
    if type(limit) is not int or limit not in {60, 180}:
        limit = None
    observations = event.get('turn_observations', [])
    metrics = event.get('turn_metrics', [])
    if not isinstance(observations, list) or len(observations) > 8 or not isinstance(metrics, list) or len(metrics) > 8:
        raise ReportError('invalid_phone_report_turns')
    turns, seen = [], set()
    for row in observations:
        if not isinstance(row, dict):
            raise ReportError('invalid_phone_report_turns')
        number = integer(row.get('request_number'), 8)
        if number < 1 or number in seen or row.get('kind') not in {'turn', 'greeting', 'probe'}:
            raise ReportError('invalid_phone_report_turns')
        seen.add(number)
        if any(type(row.get(key)) is not bool for key in ('completed', 'interrupted')):
            raise ReportError('invalid_phone_report_turns')
        case = row.get('test_case')
        if case is not None and (not isinstance(case, str) or case not in {*CASES, 'greeting', 'stop_pending', 'out_of_scope', 'loan_dialogue'}):
            raise ReportError('invalid_phone_report_case')
        end = row.get('end')
        if end is not None and type(end) is not bool:
            raise ReportError('invalid_phone_report_case')
        turns.append({'request_number': number, 'kind': row['kind'], 'completed': row['completed'],
                      'interrupted': row['interrupted'], 'test_case': case, 'end': end,
                      'first_audio_received_ms': optional_ms(row.get('first_audio_received_ms')),
                      'first_reply_rtp_ms': optional_ms(row.get('first_reply_rtp_ms'))})
        control = row.get('stop_control')
        if control is not None and (not isinstance(control, str) or control not in {'none', 'requested', 'confirmed', 'cancelled'}):
            raise ReportError('invalid_phone_report_case')
        audio = row.get('input_audio_ms')
        ignored = row.get('llm_end_ignored')
        if ignored is not None and type(ignored) is not bool:
            raise ReportError('invalid_phone_report_case')
        turns[-1].update(stop_control=control, input_audio_ms=None if audio is None else integer(audio, 6000),
                         llm_end_ignored=ignored)
    safe_metrics, measured_numbers = [], set()
    for row in metrics:
        if not isinstance(row, dict):
            raise ReportError('invalid_phone_report_turns')
        # Older clients have no request numbers; do not invent timing/case associations.
        if 'request_number' not in row:
            continue
        number = integer(row['request_number'], 8)
        if number not in seen or number in measured_numbers:
            raise ReportError('invalid_phone_report_turns')
        measured_numbers.add(number)
        stages = row.get('stage_ms', {})
        if not isinstance(stages, dict) or set(stages) - {'source_tts', 'asr', 'llm', 'reply_tts'}:
            raise ReportError('invalid_phone_report_timing')
        safe_metrics.append({'request_number': number, 'host_elapsed_ms': optional_ms(row.get('elapsed_ms')),
                             'host_first_audio_ms': optional_ms(row.get('first_audio_ms')),
                             'stage_ms': {key: optional_ms(value) for key, value in stages.items()}})
    attestations = {} if attestations is None else attestations
    if (not isinstance(attestations, dict) or set(attestations) - set(ATTESTATIONS)
            or any(type(value) is not bool and value is not None for value in attestations.values())):
        raise ReportError('invalid_operator_attestation')
    human = {key: ('operator_reported_pass' if attestations.get(key) is True else
                   'operator_reported_fail' if attestations.get(key) is False else 'not_reviewed') for key in ATTESTATIONS}
    turns.sort(key=lambda row: row['request_number'])
    by_number = {row['request_number']: row for row in turns}
    actual_turns = [row for row in turns if row['kind'] == 'turn' and row['completed']]
    covered = {case: any(row['test_case'] == case and row['end'] is (case == 'stop') for row in actual_turns) for case in CASES}
    rtp_values = [row['first_reply_rtp_ms'] for row in actual_turns]
    phone_numbers = {row['request_number']: row for row in actual_turns}
    phone_metrics = [row for row in safe_metrics if row['request_number'] in phone_numbers]
    metrics_by_number = {row['request_number']: row for row in phone_metrics}

    def prompt_precedes(row):
        prior = by_number.get(row['request_number'] - 1)
        return (prior is not None and prior['kind'] == 'turn' and prior['completed'] and not prior['interrupted']
                and prior['test_case'] == 'stop_pending' and prior['end'] is False
                and prior['stop_control'] == 'requested' and prior['first_reply_rtp_ms'] is not None)

    def playback_recovered(row):
        following = by_number.get(row['request_number'] + 1)
        return (row['kind'] == 'turn' and row['interrupted'] and row['end'] is not True
                and row['first_audio_received_ms'] is not None and row['first_reply_rtp_ms'] is not None
                and row['first_audio_received_ms'] <= row['first_reply_rtp_ms']
                and following is not None and following['kind'] == 'turn' and following['completed']
                and not following['interrupted'] and following['end'] is False
                and following['test_case'] in {'confirm', 'numbers_date', 'negation'}
                and following['first_reply_rtp_ms'] is not None)

    def host_timing_complete(row):
        metric = metrics_by_number.get(row['request_number'])
        if metric is None or metric['host_elapsed_ms'] is None or metric['host_first_audio_ms'] is None:
            return False
        required = {'asr', 'reply_tts'}
        if row['test_case'] in {'confirm', 'numbers_date'}:
            required.add('llm')
        return (all(metric['stage_ms'].get(stage) is not None for stage in required)
                and metric['host_first_audio_ms'] <= metric['host_elapsed_ms'])

    stage_distributions = {stage: distribution([row['stage_ms'].get(stage) for row in phone_metrics
                                               if stage != 'llm' or phone_numbers[row['request_number']]['test_case']
                                               not in {'stop', 'stop_pending', 'negation'}])
                           for stage in ('asr', 'llm', 'reply_tts')}
    media_checks = {'inbound_rtp_observed': (counters['received_packets'] or 0) > 0,
                    'confirmed_stop_observed': event.get('stop_confirmation_required') is True and any(
                        row['test_case'] == 'stop' and row['end'] is True and row['stop_control'] == 'confirmed'
                        and not row['interrupted'] and prompt_precedes(row)
                        for row in actual_turns),
                    'no_model_end_conflict': bool(actual_turns) and all(row['llm_end_ignored'] is False for row in actual_turns),
                    'reply_rtp_sent': (counters['reply_rtp_packets'] or 0) > 0,
                    'four_case_multiturn_observed': all(covered.values()),
                    'interruption_detected': (counters['interruptions'] or 0) > 0,
                    'playback_interruption_recovered': any(playback_recovered(row) for row in turns),
                    'normal_dialogue_end': reason == 'dialogue_ended',
                    'no_voice_failure_reported': event.get('local_voice_failed') is False and event.get('voice_session_failed') is False,
                    'provider_completed': event.get('provider_request_state') == 'completed' and event.get('provider_error') is None
                                          and (counters['cloud_provider_calls'] or 0) > 0,
                    'current_capture_consistent': type(event.get('voice_test_metadata_version')) is int
                                                  and event['voice_test_metadata_version'] == 1
                                                  and [row['request_number'] for row in turns] == list(range(1, len(turns) + 1))
                                                  and counters['local_turns_submitted'] == len(turns)
                                                  and counters['local_turns_completed'] == sum(row['completed'] for row in turns),
                    'phone_turn_timing_available': bool(actual_turns) and all(
                        row['first_audio_received_ms'] is not None and row['first_reply_rtp_ms'] is not None
                        and row['first_audio_received_ms'] <= row['first_reply_rtp_ms'] for row in actual_turns),
                    'host_stage_timing_available': bool(actual_turns) and all(host_timing_complete(row) for row in actual_turns)}
    if ('operator_reported_fail' in human.values() or reason == 'voice_failed'
            or event.get('local_voice_failed') is True or event.get('voice_session_failed') is True
            or event.get('provider_error') is not None):
        assessment = 'issues_found'
    elif not all(media_checks.values()):
        assessment = 'needs_retest'
    elif all(value == 'operator_reported_pass' for value in human.values()):
        assessment = 'passed_with_operator_attestation'
    else:
        assessment = 'needs_operator_review'
    provider = event.get('provider')
    safe_provider = None
    if isinstance(provider, dict) and provider.get('kind') == 'qwen_token_plan':
        reviewed = {'asr': 'qwen-audio-3.0-asr-flash', 'llm': 'qwen3.8-flash', 'tts': 'qwen-audio-3.0-tts-plus'}
        profiles = {'baseline': ('longanhuan_v3.6', None),
                    'warm_female': ('longanlingxin', 0.96), 'clear_male': ('longanlufeng', 1.0)}
        name = provider.get('speech_profile', 'baseline')
        if isinstance(name, str) and name in profiles:
            voice, rate = profiles[name]
            expected = {'kind': 'qwen_token_plan', 'models': reviewed, 'voice': voice,
                        'sample_rate': 8000, 'protocol_version': 1}
            if name != 'baseline':
                expected.update(speech_profile=name, rate=rate, pitch=1.0)
            types_ok = type(provider.get('sample_rate')) is int and type(provider.get('protocol_version')) is int
            if name != 'baseline':
                types_ok = types_ok and type(provider.get('rate')) is float and type(provider.get('pitch')) is float
            if provider == expected and types_ok:
                safe_provider = {k: v for k, v in expected.items() if k != 'protocol_version'}
    if safe_provider is None:
        assessment = 'needs_retest' if assessment != 'issues_found' else assessment
    return {'schema_version': 2, 'acceptance_rules_version': ACCEPTANCE_RULES_VERSION,
            'mode': 'personal_qwen_phone_verification', 'report_id': uuid.uuid4().hex,
            'created_at': datetime.now(UTC).isoformat(), 'source': 'current_launcher_media_session_summary',
            'assessment': assessment, 'provider': safe_provider, 'session_limit_seconds': limit,
            'end_reason': reason, 'counters': counters, 'observed_cases': covered, 'measured_media_checks': media_checks,
            'operator_attestations': human, 'turns': turns, 'host_timings': safe_metrics,
            'latency': {'scope': SCOPE, 'first_reply_rtp': distribution(rtp_values),
                        'host_stage_ms': stage_distributions, 'host_completed_response': distribution(
                            [row['host_elapsed_ms'] for row in phone_metrics]),
                        'completed_phone_turns_only': True, 'incomplete_phone_turns': sum(
                            row['kind'] == 'turn' and not row['completed'] for row in turns),
                        'excludes': ['vad_endpoint_wait', 'phone_network_after_media_send', 'device_playback',
                                     'human_hearing', 'weight_loading']},
            'phone_audio_verified': False, 'business_ready': False, 'billing_verified': False, 'pstn_enabled': False}


def encode(report):
    return json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def private_directory(path, *, create=False):
    path = Path(path)
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink() or not path.is_dir() or stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise ReportError('invalid_private_report_directory')
    return path


def save_report(generated, report):
    private_directory(generated)
    directory = private_directory(Path(generated) / 'phone-reports', create=True)
    if len(list(directory.iterdir())) >= 200:
        raise ReportError('phone_report_storage_budget_exhausted')
    if not re.fullmatch(r'[a-f0-9]{32}', report.get('report_id', '')):
        raise ReportError('invalid_phone_report_id')
    safe = dict(report, report_digest=hashlib.sha256(encode(report).encode()).hexdigest())
    text = encode(safe)
    if len(text.encode()) > 48000:
        raise ReportError('phone_report_size_exceeded')
    destination = directory / (report['report_id'] + '.json')
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            output.write(text + '\n')
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination


def latest_report(generated):
    private_directory(generated)
    directory = private_directory(Path(generated) / 'phone-reports')
    names = list(directory.iterdir())
    if len(names) > 200:
        raise ReportError('phone_report_storage_budget_exhausted')
    files = [path for path in names if re.fullmatch(r'[a-f0-9]{32}\.json', path.name)]
    if not files:
        raise ReportError('phone_report_missing_run_verify')
    path = max(files, key=lambda item: item.lstat().st_mtime_ns)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'r', encoding='utf-8') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 48000:
            raise ReportError('invalid_private_phone_report')
        report = json.loads(source.read(48001))
    if not isinstance(report, dict) or 'report_digest' not in report:
        raise ReportError('invalid_private_phone_report')
    claimed = report.pop('report_digest')
    actual = hashlib.sha256(encode(report).encode()).hexdigest()
    if claimed != actual:
        raise ReportError('phone_report_digest_mismatch')
    if (type(report.get('schema_version')) is not int or report['schema_version'] != 2
            or report.get('mode') != 'personal_qwen_phone_verification' or report.get('report_id') != path.stem
            or not isinstance(report.get('assessment'), str) or report['assessment'] not in {
                'needs_retest', 'needs_operator_review', 'issues_found', 'passed_with_operator_attestation'}
            or any(report.get(key) is not False for key in ('phone_audio_verified', 'business_ready', 'billing_verified', 'pstn_enabled'))):
        raise ReportError('invalid_private_phone_report')
    if (type(report.get('acceptance_rules_version')) is not int
            or report['acceptance_rules_version'] != ACCEPTANCE_RULES_VERSION):
        raise ReportError('phone_report_legacy_acceptance_rules')
    checks = report.get('measured_media_checks')
    if (not isinstance(checks, dict) or set(checks) != set(CHECK_LABELS)
            or any(type(value) is not bool for value in checks.values())
            or report['assessment'] in {'needs_operator_review', 'passed_with_operator_attestation'}
            and not all(checks.values())):
        raise ReportError('invalid_private_phone_report')
    latency = report.get('latency')
    coverage, turns = report.get('observed_cases'), report.get('turns')
    if (not isinstance(coverage, dict) or set(coverage) != set(CASES)
            or any(type(value) is not bool for value in coverage.values())
            or not isinstance(turns, list) or len(turns) > 8):
        raise ReportError('invalid_private_phone_report')
    for row in turns:
        if (not isinstance(row, dict) or not isinstance(row.get('kind'), str)
                or row['kind'] not in {'turn', 'greeting', 'probe'}
                or type(row.get('completed')) is not bool
                or row.get('test_case') is not None and (not isinstance(row['test_case'], str)
                                                       or row['test_case'] not in {*CASES, 'greeting', 'stop_pending', 'out_of_scope', 'loan_dialogue'})):
            raise ReportError('invalid_private_phone_report')
    if not isinstance(latency, dict) or not isinstance(latency.get('host_stage_ms'), dict):
        raise ReportError('invalid_private_phone_report')
    for distribution_data in [latency.get('first_reply_rtp'), *latency['host_stage_ms'].values()]:
        if not isinstance(distribution_data, dict):
            raise ReportError('invalid_private_phone_report')
        integer(distribution_data.get('samples'), 8)
        for key in ('min_ms', 'median_ms', 'p95_ms', 'max_ms'):
            value = distribution_data.get(key)
            if value is not None and (type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 185000):
                raise ReportError('invalid_private_phone_report')
    report['report_digest'] = actual
    return report


def describe(report, *, synthetic_dialogue=False):
    synthetic_dialogue = synthetic_dialogue or any(
        isinstance(row, dict) and row.get('test_case') == 'loan_dialogue'
        for row in report.get('turns', []))
    provider = report.get("provider") or {}
    name = provider.get("speech_profile", "baseline") if isinstance(provider, dict) else None
    labels = {"baseline": "原音色", "warm_female": "温暖女声", "clear_male": "清晰男声"}
    label = labels.get(name, "未确认") if isinstance(name, str) else "未确认"
    print("语音档：" + label)
    states = {'issues_found': '发现问题', 'needs_retest': '场景或测量不足，请重测',
              'needs_operator_review': '程序检查完整，待听音确认',
              'passed_with_operator_attestation': '电话测试通过（含操作者听音确认）'}
    assessment = states.get(report.get('assessment'), '报告状态未知')
    timing = report.get('latency', {}).get('first_reply_rtp', {})
    median, p95 = timing.get('median_ms'), timing.get('p95_ms')
    # Only bounded scalars enter terminal output, even when a local report was tampered with.
    def show(value):
        return str(value) if type(value) in {int, float} and 0 <= value <= 185000 else '未测得'

    def count(value):
        return str(value) if type(value) is int and 0 <= value <= 8 else '未测得'
    if synthetic_dialogue:
        print('合成案件电话测量：' + ('语音服务失败' if report.get('end_reason') == 'voice_failed' else '已保存，待核对实际听音及草案'), flush=True)
    else:
        print('电话验收：' + assessment, flush=True)
    checks = report.get('measured_media_checks', {})
    if isinstance(checks, dict):
        missing = [label for key, label in CHECK_LABELS.items() if checks.get(key) is not True
                   and not (synthetic_dialogue and key == 'four_case_multiturn_observed')]
        if missing:
            print('未通过的程序检查：' + '；'.join(missing) + '。', flush=True)
    human = report.get('operator_attestations', {})
    if isinstance(human, dict):
        failed = [label for key, label in ATTESTATIONS.items() if human.get(key) == 'operator_reported_fail']
        if failed:
            print('操作者报告的问题：' + '；'.join(failed) + '。', flush=True)
    coverage = report.get('observed_cases', {})
    if synthetic_dialogue:
        completed = sum(row.get('completed') is True and row.get('test_case') == 'loan_dialogue'
                        for row in report.get('turns', []) if isinstance(row, dict))
        print('合成对话完成请求：' + count(completed) + '；草案确认需另行导出核对，本报告不证明承诺成立。', flush=True)
    else:
        print('完成场景：' + '，'.join(label + ('已完成' if coverage.get(case) is True else '缺失')
                                   for case, label in (('confirm', '确认'), ('numbers_date', '数字日期'),
                                                       ('negation', '否定结束'), ('stop', '正常结束'))), flush=True)
    unmatched = sum(row.get('kind') == 'turn' and row.get('completed') is True
                    and row.get('test_case') == 'out_of_scope'
                    for row in report.get('turns', []) if isinstance(row, dict))
    if unmatched:
        print('存在未匹配控制指令；请按合成案件提示重测，单句控制在6秒内。' if synthetic_dialogue
              else '存在未匹配短句；请用“复述1234和10月9日”重测，单句控制在6秒内。', flush=True)
    print('句末提交→首个回复RTP：中位数 ' + show(median) + ' ms，P95 ' + show(p95) + ' ms。', flush=True)
    print('首回复RTP样本：' + count(timing.get('samples')) + '；未完成电话请求：'
          + count(report.get('latency', {}).get('incomplete_phone_turns')) + '。', flush=True)
    stages = report.get('latency', {}).get('host_stage_ms', {})
    print('宿主阶段中位数：ASR ' + show(stages.get('asr', {}).get('median_ms')) + ' ms，LLM '
          + show(stages.get('llm', {}).get('median_ms')) + ' ms，TTS '
          + show(stages.get('reply_tts', {}).get('median_ms')) + ' ms。', flush=True)
    available = [(label, stages.get(stage, {}).get('median_ms'))
                 for stage, label in (('asr', 'ASR'), ('llm', 'LLM'), ('reply_tts', 'TTS'))]
    available = [(label, value) for label, value in available
                 if type(value) in {int, float} and math.isfinite(value) and 0 <= value <= 185000]
    if available:
        longest = max(value for _, value in available)
        print('已测宿主阶段中位数最高：' + '、'.join(label for label, value in available if value == longest)
              + '；TTS为整段生成耗时，不能据此认定首音频瓶颈。', flush=True)
    print('该时间不包含VAD句末等待和手机播放；听音结论为操作者反馈，业务就绪仍为否。', flush=True)
