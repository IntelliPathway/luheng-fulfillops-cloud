"""Tenant model choices and measured synthetic probes. Never grants phone execution."""
import math
import os
import re
from datetime import timedelta

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .audit import audit
from .dependencies import Context, Database
from .domain import utcnow
from .loan_policy import lock_policy_scope
from .models import Activity, TenantMembership, User, VoiceBenchmark, VoiceCombination, new_id
from .security import require_role
from .sip_lab_model_host import HOST_STATES, status_report
from .sip_lab_voice import TOKEN, VoiceError
from .sip_lab_voice_config import VoiceSelection, catalog_report, validate_service_configuration

router = APIRouter(prefix='/api/v1/voice-combinations', tags=['voice-combinations'])
BOUNDARY = {'business_ready': False, 'phone_audio_verified': False, 'pstn_enabled': False,
            'usage_scope': 'synthetic_lab'}
ERRORS = {'local_voice_busy', 'model_host_busy', 'unmanaged_model_service_running',
          'model_load_failed', 'model_load_timeout', 'managed_model_unreachable',
          'local_voice_revision_mismatch', 'adapter_not_implemented', 'model_not_prepared_or_invalid'}


class Save(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=80, pattern=r'^[^\r\n<>]+$')
    selection: dict
    expected_version: int = Field(strict=True, ge=0)


