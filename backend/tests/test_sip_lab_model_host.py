import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.sip_lab_local_voice import create_app as native_app
from app.sip_lab_model_host import ModelHost, create_app
from app.sip_lab_voice import VoiceError
from app.sip_lab_voice_config import PROFILES, VoiceSelection, service_configuration

TOKEN = 'a' * 64
AUTH = {'Authorization': 'Bearer ' + TOKEN}


def configuration(selection):
    return service_configuration(selection, {kind: {'revision': 'b' * 40} for kind in ('asr', 'llm', 'tts')})


def response(data, code=200):
    return SimpleNamespace(status_code=code, json=lambda: data)


def test_native_status_and_drain_auth_and_active_call_gate():
    engine = SimpleNamespace(warmed=True, configuration=configuration(VoiceSelection()))
    with TestClient(native_app(engine, TOKEN)) as client:
        assert client.get('/lab/status').status_code == 403
        assert client.post('/lab/drain', headers={**AUTH, 'Origin': 'http://localhost'}).status_code == 403
        with client.websocket_connect('/lab/voice', headers=AUTH):
            assert client.get('/lab/status', headers=AUTH).json()['busy']
            assert client.post('/lab/drain', headers=AUTH).status_code == 409
        result = client.post('/lab/drain', headers=AUTH)
        assert result.status_code == 200
        assert client.get('/lab/status', headers=AUTH).json()['draining']
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/lab/voice', headers=AUTH):
                pass


def test_host_auth_bounds_and_error_redaction():
    class Manager:
        def check(self, selection):
            return {'prepared': True, 'configuration': configuration(selection)}
        def probe(self, selection, samples, expected_revisions=None):
            raise RuntimeError('private model path and secret ' + TOKEN)
    with TestClient(create_app(Manager(), TOKEN)) as client:
        payload = {'selection': VoiceSelection().payload(), 'samples': 3}
        assert client.post('/lab/check', json=payload).status_code == 403
        assert client.post('/lab/check', headers={**AUTH, 'Origin': 'http://localhost'}, json=payload).status_code == 403
        assert client.post('/lab/check', headers=AUTH, json=payload).json()['prepared']
        assert client.post('/lab/probe', headers=AUTH, json={**payload, 'samples': 4}).status_code == 422
        result = client.post('/lab/probe', headers=AUTH, json=payload)
        assert result.status_code == 503
        assert TOKEN not in result.text and 'private model' not in result.text
        invalid = {**payload, 'selection': {**payload['selection'], 'url': 'http://outside'}}
        assert client.post('/lab/check', headers=AUTH, json=invalid).status_code == 409


def test_busy_phone_or_failed_drain_never_terminates_previous_process(monkeypatch):
    manager = ModelHost(TOKEN)
    selected = PROFILES['asr-fast']
    monkeypatch.setattr(manager, 'check', lambda selection: {'configuration': configuration(selection)})
    child = SimpleNamespace(poll=lambda: None, terminate=lambda: pytest.fail('active call must not be stopped'))
    manager.child = child
    for busy, drain_status in ((True, 200), (False, 409)):
        manager.native = lambda method, path, busy=busy, drain_status=drain_status: response({'busy': busy, 'configuration': configuration(VoiceSelection())},
                                                       drain_status if path == '/lab/drain' else 200)
        with pytest.raises(VoiceError, match='local_voice_busy'):
            manager.activate(selected)
    manager.child = None
    manager.close()


def test_process_exit_precedes_new_selection_launch(monkeypatch):
    manager = ModelHost(TOKEN)
    selected = PROFILES['asr-fast']
    expected = configuration(selected)
    events = []
    monkeypatch.setattr(manager, 'check', lambda selection: {'configuration': expected})
    old = SimpleNamespace(poll=lambda: None, terminate=lambda: events.append('terminate'),
                          wait=lambda timeout: events.append('old-exited'))
    manager.child = old
    def native(method, path):
        if events and events[-1] == 'old-exited':
            raise httpx.ConnectError('not listening')
        if 'launch' in events:
            return response({'busy': False, 'warmed': True, 'configuration': expected})
        events.append('drain' if path == '/lab/drain' else 'status')
        return response({'busy': False, 'configuration': configuration(VoiceSelection()), 'draining': True})
    manager.native = native
    def launch(args, **kwargs):
        assert events[-1] == 'old-exited'
        assert args[-2:] == ['serve', '--acknowledged']
        assert 'SIP_LAB_VOICE_PROFILE' not in kwargs['env']
        events.append('launch')
        return SimpleNamespace(poll=lambda: None, terminate=lambda: None, wait=lambda timeout: None)
    monkeypatch.setattr('app.sip_lab_model_host.subprocess.Popen', launch)
    assert manager.activate(selected) == expected
    assert events.index('drain') < events.index('terminate') < events.index('old-exited') < events.index('launch')
    manager.close()


def test_unmanaged_server_is_never_taken_over(monkeypatch):
    manager = ModelHost(TOKEN)
    monkeypatch.setattr(manager, 'check', lambda selection: {'configuration': configuration(selection)})
    monkeypatch.setattr(manager, 'native', lambda *args: response({}, 403))
    monkeypatch.setattr('app.sip_lab_model_host.subprocess.Popen', lambda *args, **kwargs: pytest.fail('must not launch'))
    with pytest.raises(VoiceError, match='unmanaged_model_service_running'):
        manager.activate(VoiceSelection())
    manager.close()


