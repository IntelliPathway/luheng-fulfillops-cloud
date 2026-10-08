import copy
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain import utcnow
from app.main import create_app
from app.models import TenantMembership, VoiceBenchmark, VoiceCombination
from app.sip_lab_voice import VoiceError
from app.sip_lab_voice_config import PROFILES, VoiceSelection, service_configuration


@pytest.fixture()
def client():
    with TestClient(create_app('sqlite:///:memory:')) as value:
        yield value


def headers(tenant='TENANT_A', role='admin'):
    return {'X-Tenant-ID': tenant, 'X-Actor-ID': {'admin': 'test-user', 'operator': 'test-operator',
                                               'viewer': 'test-viewer'}[role], 'X-Role': 'admin'}


def configuration(selection):
    return service_configuration(selection, {kind: {'revision': 'b' * 40} for kind in ('asr', 'llm', 'tts')})


def measured(selection, count):
    config = configuration(selection)
    return {'configuration': config, 'source': 'local_model_host', 'business_ready': False,
            'phone_audio_verified': False, 'samples': [
                {'configuration': config, 'service_chain_completed': True, 'asr_phrase_matched': True,
                 'turn_metrics': [{'elapsed_ms': 1000 + i * 100, 'first_audio_ms': 700 + i * 100,
                                   'stage_ms': {'source_tts': 100, 'asr': 200, 'llm': 300, 'reply_tts': 400}}]}
                for i in range(count)]}


def fake_host(monkeypatch):
    calls = []
    def host(action, selection, samples=3, **kwargs):
        calls.append((action, selection, samples))
        return {'prepared': True, 'configuration': configuration(selection)} if action == 'check' else measured(selection, samples)
    monkeypatch.setattr('app.voice_combination_routes.host_request', host)
    return calls


def create(client, selection=None, tenant='TENANT_A', name='组合 A'):
    response = client.post('/api/v1/voice-combinations', headers=headers(tenant), json={
        'name': name, 'selection': (selection or VoiceSelection()).payload(), 'expected_version': 0})
    assert response.status_code == 200, response.text
    return response.json()


def benchmark(client, row, samples=3, role='admin'):
    return client.post('/api/v1/voice-combinations/compare', headers=headers(role=role), json={
        'combinations': {row['id']: row['version']}, 'samples': samples})


def enable(client, row):
    response = client.post(f"/api/v1/voice-combinations/{row['id']}/enable", headers=headers(),
                           json={'expected_version': row['version']})
    assert response.status_code == 200, response.text
    return response.json()


def activity(client):
    response = client.post('/api/v1/activities', headers=headers(), json={
        'name': '组合测试活动', 'package_id': 'PKG_A', 'goal': '首次联络与意愿确认', 'budget_yuan': 10,
        'case_ids': ['C008'], 'requested_mode': 'sandbox'})
    assert response.status_code == 201, response.text
    return response.json()['activity_id']


def test_tenant_roles_catalog_and_reserved_adapters(client):
    row = create(client)
    assert client.get('/api/v1/voice-combinations', headers=headers('TENANT_B')).json()['combinations'] == []
    assert client.put(f"/api/v1/voice-combinations/{row['id']}", headers=headers('TENANT_B'), json={
        'name': 'other', 'selection': row['selection'], 'expected_version': 1}).status_code == 404
    for role in ('operator', 'viewer'):
        assert client.post('/api/v1/voice-combinations', headers=headers(role=role), json={
            'name': 'forbidden', 'selection': row['selection'], 'expected_version': 0}).status_code == 403
        assert benchmark(client, row, role=role).status_code == 403
    reserved = client.post('/api/v1/voice-combinations', headers=headers(), json={
        'name': 'SenseVoice', 'selection': VoiceSelection(asr='sensevoice-small').payload(), 'expected_version': 0})
    assert reserved.status_code == 200, reserved.text
    assert reserved.json()['adapter_implemented'] is False
    assert benchmark(client, reserved.json()).status_code == 422
    assert client.get('/api/v1/voice-combinations', headers=headers()).json()['catalog']['profiles'] == {
        name: selected.payload() for name, selected in PROFILES.items()}


