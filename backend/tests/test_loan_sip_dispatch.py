from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from test_loan_collection import BASE, H, enroll, start
from test_loan_collection import client as client

from app import loan_sip_dispatch
from app.domain import utcnow
from app.loan_models import LoanContactPolicy, LoanSession, LoanSipDispatch
from app.models import CaseRecord, TenantMembership
from app.worker import DatabaseWorker


@pytest.fixture
def lab(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'development')
    monkeypatch.setenv('ENABLE_SIP_LAB', 'true')
    monkeypatch.setenv('SIP_LAB_TENANT_ID', 'TENANT_A')
    monkeypatch.setenv('SIP_LAB_INSTANCE_ID', 'a' * 32)
    monkeypatch.setenv('SIP_LAB_ARI_PASSWORD', 'SYNTHETIC_' + 'x' * 40)
    calls = []

    def response(request):
        calls.append(request)
        return httpx.Response(200, json={'id': request.url.path.rsplit('/', 1)[-1], 'state': 'Up'})

    monkeypatch.setattr(loan_sip_dispatch, 'http_client', lambda _: httpx.Client(
        base_url='http://127.0.0.1:8088/ari', transport=httpx.MockTransport(response)))
    return calls


def reserve(client):
    response = enroll(client)
    assert response.status_code == 200, response.text
    response = start(client)
    assert response.status_code == 201, response.text
    session = response.json()
    response = client.post(BASE + f"/sessions/{session['id']}/sip-echo", headers=H,
                           json={'expected_version': session['version'], 'acknowledged': True,
                                 'test_extension_only': True})
    assert response.status_code == 202, response.text
    return session, response.json()


def work(client, job_id):
    assert DatabaseWorker(client.app.state.Session, worker_id='sip-worker').run_once()
    result = client.get(f'/api/v1/jobs/{job_id}', headers=H).json()
    assert result['status'] == 'succeeded', result
    return result['result']


def test_fixed_echo_dispatch_and_duplicate_never_dials_twice(client, lab):
    session, queued = reserve(client)
    result = work(client, queued['job']['id'])
    assert result['state'] == 'submitted' and not result['customer_contact']
    assert not result['audio_verified'] and not result['ai_dialogue_ready']
    request = lab[0]
    assert request.method == 'POST' and request.url.params['endpoint'] == 'PJSIP/1001'
    assert 'C004' not in str(request.url) and 'amount' not in str(request.url)
    duplicate = client.post(BASE + f"/sessions/{session['id']}/sip-echo", headers=H,
                            json={'expected_version': 1, 'acknowledged': True, 'test_extension_only': True}).json()
    assert not duplicate['created'] and duplicate['job']['id'] == queued['job']['id']
    query = client.post(BASE + f"/sip-dispatches/{result['id']}/reconcile", headers=H,
                        json={'request_key': 'query-1', 'acknowledged': True}).json()
    assert work(client, query['job']['id'])['observation']['channel_state'] == 'Up'
    assert [r.method for r in lab] == ['POST', 'GET']


def test_committed_intent_survives_restart_and_only_queries(client, lab):
    _, queued = reserve(client)
    with client.app.state.Session() as db:
        db.get(LoanSipDispatch, queued['dispatch']['id']).state = 'dispatching'
        db.commit()
    result = work(client, queued['job']['id'])
    assert [r.method for r in lab] == ['GET']
    assert result['state'] == 'dispatching' and result['observation']['channel_state'] == 'Up'


@pytest.mark.parametrize('change', ['protection', 'policy', 'expiry', 'role', 'cancel'])
def test_changes_after_reservation_block_network(client, lab, change):
    session, queued = reserve(client)
    if change == 'cancel':
        from app.job_queue import claim_job
        assert claim_job(client.app.state.Session, 'sip-worker', job_id=queued['job']['id'])
    with client.app.state.Session() as db:
        if change == 'protection':
            db.scalar(select(CaseRecord).where(CaseRecord.tenant_id == 'TENANT_A', CaseRecord.case_id == 'C004')).blocked = True
        elif change == 'policy':
            db.get(LoanContactPolicy, 'TENANT_A').paused = True
        elif change == 'expiry':
            db.get(LoanSession, session['id']).authorization_expires_at = utcnow() - timedelta(seconds=1)
        elif change == 'role':
            db.scalar(select(TenantMembership).where(TenantMembership.tenant_id == 'TENANT_A',
                                                   TenantMembership.user_id == 'Terry')).role = 'operator'
        else:
            from app.models import AsyncJob
            db.get(AsyncJob, queued['job']['id']).cancel_requested_at = utcnow()
        db.commit()
    if change == 'cancel':
        from app.jobs import execute_claimed_job
        execute_claimed_job(client.app.state.Session, queued['job']['id'], 'sip-worker')
    else:
        assert work(client, queued['job']['id'])['state'] == 'blocked'
    assert not lab