def test_manager_single_probe_gate_is_held_until_inference_exits(monkeypatch):
    manager = ModelHost(TOKEN)
    entered, leave = threading.Event(), threading.Event()
    config = configuration(VoiceSelection())
    monkeypatch.setattr(manager, 'activate', lambda selection: config)
    def inference(*args):
        entered.set()
        assert leave.wait(3)
        return {'configuration': config}
    monkeypatch.setattr('app.sip_lab_model_host.probe_summary', inference)
    outcomes = []
    thread = threading.Thread(target=lambda: outcomes.append(manager.probe(VoiceSelection(), 1)))
    thread.start()
    assert entered.wait(2)
    with pytest.raises(VoiceError, match='model_host_busy'):
        manager.probe(PROFILES['asr-fast'], 1)
    leave.set()
    thread.join(3)
    assert not thread.is_alive() and outcomes[0]['source'] == 'local_model_host'
    assert not manager.gate.locked()
    manager.close()


def test_changed_pinned_manifest_is_rejected_before_old_process_stops(monkeypatch):
    manager = ModelHost(TOKEN)
    monkeypatch.setattr(manager, 'check', lambda selection: {'configuration': configuration(selection)})
    monkeypatch.setattr(manager, 'activate', lambda selection: pytest.fail('must reject before switching'))
    with pytest.raises(VoiceError, match='local_voice_revision_mismatch'):
        manager.probe(VoiceSelection(), 1, {kind: 'c' * 40 for kind in ('asr', 'llm', 'tts')})
    assert not manager.gate.locked()
    manager.close()


@pytest.mark.parametrize(('busy', 'warmed', 'draining', 'state'), [
    (False, True, False, 'ready'), (True, True, False, 'phone_or_model_busy'),
    (False, False, False, 'loading'), (False, True, True, 'draining'),
])
def test_readonly_status_never_interrupts_owned_process(busy, warmed, draining, state):
    manager = ModelHost(TOKEN)
    manager.child = SimpleNamespace(poll=lambda: None, terminate=lambda: pytest.fail('status must not stop a process'))
    manager.configuration = configuration(VoiceSelection())
    calls = []
    def native(method, path):
        calls.append((method, path))
        return httpx.Response(200, json={'busy': busy, 'warmed': warmed, 'draining': draining,
                                       'configuration': manager.configuration, 'business_ready': False})
    manager.native = native
    with TestClient(create_app(manager, 'c' * 64)) as client:
        assert client.get('/lab/status').status_code == 403
        assert client.get('/lab/status', headers={'Authorization': 'Bearer ' + 'c' * 64,
                                                 'Origin': 'http://localhost'}).status_code == 403
        result = client.get('/lab/status', headers={'Authorization': 'Bearer ' + 'c' * 64})
        assert result.json()['state'] == state and result.json()['business_ready'] is False
        assert result.json()['phone_audio_verified'] is False and TOKEN not in result.text
    assert calls == [('GET', '/lab/status')]
    manager.child = None
    manager.close()


def test_status_distinguishes_unmanaged_idle_loading_and_inference_gate():
    manager = ModelHost(TOKEN)
    manager.native = lambda *args: httpx.Response(403, text=TOKEN)
    assert manager.status()['state'] == 'unmanaged'
    def unavailable(*args):
        raise httpx.ConnectError(TOKEN)
    manager.native = unavailable
    assert manager.status()['state'] == 'idle'
    manager.gate.acquire()
    assert manager.status()['state'] == 'host_busy' and manager.gate.locked()
    manager.gate.release()
    manager.child = SimpleNamespace(poll=lambda: None)
    assert manager.status()['state'] == 'loading'
    manager.child = None
    manager.close()


@pytest.mark.parametrize('change', [lambda data: data.update(busy='false'),
                                    lambda data: data.update(configuration={'secret': TOKEN}),
                                    lambda data: data.update(warmed=1)])
def test_status_rejects_invalid_native_evidence(change):
    manager = ModelHost(TOKEN)
    manager.child = SimpleNamespace(poll=lambda: None)
    data = {'busy': False, 'warmed': True, 'draining': False, 'configuration': configuration(VoiceSelection())}
    change(data)
    manager.native = lambda *args: httpx.Response(200, json=data)
    assert manager.status()['state'] == 'invalid_response'
    manager.child = None
    manager.close()


def test_doctor_uses_only_installed_metadata_and_cached_manifest(monkeypatch):
    import app.sip_lab_model_host as module
    monkeypatch.setattr(module, 'mac_only', lambda: None)
    monkeypatch.setattr(module.sys, 'version_info', (3, 12))
    monkeypatch.setattr('app.sip_lab.LabConfig.from_environment', lambda: None)
    monkeypatch.setattr(module, 'local_token', lambda: TOKEN)
    monkeypatch.setenv('ENABLE_SIP_LAB_LOCAL_VOICE', 'true')
    monkeypatch.setenv('SIP_LAB_MODEL_HOST_ENABLED', 'true')
    monkeypatch.setenv('SIP_LAB_MODEL_HOST_TOKEN', 'c' * 64)
    calls = []
    monkeypatch.setattr(module.importlib.metadata, 'version', lambda package: calls.append(package) or 'installed')
    monkeypatch.setattr(module, 'load_manifest', lambda root, selection: calls.append(selection) or {})
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('doctor must not load models'))
    monkeypatch.setattr(module.httpx, 'Client', lambda **kwargs: pytest.fail('doctor must not use network'))
    result = module.doctor('asr-fast')
    assert result['ready_to_start'] and not result['phone_audio_verified']
    assert calls == ['mlx-audio', 'mlx-lm', PROFILES['asr-fast']]
    def missing(*args):
        raise RuntimeError('secret directory ' + TOKEN)
    monkeypatch.setattr(module, 'load_manifest', missing)
    result = module.doctor()
    assert not result['ready_to_start'] and result['checks']['cached_model_manifest'] == 'not_ready'
    assert TOKEN not in str(result) and 'secret directory' not in str(result)