class Version(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(strict=True, ge=1)


class Compare(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    combinations: dict[str, int] = Field(min_length=1, max_length=4)
    samples: int = Field(default=3, strict=True, ge=1, le=3)


class Bind(Version):
    combination_id: str = Field(pattern=r'^VC-[A-F0-9]{12}$')


def selection_for(payload, available=False):
    try:
        return VoiceSelection.from_payload(payload, require_available=available)
    except VoiceError:
        raise HTTPException(status_code=422, detail='模型组合无效或适配器尚未实现') from None


def host_request(action, selection, samples=3, expected_configuration=None):
    if os.getenv('APP_ENV', 'development') not in {'development', 'test'} or os.getenv(
            'SIP_LAB_MODEL_HOST_ENABLED') != 'true':
        raise VoiceError('local_model_host_disabled')
    token = os.getenv('SIP_LAB_MODEL_HOST_TOKEN', '')
    if not TOKEN.fullmatch(token):
        raise VoiceError('local_model_host_disabled')
    # Fixed loopback target. No arbitrary URLs, proxy, redirects, credentials or case data from the browser.
    with httpx.Client(timeout=720, trust_env=False, follow_redirects=False) as client:
        response = client.post('http://127.0.0.1:8091/lab/' + action,
                               headers={'Authorization': 'Bearer ' + token},
                               json={'selection': selection.payload(), 'samples': samples,
                                     'expected_revisions': expected_configuration['revisions'] if expected_configuration else None})
        if response.status_code != 200:
            try:
                code = response.json().get('detail')
            except ValueError:
                code = None
            raise VoiceError(code if code in ERRORS else 'local_model_host_unavailable')
        if len(response.content) > 32000:
            raise VoiceError('invalid_model_host_report')
        return response.json()


def host_status():
    if os.getenv('APP_ENV', 'development') not in {'development', 'test'} or os.getenv(
            'SIP_LAB_MODEL_HOST_ENABLED') != 'true':
        return {'state': 'disabled'}
    token = os.getenv('SIP_LAB_MODEL_HOST_TOKEN', '')
    if not TOKEN.fullmatch(token):
        return {'state': 'credentials_missing'}
    try:
        with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client:
            with client.stream('GET', 'http://127.0.0.1:8091/lab/status',
                               headers={'Authorization': 'Bearer ' + token}) as response:
                if response.status_code != 200:
                    return {'state': 'auth_failed' if response.status_code == 403 else 'unreachable'}
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=4096):
                    if len(body) + len(chunk) > 4096:
                        return {'state': 'invalid_response'}
                    body.extend(chunk)
        import json
        data = json.loads(body)
        if not isinstance(data, dict) or not isinstance(data.get('state'), str) or data['state'] not in HOST_STATES:
            return {'state': 'invalid_response'}
        expected = status_report(data['state'])
        if set(data) != set(expected) or any(type(data[key]) is not type(value) or data[key] != value
                                             for key, value in expected.items()):
            return {'state': 'invalid_response'}
        return {'state': data['state']}
    except httpx.TransportError:
        return {'state': 'unreachable'}
    except (ValueError, TypeError):
        return {'state': 'invalid_response'}


def row_for(db, tenant, identifier):
    row = db.scalar(select(VoiceCombination).where(VoiceCombination.tenant_id == tenant,
                    VoiceCombination.id == identifier).execution_options(populate_existing=True))
    if not row:
        raise HTTPException(status_code=404, detail='模型组合不存在')
    return row


def require_version(row, expected):
    if type(expected) is not int or row.version != expected:
        raise HTTPException(status_code=409, detail='配置版本已改变，请刷新')


def current_actor(db, context, roles):
    user = db.get(User, context.actor_id, populate_existing=True)
    member = db.scalar(select(TenantMembership).where(TenantMembership.tenant_id == context.tenant_id,
                       TenantMembership.user_id == context.actor_id).execution_options(populate_existing=True))
    if not user or user.status != 'active' or not member or member.status != 'active' or member.role not in roles:
        raise HTTPException(status_code=403, detail='当前成员权限已失效')


def active_report(db, row):
    report = db.get(VoiceBenchmark, row.active_report_id) if row.active_report_id else None
    return report if report and report.status == 'running' and report.expires_at > utcnow() else None


def fresh_report(db, row):
    if row.active_report_id:
        pending = db.get(VoiceBenchmark, row.active_report_id)
        if pending and pending.status == 'running' and pending.expires_at <= utcnow():
            return None
    if not row.enabled_report_id or not row.enabled_by:
        return None
    user = db.get(User, row.enabled_by, populate_existing=True)
    member = db.scalar(select(TenantMembership).where(TenantMembership.tenant_id == row.tenant_id,
                       TenantMembership.user_id == row.enabled_by, TenantMembership.status == 'active',
                       TenantMembership.role == 'admin'))
    if not user or user.status != 'active' or not member:
        return None
    report = db.get(VoiceBenchmark, row.enabled_report_id)
    if report and report.tenant_id == row.tenant_id and report.combination_id == row.id and (
            report.config_version == row.version and report.status == 'passed' and report.expires_at > utcnow() and
            row.connection.get('status') == 'prepared' and row.connection.get('configuration') == report.result.get('configuration')):
        return report
    return None


def view(db, row):
    selected = selection_for(row.selection)
    report = fresh_report(db, row)
    return {'id': row.id, 'tenant_id': row.tenant_id, 'name': row.name, 'selection': row.selection,
            'config_digest': selected.config_digest, 'version': row.version,
            'adapter_implemented': all(item['adapter_implemented'] for alias, item in catalog_report()['models'].items()
                                       if alias in (selected.asr, selected.llm, selected.tts)),
            'connection': row.connection, 'enabled': bool(report),
            'enabled_report_id': report.id if report else None, 'active_report_id': row.active_report_id if active_report(db, row) else None,
            'updated_at': row.updated_at, **BOUNDARY}


def report_view(db, report):
    row = row_for(db, report.tenant_id, report.combination_id)
    return {'id': report.id, 'combination_id': row.id, 'combination_name': row.name,
            'config_version': report.config_version, 'comparison_id': report.comparison_id,
            'status': report.status if report.status != 'running' or report.expires_at > utcnow() else 'unknown',
            'current': report.config_version == row.version and report.expires_at > utcnow(),
            'result': report.result, 'created_at': report.created_at, 'expires_at': report.expires_at, **BOUNDARY}


def configuration_matches(data, selection):
    configuration = data.get('configuration')
    validate_service_configuration(configuration)
    if configuration['config_digest'] != selection.config_digest or configuration['selection'] != selection.payload():
        raise VoiceError('local_voice_configuration_mismatch')
    return configuration


def measure(data, selection, count):
    configuration = configuration_matches(data, selection)
    if data.get('source') != 'local_model_host' or data.get('business_ready') is not False or (
            data.get('phone_audio_verified') is not False):
        raise VoiceError('invalid_model_host_report')
    samples = data.get('samples')
    if not isinstance(samples, list) or len(samples) != count:
        raise VoiceError('invalid_model_host_report')
    metrics = []
    for sample in samples:
        if not isinstance(sample, dict) or sample.get('configuration') != configuration or (
                sample.get('service_chain_completed') is not True or sample.get('asr_phrase_matched') is not True):
            raise VoiceError('invalid_model_host_report')
        timing = sample.get('turn_metrics')
        if not isinstance(timing, list) or len(timing) != 1:
            raise VoiceError('invalid_model_host_report')
        item = timing[0]
        stages = item.get('stage_ms')
        if not isinstance(stages, dict) or set(stages) != {'source_tts', 'asr', 'llm', 'reply_tts'}:
            raise VoiceError('invalid_model_host_report')
        flat = {'elapsed_ms': item.get('elapsed_ms'), 'first_audio_ms': item.get('first_audio_ms'), **stages}
        if any(type(n) not in (int, float) or not math.isfinite(n) or not 0 <= n <= 20000 for n in flat.values()):
            raise VoiceError('invalid_model_host_report')
        metrics.append(flat)
    statistics = {}
    for key in metrics[0]:
        values = sorted(item[key] for item in metrics)
        statistics[key] = {'p50_ms': values[math.ceil(len(values) * .5) - 1],
                           'p95_ms': values[math.ceil(len(values) * .95) - 1]}
    return {'source': 'local_model_host', 'configuration': configuration, 'sample_count': count,
            'phrase': 'fixed_internal_phrase_v1', 'samples': metrics, 'statistics': statistics,
            'timing_scope': 'warmed_worker_includes_stream_queue_wait_excludes_load_http_roundtrip_phone_playback', **BOUNDARY}


@router.get('')
def overview(context: Context, db: Database):
    rows = list(db.scalars(select(VoiceCombination).where(VoiceCombination.tenant_id == context.tenant_id)
                          .order_by(VoiceCombination.updated_at.desc())))
    reports = list(db.scalars(select(VoiceBenchmark).where(VoiceBenchmark.tenant_id == context.tenant_id)
                             .order_by(VoiceBenchmark.created_at.desc()).limit(40)))
    return {'tenant_id': context.tenant_id, 'catalog': catalog_report(), 'combinations': [view(db, r) for r in rows],
            'reports': [report_view(db, r) for r in reports], **BOUNDARY}


@router.get('/host-status')
def model_host_status(context: Context, db: Database):
    require_role(context, 'admin')
    current_actor(db, context, {'admin'})
    state = host_status()
    # A slow host response cannot revive a revoked administrator or stale UI evidence.
    db.rollback()
    current_actor(db, context, {'admin'})
    return {'tenant_id': context.tenant_id, 'checked_at': utcnow(), **state, **BOUNDARY}


def save(db, context, payload, identifier=None):
    require_role(context, 'admin')
    selected = selection_for(payload.selection)
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail='组合名称不能为空')
    lock_policy_scope(db, context.tenant_id)
    current_actor(db, context, {'admin'})
    if identifier:
        row = row_for(db, context.tenant_id, identifier)
        require_version(row, payload.expected_version)
        if row.active_report_id:
            report = db.get(VoiceBenchmark, row.active_report_id)
            if report and report.expires_at > utcnow():
                raise HTTPException(status_code=409, detail='测试正在执行，请等待完成后修改')
        row.version += 1
    else:
        if payload.expected_version != 0:
            raise HTTPException(status_code=409, detail='新配置版本必须为零')
        if len(list(db.scalars(select(VoiceCombination.id).where(VoiceCombination.tenant_id == context.tenant_id)))) >= 12:
            raise HTTPException(status_code=409, detail='最多保存 12 套模型组合')
        row = VoiceCombination(tenant_id=context.tenant_id, version=1)
        db.add(row)
    row.name = payload.name.strip()
    row.selection = selected.payload()
    row.connection = {}
    row.enabled_report_id = row.enabled_by = row.active_report_id = None
    db.flush()
    audit(db, context, 'voice_combination.saved', 'voice_combination', row.id, {'version': row.version})
    db.commit()
    return view(db, row)