def test_missing_binding_never_reserves(client, lab, monkeypatch):
    response = enroll(client)
    assert response.status_code == 200, response.text
    response = start(client)
    assert response.status_code == 201, response.text
    session = response.json()
    monkeypatch.setenv('SIP_LAB_TENANT_ID', 'OTHER_TENANT')
    response = client.post(BASE + f"/sessions/{session['id']}/sip-echo", headers=H,
                           json={'expected_version': 1, 'acknowledged': True, 'test_extension_only': True})
    assert response.status_code == 503
    with client.app.state.Session() as db:
        assert db.scalar(select(LoanSipDispatch)) is None


def test_timeout_then_lookup_404_never_redials(client, lab, monkeypatch):
    methods = []

    def response(request):
        methods.append(request.method)
        if request.method == 'POST':
            raise httpx.ReadTimeout('synthetic timeout', request=request)
        return httpx.Response(404)

    monkeypatch.setattr(loan_sip_dispatch, 'http_client', lambda _: httpx.Client(
        base_url='http://127.0.0.1:8088/ari', transport=httpx.MockTransport(response)))
    _, queued = reserve(client)
    result = work(client, queued['job']['id'])
    assert result['state'] == 'unknown'
    query = client.post(BASE + f"/sip-dispatches/{result['id']}/reconcile", headers=H,
                        json={'request_key': 'timeout-query', 'acknowledged': True}).json()
    result = work(client, query['job']['id'])
    assert result['state'] == 'unknown' and result['observation']['channel_state'] == 'not_found_or_ended'
    assert methods == ['POST', 'GET']


def test_dispatch_read_and_query_are_tenant_scoped(client, lab):
    _, queued = reserve(client)
    headers = {**H, 'X-Tenant-ID': 'TENANT_B'}
    url = BASE + f"/sip-dispatches/{queued['dispatch']['id']}"
    assert client.get(url, headers=headers).status_code == 404
    assert client.post(url + '/reconcile', headers=headers,
                       json={'request_key': 'cross-tenant', 'acknowledged': True}).status_code == 404
    assert not lab


def test_stop_before_worker_blocks_original_send_without_freeing_reservation(client, lab):
    session, queued = reserve(client)
    url = BASE + f"/sip-dispatches/{queued['dispatch']['id']}/stop"
    stopped = client.post(url, headers=H, json={'request_key': 'stop-1', 'acknowledged': True})
    assert stopped.status_code == 202
    assert work(client, queued['job']['id'])['state'] == 'blocked'
    assert work(client, stopped.json()['job']['id'])['state'] == 'blocked'
    assert not lab
    duplicate = client.post(BASE + f"/sessions/{session['id']}/sip-echo", headers=H,
                            json={'expected_version': 1, 'acknowledged': True, 'test_extension_only': True}).json()
    assert not duplicate['created'] and duplicate['dispatch']['state'] == 'blocked'


def test_stop_after_send_only_deletes_fixed_channel(client, lab, monkeypatch):
    _, queued = reserve(client)
    result = work(client, queued['job']['id'])
    methods = []

    def response(request):
        methods.append(request.method)
        assert request.url.path.startswith('/ari/channels/RG-LAB-')
        return httpx.Response(204)

    monkeypatch.setattr(loan_sip_dispatch, 'http_client', lambda _: httpx.Client(
        base_url='http://127.0.0.1:8088/ari', transport=httpx.MockTransport(response)))
    stopped = client.post(BASE + f"/sip-dispatches/{result['id']}/stop", headers=H,
                          json={'request_key': 'stop-2', 'acknowledged': True}).json()
    result = work(client, stopped['job']['id'])
    assert result['state'] == 'stop_requested' and result['observation']['hangup_state'] == 'requested'
    assert methods == ['DELETE'] and not result['audio_verified']


def test_instance_change_cannot_authorize_second_dispatch(client, lab, monkeypatch):
    session, queued = reserve(client)
    monkeypatch.setenv('SIP_LAB_INSTANCE_ID', 'b' * 32)
    response = client.post(BASE + f"/sessions/{session['id']}/sip-echo", headers=H,
                           json={'expected_version': 1, 'acknowledged': True, 'test_extension_only': True})
    assert response.status_code == 409
    DatabaseWorker(client.app.state.Session, worker_id='changed-instance').run_once()
    assert not lab
    with client.app.state.Session() as db:
        assert db.get(LoanSipDispatch, queued['dispatch']['id']).instance_id == 'a' * 32