def test_measured_report_enable_revision_invalidation_and_history(client, monkeypatch):
    calls = fake_host(monkeypatch)
    row = create(client)
    assert client.post(f"/api/v1/voice-combinations/{row['id']}/enable", headers=headers(),
                       json={'expected_version': 1}).status_code == 409
    connection = client.post(f"/api/v1/voice-combinations/{row['id']}/connection-test", headers=headers(),
                             json={'expected_version': 1}).json()
    assert connection['connection']['status'] == 'prepared' and not connection['enabled']
    response = benchmark(client, row)
    assert response.status_code == 200, response.text
    report = response.json()['reports'][0]
    assert report['status'] == 'passed'
    assert report['result']['statistics']['elapsed_ms'] == {'p50_ms': 1100, 'p95_ms': 1200}
    assert report['result']['statistics']['asr']['p50_ms'] == 200
    assert report['phone_audio_verified'] is False and report['business_ready'] is False
    assert enable(client, row)['enabled']
    update = client.put(f"/api/v1/voice-combinations/{row['id']}", headers=headers(), json={
        'name': '新组合', 'selection': PROFILES['asr-fast'].payload(), 'expected_version': 1})
    assert update.status_code == 200
    assert update.json()['version'] == 2 and not update.json()['enabled'] and update.json()['connection'] == {}
    overview = client.get('/api/v1/voice-combinations', headers=headers()).json()
    assert overview['reports'][0]['current'] is False
    assert benchmark(client, row).status_code == 409
    assert len(calls) == 2


def test_disabled_host_no_fixed_latency_or_readiness(client, monkeypatch):
    monkeypatch.delenv('SIP_LAB_MODEL_HOST_ENABLED', raising=False)
    row = create(client)
    result = benchmark(client, row).json()['reports'][0]
    assert result['status'] == 'failed'
    assert result['result']['error'] == 'local_model_host_disabled'
    assert 'statistics' not in result['result']
    assert client.post(f"/api/v1/voice-combinations/{row['id']}/enable", headers=headers(),
                       json={'expected_version': 1}).status_code == 409


@pytest.mark.parametrize('change', ['digest', 'revision', 'wrong_count', 'nan', 'phrase', 'timing', 'provider_claim'])
def test_invalid_or_mismatched_host_evidence_is_rejected(client, monkeypatch, change):
    row = create(client)
    def host(action, selection, samples, **kwargs):
        result = measured(selection, samples)
        if change == 'digest':
            result['configuration']['config_digest'] = '0' * 64
        if change == 'revision':
            result['samples'][0] = copy.deepcopy(result['samples'][0])
            result['samples'][0]['configuration']['revisions']['asr'] = 'c' * 40
        if change == 'wrong_count':
            result['samples'] = []
        if change == 'nan':
            result['samples'][0]['turn_metrics'][0]['elapsed_ms'] = float('nan')
        if change == 'phrase':
            result['samples'][0]['asr_phrase_matched'] = False
        if change == 'timing':
            result['samples'][0]['turn_metrics'][0]['stage_ms'].pop('asr')
        if change == 'provider_claim':
            result['business_ready'] = True
        return result
    monkeypatch.setattr('app.voice_combination_routes.host_request', host)
    report = benchmark(client, row).json()['reports'][0]
    assert report['status'] == 'failed' and 'statistics' not in report['result']


def test_operator_uses_approved_activity_snapshot_then_config_change_blocks(client, monkeypatch):
    calls = fake_host(monkeypatch)
    row = create(client)
    benchmark(client, row)
    enable(client, row)
    identifier = activity(client)
    binding = client.post(f'/api/v1/voice-combinations/activities/{identifier}/bind', headers=headers(role='operator'),
                          json={'combination_id': row['id'], 'expected_version': 1})
    assert binding.status_code == 200, binding.text
    snapshot = binding.json()['snapshot']
    tested = client.post(f'/api/v1/voice-combinations/activities/{identifier}/test', headers=headers(role='operator'))
    assert tested.status_code == 200, tested.text
    assert tested.json()['reports'][0]['status'] == 'passed'
    assert calls[-1][2] == 1
    assert client.get(f'/api/v1/voice-combinations/activities/{identifier}', headers=headers()).json()['current']
    assert client.get(f'/api/v1/voice-combinations/activities/{identifier}', headers=headers('TENANT_B')).json()['snapshot'] is None
    client.put(f"/api/v1/voice-combinations/{row['id']}", headers=headers(), json={
        'name': 'changed', 'selection': row['selection'], 'expected_version': 1})
    choice = client.get(f'/api/v1/voice-combinations/activities/{identifier}', headers=headers()).json()
    assert not choice['current'] and choice['snapshot'] == snapshot
    assert client.post(f'/api/v1/voice-combinations/activities/{identifier}/test', headers=headers(role='operator')).status_code == 409
    assert len(calls) == 2