@router.post('')
def create(payload: Save, context: Context, db: Database):
    return save(db, context, payload)


@router.put('/{identifier}')
def update(identifier: str, payload: Save, context: Context, db: Database):
    return save(db, context, payload, identifier)


@router.post('/{identifier}/connection-test')
def connection(identifier: str, payload: Version, context: Context, db: Database):
    require_role(context, 'admin')
    lock_policy_scope(db, context.tenant_id)
    current_actor(db, context, {'admin'})
    row = row_for(db, context.tenant_id, identifier)
    require_version(row, payload.expected_version)
    selected = selection_for(row.selection, True)
    if active_report(db, row):
        raise HTTPException(status_code=409, detail='测试正在执行，请等待完成')
    row.active_report_id = None
    row.enabled_report_id = row.enabled_by = None
    request_id = new_id('VCT')
    row.connection = {'status': 'checking', 'version': row.version, 'request_id': request_id}
    db.commit()
    try:
        data = host_request('check', selected)
        configuration = configuration_matches(data, selected)
        if data.get('prepared') is not True:
            raise VoiceError('model_not_prepared_or_invalid')
        result = {'status': 'prepared', 'configuration': configuration, 'version': payload.expected_version}
    except Exception as exc:
        code = str(exc) if isinstance(exc, VoiceError) else 'local_model_host_unavailable'
        result = {'status': 'unavailable', 'error': code if re.fullmatch(r'[a-z_]{1,80}', code) else 'adapter_not_implemented',
                  'version': payload.expected_version}
    lock_policy_scope(db, context.tenant_id)
    current_actor(db, context, {'admin'})
    row = row_for(db, context.tenant_id, identifier)
    require_version(row, payload.expected_version)
    if row.connection.get('request_id') != request_id or row.active_report_id:
        raise HTTPException(status_code=409, detail='连接检查已被新请求替代')
    row.enabled_report_id = row.enabled_by = None
    row.connection = result
    audit(db, context, 'voice_combination.connection_tested', 'voice_combination', row.id, {'status': result['status']})
    db.commit()
    return view(db, row)


