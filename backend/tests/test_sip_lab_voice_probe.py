import json
from collections import deque
from contextlib import contextmanager

import httpx
import pytest

from app.sip_lab_voice_probe import ASR, CHAT, LLM, PHRASE, TTS, ProbeError, VoiceProbe, main

KEY = 'SYNTHETIC_CLOUD_KEY_' + 'x' * 32


class SpeechSocket:
    def __init__(self, results):
        self.results = deque(results)
        self.sent = []
        self.task = None

    def send(self, value):
        self.sent.append(value)
        if isinstance(value, str):
            command = json.loads(value)
            if command['header']['action'] == 'run-task':
                self.task = command['header']['task_id']

    def recv(self, timeout):
        assert 0 < timeout <= 15
        value = self.results.popleft()
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return json.dumps({'header': {'task_id': self.task, 'event': value}, 'payload': {}})
        value.setdefault('header', {'task_id': self.task, 'event': 'result-generated'})
        return json.dumps(value)


def sentence(text=PHRASE + '。', **fields):
    result = {'sentence_id': 1, 'sentence_end': True, 'text': text}
    result.update(fields)
    return {'payload': {'output': {'sentence': result}}}


def make_probe(sockets, handler=None, clock=None):
    pending = deque(sockets)

    @contextmanager
    def factory(key, timeout):
        assert key == KEY and 0 < timeout <= 60
        yield pending.popleft()

    def default_handler(request):
        assert str(request.url) == CHAT and request.method == 'POST'
        assert request.headers['Authorization'] == 'Bearer ' + KEY
        payload = json.loads(request.content)
        assert payload['model'] == LLM and payload['enable_thinking'] is False
        assert payload['response_format'] == {'type': 'json_object'}
        assert payload['messages'][1]['content'] == PHRASE + '。'
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps({'reply': '语音测试完成。', 'end': True}, ensure_ascii=False)}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler or default_handler))
    options = {'clock': clock} if clock else {}
    return VoiceProbe(KEY, ws_factory=factory, client=client, sleep=lambda _: None, **options)


def test_synthetic_cloud_chain_protocol_and_redacted_result():
    source = SpeechSocket(['task-started', b'\0' * 3200, 'task-finished'])
    recognition = SpeechSocket(['task-started', sentence('错误中间稿', sentence_end=False),
                                sentence('', heartbeat=True, sentence_id=0), sentence(), 'task-finished'])
    reply = SpeechSocket(['task-started', b'\0' * 1600, 'task-finished'])
    result = make_probe([source, recognition, reply]).run()
    assert result['service_chain_completed'] and result['asr_phrase_matched']
    assert result['models'] == {'asr': ASR, 'llm': LLM, 'tts': TTS}
    assert result['tts_samples'] == 800 and not result['phone_audio_verified'] and not result['business_ready']
    assert [item['stage'] for item in result['stages']] == ['tts', 'asr', 'llm', 'tts']
    assert KEY not in json.dumps(result) and PHRASE not in json.dumps(result, ensure_ascii=False)
    for ws, task, function in [(source, 'tts', 'SpeechSynthesizer'), (recognition, 'asr', 'recognition')]:
        commands = [json.loads(value) for value in ws.sent if isinstance(value, str)]
        assert commands[0]['payload']['task'] == task and commands[0]['payload']['function'] == function
        assert commands[0]['payload']['parameters']['sample_rate'] == 8000
        assert commands[0]['payload']['parameters']['format'] == 'pcm'
        assert commands[-1]['header']['action'] == 'finish-task'
        assert len({item['header']['task_id'] for item in commands}) == 1
    assert [len(value) for value in recognition.sent if isinstance(value, bytes)] == [1600, 1600]


@pytest.mark.parametrize('results,reason', [
    (['task-started', b'\0' * 160002, 'task-finished'], 'tts_audio_budget_exceeded'),
    (['task-started', b'x', 'task-finished'], 'tts_incomplete_audio'),
    (['task-started', 'task-finished'], 'tts_incomplete_audio'),
    (['task-failed'], 'speech_task_failed_or_mismatched'),
    (['result-generated'], 'speech_task_not_started'),
    ([{'header': {'task_id': 'other', 'event': 'task-started'}}], 'speech_task_failed_or_mismatched'),
])
def test_tts_rejects_failed_foreign_or_unbounded_output(results, reason):
    ws = SpeechSocket(results)
    probe = make_probe([ws])
    with pytest.raises(ProbeError, match=reason):
        probe.tts(PHRASE)
    assert not probe.metrics
    assert len([value for value in ws.sent if isinstance(value, str) and 'run-task' in value]) == 1


@pytest.mark.parametrize('result,reason', [
    (sentence('其他内容'), 'asr_synthetic_phrase_mismatch'),
    (sentence(sentence_id=True), 'invalid_asr_sentence'),
    (sentence(sentence_id=17), 'invalid_asr_sentence'),
    (sentence(sentence_id=0), 'invalid_asr_sentence'),
    (sentence('中间稿', sentence_end=False), 'asr_incomplete'),
    (b'bad', 'unexpected_asr_binary'),
])
def test_asr_final_and_known_phrase_required_before_llm(result, reason):
    probe = make_probe([SpeechSocket(['task-started', result, 'task-finished'])])
    with pytest.raises(ProbeError, match=reason):
        probe.asr(b'\0' * 1600)
    assert not probe.metrics