def test_probe_rechecks_revoked_actor_and_releases_reservation(client, monkeypatch):
    row = create(client)
    def host(action, selection, samples, **kwargs):
        with client.app.state.Session() as db:
            member = db.scalar(select(TenantMembership).where(TenantMembership.tenant_id == 'TENANT_A',
                               TenantMembership.user_id == 'test-user'))
            member.role = 'viewer'
            db.commit()
        return measured(selection, samples)
    monkeypatch.setattr('app.voice_combination_routes.host_request', host)
    assert benchmark(client, row).status_code == 403
    with client.app.state.Session() as db:
        assert db.get(VoiceCombination, row['id']).active_report_id is None
        assert db.scalar(select(VoiceBenchmark)).status == 'blocked'


def test_failed_retest_revokes_enablement_and_expired_report_blocks(client, monkeypatch):
    fake_host(monkeypatch)
    row = create(client)
    benchmark(client, row)
    enable(client, row)
    with client.app.state.Session() as db:
        report = db.scalar(select(VoiceBenchmark))
        report.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert not client.get('/api/v1/voice-combinations', headers=headers()).json()['combinations'][0]['enabled']
    benchmark(client, row)
    enable(client, row)
    monkeypatch.setattr('app.voice_combination_routes.host_request', lambda *args, **kwargs: (_ for _ in ()).throw(VoiceError('local_voice_busy')))
    failed = benchmark(client, row).json()['reports'][0]
    assert failed['status'] == 'failed'
    assert not client.get('/api/v1/voice-combinations', headers=headers()).json()['combinations'][0]['enabled']


def test_fixed_host_disabled_in_production_even_with_token(monkeypatch):
    from app.voice_combination_routes import host_request
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('SIP_LAB_MODEL_HOST_ENABLED', 'true')
    monkeypatch.setenv('SIP_LAB_MODEL_HOST_TOKEN', 'a' * 64)
    with pytest.raises(VoiceError, match='local_model_host_disabled'):
        host_request('probe', VoiceSelection())


def test_repeated_running_test_and_config_edit_fail_closed(client, monkeypatch):
    row = create(client)
    def host(action, selection, samples, **kwargs):
        assert benchmark(client, row).status_code == 409
        assert client.put(f"/api/v1/voice-combinations/{row['id']}", headers=headers(), json={
            'name': 'mid-test', 'selection': row['selection'], 'expected_version': 1}).status_code == 409
        return measured(selection, samples)
    monkeypatch.setattr('app.voice_combination_routes.host_request', host)
    assert benchmark(client, row).json()['reports'][0]['status'] == 'passed'


def test_revoked_enabling_admin_invalidates_operator_use(client, monkeypatch):
    fake_host(monkeypatch)
    row = create(client)
    benchmark(client, row)
    enable(client, row)
    identifier = activity(client)
    client.post(f'/api/v1/voice-combinations/activities/{identifier}/bind', headers=headers(role='operator'),
                json={'combination_id': row['id'], 'expected_version': 1})
    with client.app.state.Session() as db:
        member = db.scalar(select(TenantMembership).where(TenantMembership.tenant_id == 'TENANT_A',
                           TenantMembership.user_id == 'test-user'))
        member.role = 'viewer'
        db.commit()
    assert not client.get('/api/v1/voice-combinations', headers=headers(role='operator')).json()['combinations'][0]['enabled']
    assert not client.get(f'/api/v1/voice-combinations/activities/{identifier}', headers=headers(role='operator')).json()['current']
    assert client.post(f'/api/v1/voice-combinations/activities/{identifier}/test', headers=headers(role='operator')).status_code == 409


def test_catalog_fallback_matches_authoritative_catalog():
    import json
    from pathlib import Path

    from app.sip_lab_voice_config import catalog_report
    assert json.loads((Path(__file__).resolve().parents[2] / 'src/voice-model-catalog.json').read_text()) == catalog_report()


def test_private_host_credentials_never_replace_voice_or_sip_config(tmp_path):
    import importlib.util
    from pathlib import Path
    script = Path(__file__).resolve().parents[2] / 'scripts/sip-lab-model-host-config.py'
    spec = importlib.util.spec_from_file_location('host_config', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / 'lab.env').write_text('synthetic-sip-original')
    (tmp_path / 'local-voice.env').write_text('synthetic-voice-original')
    module.generate(tmp_path)
    host = tmp_path / 'model-host.env'
    assert host.stat().st_mode & 0o777 == 0o600
    assert 'SIP_LAB_MODEL_HOST_ENABLED=true' in host.read_text()
    with pytest.raises(FileExistsError):
        module.generate(tmp_path)
    assert (tmp_path / 'lab.env').read_text() == 'synthetic-sip-original'
    assert (tmp_path / 'local-voice.env').read_text() == 'synthetic-voice-original'