def test_daily_budget_counts_unknown_reservations(client, lab):
    enroll(client)
    session = start(client).json()
    with client.app.state.Session() as db:
        source = db.get(LoanSession, session['id'])
        for number in range(20):
            old = LoanSession(id=f'LC-budget-{number}', tenant_id='TENANT_A', case_id=source.case_id,
                              request_key=f'budget-{number}', state='ended', mode='sandbox',
                              profile_version=source.profile_version, authorized_by='Terry')
            db.add(old)
            db.flush()
            db.add(LoanSipDispatch(tenant_id='TENANT_A', session_id=old.id, session_version=1,
                                   instance_id='a' * 32, authorized_by='Terry', state='unknown'))
        db.commit()
    response = client.post(BASE + f"/sessions/{session['id']}/sip-echo", headers=H,
                           json={'expected_version': 1, 'acknowledged': True, 'test_extension_only': True})
    assert response.status_code == 409 and '配额' in response.text
    assert not lab


def test_production_lab_config_is_unavailable(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setenv('APP_ENV', 'production')
    with pytest.raises(HTTPException) as denied:
        loan_sip_dispatch.lab_config('TENANT_A')
    assert denied.value.status_code == 503


def test_exception_pause_enqueues_stop_without_changing_real_protection(client, lab, monkeypatch):
    session, queued = reserve(client)
    result = work(client, queued['job']['id'])
    methods = []
    monkeypatch.setattr(loan_sip_dispatch, 'http_client', lambda _: httpx.Client(
        base_url='http://127.0.0.1:8088/ari', transport=httpx.MockTransport(
            lambda r: (methods.append(r.method), httpx.Response(204))[1])))
    response = client.post(BASE + f"/sessions/{session['id']}/events", headers=H,
                           json={'event_key': 'wrong-person', 'expected_version': 1,
                                 'intent': 'wrong_person', 'acknowledged': True})
    assert response.status_code == 200 and response.json()['state'] == 'paused'
    assert DatabaseWorker(client.app.state.Session, worker_id='stop-exception').run_once()
    assert methods == ['DELETE']
    with client.app.state.Session() as db:
        case = db.scalar(select(CaseRecord).where(CaseRecord.tenant_id == 'TENANT_A', CaseRecord.case_id == 'C004'))
        assert not case.blocked  # Sandbox assertion must not become real protection evidence.
        assert db.get(LoanSipDispatch, result['id']).state == 'stop_requested'


def test_policy_change_blocks_prepared_dispatch_and_schedules_stop(client, lab):
    from test_loan_collection import configure_policy
    _, queued = reserve(client)
    response = configure_policy(client, expected_version=1, paused=True)
    assert response.status_code == 200
    assert work(client, queued['job']['id'])['state'] == 'blocked'
    assert DatabaseWorker(client.app.state.Session, worker_id='stop-policy').run_once()
    assert not lab


def test_stop_between_committed_intent_and_send_prevents_origination(client, lab, monkeypatch):
    _, queued = reserve(client)
    original_lock = loan_sip_dispatch.lock_policy_scope
    locks = 0

    def stop_before_reacquiring(db, tenant):
        nonlocal locks
        locks += 1
        if locks == 2:
            response = client.post(BASE + f"/sip-dispatches/{queued['dispatch']['id']}/stop", headers=H,
                                   json={'request_key': 'race-stop', 'acknowledged': True})
            assert response.status_code == 202
            assert response.json()['dispatch']['state'] == 'stop_requested'
        return original_lock(db, tenant)

    monkeypatch.setattr(loan_sip_dispatch, 'lock_policy_scope', stop_before_reacquiring)
    result = work(client, queued['job']['id'])
    assert result['state'] == 'stop_requested'
    assert not lab
    assert DatabaseWorker(client.app.state.Session, worker_id='race-stop').run_once()
    assert [request.method for request in lab] == ['DELETE']


def test_query_after_stop_cannot_reopen_or_originate(client, lab):
    _, queued = reserve(client)
    result = work(client, queued['job']['id'])
    stopped = client.post(BASE + f"/sip-dispatches/{result['id']}/stop", headers=H,
                          json={'request_key': 'stop-query', 'acknowledged': True}).json()
    assert stopped['dispatch']['state'] == 'stop_requested'
    work(client, stopped['job']['id'])
    query = client.post(BASE + f"/sip-dispatches/{result['id']}/reconcile", headers=H,
                        json={'request_key': 'query-stop', 'acknowledged': True}).json()
    result = work(client, query['job']['id'])
    assert result['state'] == 'stop_requested'
    assert result['observation']['hangup_state'] == 'unknown'
    assert [request.method for request in lab] == ['POST', 'DELETE', 'GET']