@pytest.mark.parametrize('reply,finish,reason', [
    ({'reply': '测试', 'end': False}, 'stop', 'llm_invalid_test_reply'),
    ({'reply': '测试', 'end': True, 'tool': 'execute'}, 'stop', 'llm_invalid_test_reply'),
    ({'reply': '欠款说明', 'end': True}, 'stop', 'llm_off_scope_reply'),
    ({'reply': 'x' * 61, 'end': True}, 'stop', 'llm_invalid_test_reply'),
    ({'reply': '测试', 'end': True}, 'length', 'llm_incomplete'),
])
def test_llm_rejects_incomplete_offscope_and_invalid_structures(reply, finish, reason):
    def handler(_):
        return httpx.Response(200, json={'choices': [{'finish_reason': finish,
            'message': {'content': json.dumps(reply)}}]})

    probe = make_probe([], handler)
    with pytest.raises(ProbeError, match=reason):
        probe.llm(PHRASE)
    assert not probe.metrics


@pytest.mark.parametrize('response,reason', [
    (httpx.Response(302, headers={'Location': 'https://example.invalid'}), 'llm_request_failed'),
    (httpx.Response(200, content=b'x' * 65537), 'llm_response_budget_exceeded'),
    (httpx.Response(200, json={'choices': []}), 'llm_invalid_response'),
])
def test_llm_http_failures_and_budget(response, reason):
    calls = []
    probe = make_probe([], lambda request: calls.append(request) or response)
    with pytest.raises(ProbeError, match=reason):
        probe.llm(PHRASE)
    assert len(calls) == 1


def test_deadline_and_input_guards_do_not_open_services():
    now = [0]
    probe = make_probe([], clock=lambda: now[0])
    now[0] = 61
    with pytest.raises(ProbeError, match='probe_deadline_exceeded'):
        probe.tts(PHRASE)
    for action, value in [(probe.asr, b'x'), (probe.tts, 'x' * 61), (probe.llm, '其他内容')]:
        with pytest.raises(ProbeError):
            action(value)


@pytest.fixture
def enabled(monkeypatch):
    for name, value in {'APP_ENV': 'test', 'ENABLE_SIP_LAB': 'true', 'SIP_LAB_INSTANCE_ID': 'a' * 32,
                        'SIP_LAB_ARI_PASSWORD': 'SYNTHETIC_PASSWORD_' + 'x' * 32}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv('DASHSCOPE_API_KEY', raising=False)
    monkeypatch.delenv('ENABLE_SIP_LAB_VOICE_TEST', raising=False)


def test_cli_requires_optin_before_credential_or_network(enabled, monkeypatch, capsys):
    monkeypatch.setattr('sys.argv', ['voice-probe', '--acknowledged'])
    monkeypatch.setattr('getpass.getpass', lambda _: pytest.fail('must not request key before opt-in'))
    assert main() == 2
    assert json.loads(capsys.readouterr().out)['error'] == 'synthetic_paid_probe_not_enabled'


def test_cli_never_prints_provider_exception_or_key(enabled, monkeypatch, capsys):
    monkeypatch.setenv('ENABLE_SIP_LAB_VOICE_TEST', 'true')
    monkeypatch.setenv('DASHSCOPE_API_KEY', KEY)
    monkeypatch.setattr('sys.argv', ['voice-probe', '--acknowledged'])

    def fail(_):
        raise RuntimeError(KEY + ' provider body or request URL')

    monkeypatch.setattr(VoiceProbe, 'run', fail)
    assert main() == 2
    output = capsys.readouterr().out
    assert KEY not in output and 'provider body' not in output
    assert json.loads(output)['error'] == 'voice_probe_unavailable_or_failed'


def test_subscription_key_rejected_before_network_client_or_clock():
    def forbidden(*args, **kwargs):
        pytest.fail('subscription key must be rejected before any service activity')

    key = 'sk-sp-SYNTHETIC_ONLY_' + 'x' * 32
    with pytest.raises(ProbeError, match='^subscription_key_not_supported$'):
        VoiceProbe(key, ws_factory=forbidden, clock=forbidden)


@pytest.mark.parametrize('key_source', ['environment', 'prompt'])
def test_cli_subscription_key_never_calls_services_or_discloses_key(enabled, monkeypatch, capsys, key_source):
    key = 'sk-sp-SYNTHETIC_ONLY_' + 'x' * 32
    monkeypatch.setenv('ENABLE_SIP_LAB_VOICE_TEST', 'true')
    monkeypatch.setattr('sys.argv', ['voice-probe', '--acknowledged'])
    if key_source == 'environment':
        monkeypatch.setenv('DASHSCOPE_API_KEY', key)
        monkeypatch.setattr('getpass.getpass', lambda _: pytest.fail('must use injected credential'))
    else:
        monkeypatch.setattr('getpass.getpass', lambda _: key)
    monkeypatch.setattr(VoiceProbe, 'run', lambda _: pytest.fail('must not call service chain'))
    assert main() == 2
    output = capsys.readouterr().out
    assert key not in output
    result = json.loads(output)
    assert result['error'] == 'subscription_key_not_supported'
    assert result['completed_stages'] == [] and result['failed_stage'] is None
    assert result['service_chain_completed'] is False and result['phone_audio_verified'] is False