def test_activity_probe_rejects_new_weight_revision_without_new_admin_approval(client, monkeypatch):
    fake_host(monkeypatch)
    row = create(client)
    benchmark(client, row)
    enable(client, row)
    identifier = activity(client)
    client.post(f'/api/v1/voice-combinations/activities/{identifier}/bind', headers=headers(role='operator'),
                json={'combination_id': row['id'], 'expected_version': 1})
    def changed_host(action, selection, samples, **kwargs):
        assert kwargs['expected_configuration']['revisions']['asr'] == 'b' * 40
        result = measured(selection, samples)
        result['configuration']['revisions']['asr'] = 'c' * 40
        return result
    monkeypatch.setattr('app.voice_combination_routes.host_request', changed_host)
    result = client.post(f'/api/v1/voice-combinations/activities/{identifier}/test', headers=headers(role='operator'))
    assert result.status_code == 200
    assert result.json()['reports'][0]['status'] == 'failed'
    assert result.json()['reports'][0]['result']['error'] == 'local_voice_revision_mismatch'
    assert not client.get(f'/api/v1/voice-combinations/activities/{identifier}', headers=headers(role='operator')).json()['current']


def test_unknown_expired_probe_is_visible_and_does_not_authorize_activity(client, monkeypatch):
    fake_host(monkeypatch)
    row = create(client)
    benchmark(client, row)
    enable(client, row)
    with client.app.state.Session() as db:
        pending = VoiceBenchmark(tenant_id='TENANT_A', combination_id=row['id'], config_version=1,
                                 comparison_id='CMP-EXPIRED', expires_at=utcnow()-timedelta(seconds=1))
        db.add(pending)
        db.flush()
        db.get(VoiceCombination,row['id']).active_report_id = pending.id
        db.commit()
    state = client.get('/api/v1/voice-combinations',headers=headers()).json()
    assert state['combinations'][0]['enabled'] is False
    assert state['combinations'][0]['active_report_id'] is None
    assert any(report['status']=='unknown' for report in state['reports'])
    # A new explicit admin probe can recover; it never replays the old request automatically.
    assert benchmark(client,row).json()['reports'][0]['status']=='passed'
    assert enable(client,row)['enabled']


def test_admin_status_disabled_and_production_never_contact_host(client, monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: pytest.fail('disabled status must not access network'))
    monkeypatch.delenv('SIP_LAB_MODEL_HOST_ENABLED', raising=False)
    for role in ('operator', 'viewer'):
        assert client.get('/api/v1/voice-combinations/host-status', headers=headers(role=role)).status_code == 403
    result = client.get('/api/v1/voice-combinations/host-status', headers=headers()).json()
    assert result['state'] == 'disabled' and result['tenant_id'] == 'TENANT_A'
    assert not result['business_ready'] and not result['phone_audio_verified']
    monkeypatch.setenv('SIP_LAB_MODEL_HOST_ENABLED', 'true')
    monkeypatch.setenv('SIP_LAB_MODEL_HOST_TOKEN', 'a' * 64)
    monkeypatch.setenv('APP_ENV', 'production')
    from app.voice_combination_routes import host_status
    assert host_status() == {'state': 'disabled'}