def run_comparison(db, context, payload, roles=('admin',), revoke=True):
    require_role(context, *roles)
    lock_policy_scope(db, context.tenant_id)
    current_actor(db, context, set(roles))
    rows = []
    for identifier, version in payload.combinations.items():
        row = row_for(db, context.tenant_id, identifier)
        require_version(row, version)
        selected = selection_for(row.selection, True)
        if row.connection.get('status') == 'checking':
            raise HTTPException(status_code=409, detail='连接检查正在执行')
        if row.active_report_id:
            previous = db.get(VoiceBenchmark, row.active_report_id)
            if previous and previous.expires_at > utcnow():
                raise HTTPException(status_code=409, detail='模型组合已有测试正在运行')
        rows.append((row, selected))
    comparison = new_id('CMP')
    reserved = []
    for row, selected in rows:
        report = VoiceBenchmark(tenant_id=context.tenant_id, combination_id=row.id, config_version=row.version,
                                comparison_id=comparison, expires_at=utcnow() + timedelta(hours=1))
        db.add(report)
        db.flush()
        if revoke:
            row.enabled_report_id = row.enabled_by = None
        row.active_report_id = report.id
        reserved.append((row.id, row.version, selected, report.id))
    audit(db, context, 'voice_comparison.started', 'voice_comparison', comparison, {'combinations': list(payload.combinations)})
    db.commit()  # Durable intent before any model I/O; no automatic retry after an unknown result.
    try:
        for identifier, version, selected, report_id in reserved:
            lock_policy_scope(db, context.tenant_id)
            current_actor(db, context, set(roles))
            current = row_for(db, context.tenant_id, identifier)
            require_version(current, version)
            if not revoke and not fresh_report(db, current):
                raise HTTPException(status_code=409, detail='管理员启用状态已失效')
            if current.active_report_id != report_id:
                raise HTTPException(status_code=409, detail='测试请求已失效')
            expected = (fresh_report(db, current).result['configuration'] if not revoke else
                        current.connection.get('configuration') if current.connection.get('status') == 'prepared' else None)
            db.commit()
            try:
                raw = (host_request('probe', selected, payload.samples, expected_configuration=expected) if expected else
                       host_request('probe', selected, payload.samples))
                result = measure(raw, selected, payload.samples)
                if expected and result['configuration'] != expected:
                    raise VoiceError('local_voice_revision_mismatch')
                state = 'passed'
            except Exception as exc:
                code = str(exc) if isinstance(exc, VoiceError) else 'local_model_host_unavailable'
                result = {'error': code if re.fullmatch(r'[a-z_]{1,80}', code) else 'invalid_model_host_report', **BOUNDARY}
                state = 'failed'
            lock_policy_scope(db, context.tenant_id)
            current_actor(db, context, set(roles))
            row = row_for(db, context.tenant_id, identifier)
            report = db.get(VoiceBenchmark, report_id, populate_existing=True)
            if not revoke and not fresh_report(db, row):
                state = 'blocked'
            if row.version != version or row.active_report_id != report_id:
                state = 'stale'
            report.status, report.result = state, result
            report.expires_at = utcnow() + timedelta(hours=24)
            if row.active_report_id == report_id:
                row.active_report_id = None
                if state == 'passed':
                    row.connection = {'status': 'prepared', 'version': version, 'configuration': result['configuration']}
                else:
                    row.connection = {'status': 'unavailable', 'version': version, 'error': result.get('error', 'test_authorization_or_version_changed')}
                if state != 'passed':
                    row.enabled_report_id = row.enabled_by = None
            audit(db, context, 'voice_comparison.completed', 'voice_benchmark', report.id, {'status': state})
            db.commit()
    except HTTPException:
        db.rollback()
        lock_policy_scope(db, context.tenant_id)
        for identifier, _, _, report_id in reserved:
            row = row_for(db, context.tenant_id, identifier)
            report = db.get(VoiceBenchmark, report_id, populate_existing=True)
            if report.status == 'running':
                report.status = 'blocked'
                report.result = {'error': 'test_authorization_or_version_changed', **BOUNDARY}
            if row.active_report_id == report_id:
                row.active_report_id = None
                row.enabled_report_id = row.enabled_by = None
        db.commit()
        raise
    return {'comparison_id': comparison, 'reports': [report_view(db, db.get(VoiceBenchmark, ref))
            for _, _, _, ref in reserved], **BOUNDARY}