@pytest.mark.parametrize('payload', [
    {'state': 'ready'}, {'state': 'ready', 'source': 'local_model_host', 'business_ready': True,
                       'phone_audio_verified': False, 'pstn_enabled': False},
    {'state': 'ready', 'source': 'local_model_host', 'business_ready': 0,
     'phone_audio_verified': False, 'pstn_enabled': False},
    {'state': 'private token aaaaaa'}, {'state': []}, ['ready'],
])
def test_host_status_rejects_untrusted_claims(client, monkeypatch, payload):
    import httpx

    from app import voice_combination_routes as module
    original = httpx.Client
    monkeypatch.setenv('APP_ENV', 'development')
    monkeypatch.setenv('SIP_LAB_MODEL_HOST_ENABLED', 'true')
    monkeypatch.setenv('SIP_LAB_MODEL_HOST_TOKEN', 'a' * 64)
    def client_factory(**kwargs):
        assert kwargs == {'timeout': 2, 'trust_env': False, 'follow_redirects': False}
        return original(**kwargs, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    monkeypatch.setattr(module.httpx, 'Client', client_factory)
    result = client.get('/api/v1/voice-combinations/host-status', headers=headers())
    assert result.json()['state'] == 'invalid_response' and 'private token' not in result.text


def test_business_host_native_readonly_authenticated_chain(client, monkeypatch):
    from types import SimpleNamespace

    import httpx

    from app import voice_combination_routes as module
    from app.sip_lab_local_voice import create_app as native_app
    from app.sip_lab_model_host import ModelHost
    from app.sip_lab_model_host import create_app as host_app
    original = httpx.Client
    native_token, host_token = 'a' * 64, 'c' * 64
    manager = ModelHost(native_token)
    manager.configuration = configuration(VoiceSelection())
    manager.child = SimpleNamespace(poll=lambda: None, terminate=lambda: pytest.fail('diagnostics cannot stop phone'))
    engine = SimpleNamespace(warmed=True, configuration=manager.configuration)
    monkeypatch.setenv('APP_ENV', 'development')
    monkeypatch.setenv('SIP_LAB_MODEL_HOST_ENABLED', 'true')
    monkeypatch.setenv('SIP_LAB_MODEL_HOST_TOKEN', host_token)
    with TestClient(native_app(engine, native_token)) as native, TestClient(host_app(manager, host_token)) as host:
        calls = []
        def native_request(method, path):
            assert (method, path) == ('GET', '/lab/status')
            calls.append('native')
            return native.request(method, path, headers={'Authorization': 'Bearer ' + native_token})
        manager.native = native_request
        def transport(request):
            assert request.method == 'GET' and str(request.url) == 'http://127.0.0.1:8091/lab/status'
            assert request.headers['Authorization'] == 'Bearer ' + host_token
            calls.append('host')
            response = host.get('/lab/status', headers={'Authorization': request.headers['Authorization']})
            return httpx.Response(response.status_code, content=response.content)
        monkeypatch.setattr(module.httpx, 'Client', lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(transport)))
        with native.websocket_connect('/lab/voice', headers={'Authorization': 'Bearer ' + native_token}):
            result = client.get('/api/v1/voice-combinations/host-status', headers=headers()).json()
            assert result['state'] == 'phone_or_model_busy'
        until = time.monotonic() + 3
        while True:
            result = client.get('/api/v1/voice-combinations/host-status', headers=headers()).json()
            if result['state'] != 'phone_or_model_busy' or time.monotonic() >= until:
                break
            time.sleep(0.01)
        assert result['state'] == 'ready' and not result['phone_audio_verified']
        assert calls == ['host', 'native'] * (len(calls) // 2)
        assert native_token not in str(result) and host_token not in str(result)
    manager.child = None
    manager.close()


@pytest.mark.parametrize(('kind', 'state'), [('auth', 'auth_failed'), ('redirect', 'unreachable'),
                                            ('timeout', 'unreachable'), ('oversized', 'invalid_response')])
def test_host_status_network_failures_are_bounded_and_redacted(client, monkeypatch, kind, state):
    import httpx

    from app import voice_combination_routes as module
    original = httpx.Client
    monkeypatch.setenv('SIP_LAB_MODEL_HOST_ENABLED', 'true')
    monkeypatch.setenv('SIP_LAB_MODEL_HOST_TOKEN', 'c' * 64)
    def transport(request):
        if kind == 'timeout':
            raise httpx.ReadTimeout('secret ' + 'c' * 64)
        if kind == 'oversized':
            return httpx.Response(200, content=b'x' * 4097)
        return httpx.Response(403 if kind == 'auth' else 302, headers={'Location': 'https://outside'},
                              json={'secret': 'c' * 64})
    monkeypatch.setattr(module.httpx, 'Client', lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(transport)))
    result = client.get('/api/v1/voice-combinations/host-status', headers=headers())
    assert result.status_code == 200 and result.json()['state'] == state
    assert 'c' * 64 not in result.text and 'secret' not in result.text


def test_status_rechecks_revoked_admin_without_changing_saved_reports(client, monkeypatch):
    row = create(client)
    def status():
        with client.app.state.Session() as db:
            member = db.scalar(select(TenantMembership).where(TenantMembership.tenant_id == 'TENANT_A',
                               TenantMembership.user_id == 'test-user'))
            member.role = 'viewer'
            db.commit()
        return {'state': 'ready'}
    monkeypatch.setattr('app.voice_combination_routes.host_status', status)
    assert client.get('/api/v1/voice-combinations/host-status', headers=headers()).status_code == 403
    with client.app.state.Session() as db:
        saved = db.get(VoiceCombination, row['id'])
        assert saved.version == 1 and saved.connection == {} and saved.active_report_id is None
        assert db.scalar(select(VoiceBenchmark)) is None