@router.post('/compare')
def compare(payload: Compare, context: Context, db: Database):
    return run_comparison(db, context, payload)


@router.post('/{identifier}/enable')
def enable(identifier: str, payload: Version, context: Context, db: Database):
    require_role(context, 'admin')
    lock_policy_scope(db, context.tenant_id)
    current_actor(db, context, {'admin'})
    row = row_for(db, context.tenant_id, identifier)
    require_version(row, payload.expected_version)
    report = db.scalar(select(VoiceBenchmark).where(VoiceBenchmark.tenant_id == context.tenant_id,
                       VoiceBenchmark.combination_id == row.id, VoiceBenchmark.config_version == row.version)
                       .order_by(VoiceBenchmark.created_at.desc()).limit(1))
    if not report or report.status != 'passed' or report.expires_at <= utcnow() or active_report(db, row) or (
            row.connection.get('status') != 'prepared' or row.connection.get('configuration') != report.result.get('configuration')):
        raise HTTPException(status_code=409, detail='请先完成当前版本的真实模型测试')
    row.enabled_report_id, row.enabled_by = report.id, context.actor_id
    audit(db, context, 'voice_combination.enabled', 'voice_combination', row.id, {'report_id': report.id})
    db.commit()
    return view(db, row)


def activity_for(db, context, identifier):
    activity = db.scalar(select(Activity).where(Activity.tenant_id == context.tenant_id,
                         Activity.activity_id == identifier).execution_options(populate_existing=True))
    if not activity:
        raise HTTPException(status_code=404, detail='活动不存在')
    return activity


@router.post('/activities/{activity_id}/bind')
def bind(activity_id: str, payload: Bind, context: Context, db: Database):
    require_role(context, 'operator', 'admin')
    lock_policy_scope(db, context.tenant_id)
    current_actor(db, context, {'operator', 'admin'})
    row = row_for(db, context.tenant_id, payload.combination_id)
    require_version(row, payload.expected_version)
    report = fresh_report(db, row)
    if not report or active_report(db, row):
        raise HTTPException(status_code=409, detail='组合尚未启用、测试已过期、配置已变更或测试正在运行')
    activity = activity_for(db, context, activity_id)
    if activity.status in {'blocked', 'completed'}:
        raise HTTPException(status_code=409, detail='当前活动不可切换模型组合')
    snapshot = {'combination_id': row.id, 'name': row.name, 'version': row.version, 'report_id': report.id,
                'configuration': report.result['configuration'], **BOUNDARY}
    activity.service_snapshot = {**activity.service_snapshot, 'voice_combination': snapshot}
    audit(db, context, 'activity.voice_combination_bound', 'activity', activity_id,
          {'combination_id': row.id, 'version': row.version})
    db.commit()
    return {'activity_id': activity_id, 'snapshot': snapshot, **BOUNDARY}


@router.get('/activities/{activity_id}')
def activity_choice(activity_id: str, context: Context, db: Database):
    activity = activity_for(db, context, activity_id)
    snapshot = activity.service_snapshot.get('voice_combination')
    current = False
    if snapshot:
        row = db.scalar(select(VoiceCombination).where(VoiceCombination.tenant_id == context.tenant_id,
                        VoiceCombination.id == snapshot.get('combination_id')))
        report = fresh_report(db, row) if row else None
        current = bool(report and row.version == snapshot.get('version') and report.id == snapshot.get('report_id'))
    return {'activity_id': activity_id, 'snapshot': snapshot, 'current': current, **BOUNDARY}


@router.post('/activities/{activity_id}/test')
def activity_test(activity_id: str, context: Context, db: Database):
    require_role(context, 'operator', 'admin')
    lock_policy_scope(db, context.tenant_id)
    current_actor(db, context, {'operator', 'admin'})
    activity = activity_for(db, context, activity_id)
    choice = activity_choice(activity_id, context, db)
    if activity.status in {'blocked', 'completed'} or not choice['current']:
        raise HTTPException(status_code=409, detail='活动或模型快照已失效，请重新选择可用组合')
    snapshot = choice['snapshot']
    # Fixed internal probe; no case facts, transcript, customer phone or accounting writes.
    return run_comparison(db, context, Compare(combinations={snapshot['combination_id']: snapshot['version']}, samples=1),
                          roles=('operator', 'admin'), revoke=False)
