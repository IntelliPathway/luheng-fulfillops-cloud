"""Loopback-only controller for cached Mac models; never downloads or dials."""
import argparse
import hmac
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .sip_lab_local_voice import load_manifest, mac_only, model_directory, probe_summary
from .sip_lab_voice import TOKEN, VoiceError, local_token
from .sip_lab_voice_config import PROFILES, VoiceSelection, service_configuration, validate_service_configuration

NATIVE = 'http://127.0.0.1:8090'
HOST = 'http://127.0.0.1:8091'
HOST_STATES = {'idle', 'ready', 'host_busy', 'phone_or_model_busy', 'loading', 'draining',
               'unmanaged', 'native_unreachable', 'native_auth_failed', 'invalid_response'}


def status_report(state):
    return {'state': state, 'source': 'local_model_host', 'business_ready': False,
            'phone_audio_verified': False, 'pstn_enabled': False}


class SelectionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    selection: dict
    samples: int = Field(default=3, strict=True, ge=1, le=3)
    expected_revisions: dict[str, str] | None = None


class ModelHost:
    def __init__(self, token, *, clock=time.monotonic):
        self.token = token
        self.clock = clock
        self.gate = threading.Lock()
        self.child = None
        self.configuration = None
        self.directory = tempfile.TemporaryDirectory(prefix='repayguard-model-host-')

    def native(self, method, path):
        with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client:
            return client.request(method, NATIVE + path, headers={'Authorization': 'Bearer ' + self.token})

    def check(self, selection):
        selection.validate()
        manifest = load_manifest(model_directory(), selection)
        return {'prepared': True, 'configuration': service_configuration(selection, manifest),
                'busy': self.gate.locked(), 'business_ready': False}

    def status(self):
        # Only inspect. Do not acquire the inference gate, drain, load or replace a process.
        managed = self.child is not None and self.child.poll() is None
        try:
            response = self.native('GET', '/lab/status')
        except httpx.TransportError:
            return status_report('loading' if managed else 'host_busy' if self.gate.locked() else 'idle')
        if not managed:
            return status_report('host_busy' if self.gate.locked() else 'unmanaged')
        if response.status_code != 200:
            return status_report('native_auth_failed' if response.status_code == 403 else 'native_unreachable')
        try:
            if len(response.content) > 16000:
                raise ValueError('oversized')
            data = response.json()
            if not isinstance(data, dict) or any(type(data.get(key)) is not bool
                                                 for key in ('busy', 'warmed', 'draining')):
                raise ValueError('invalid flags')
            validate_service_configuration(data.get('configuration'))
        except (ValueError, VoiceError, TypeError):
            return status_report('invalid_response')
        if data['draining']:
            state = 'draining'
        elif data['busy']:
            state = 'phone_or_model_busy'
        elif self.gate.locked():
            state = 'host_busy'
        elif not data['warmed'] or data['configuration'] != self.configuration:
            state = 'loading'
        else:
            state = 'ready'
        return status_report(state)

    def activate(self, selection):
        expected = self.check(selection)['configuration']
        if self.child is not None and self.child.poll() is None:
            state = self.native('GET', '/lab/status')
            if state.status_code != 200:
                raise VoiceError('managed_model_unreachable')
            data = state.json()
            if data.get('busy'):
                raise VoiceError('local_voice_busy')
            if data.get('configuration') == expected and data.get('warmed') and not data.get('draining'):
                return expected
            drained = self.native('POST', '/lab/drain')
            if drained.status_code != 200:
                raise VoiceError('local_voice_busy')
            self.child.terminate()
            try:
                self.child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=10)
        # Do not take over a separately started native service or kill an unrelated process.
        try:
            self.native('GET', '/lab/status')
        except httpx.TransportError:
            pass
        else:
            raise VoiceError('unmanaged_model_service_running')
        config = Path(self.directory.name) / 'selection.json'
        config.write_text(json.dumps(selection.payload()))
        config.chmod(0o600)
        environment = dict(os.environ)
        environment.pop('SIP_LAB_VOICE_PROFILE', None)
        environment['SIP_LAB_VOICE_CONFIG'] = str(config)
        self.child = subprocess.Popen(
            [sys.executable, '-m', 'app.sip_lab_local_voice', 'serve', '--acknowledged'],
            env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        until = self.clock() + 600
        while self.clock() < until:
            if self.child.poll() is not None:
                raise VoiceError('model_load_failed')
            try:
                response = self.native('GET', '/lab/status')
                data = response.json() if response.status_code == 200 else {}
                if data.get('warmed') and data.get('configuration') == expected:
                    self.configuration = expected
                    return expected
            except (httpx.TransportError, ValueError):
                pass
            time.sleep(0.2)
        # Own process only; no test or phone session was admitted before readiness.
        self.child.terminate()
        self.child.wait(timeout=30)
        raise VoiceError('model_load_timeout')

    def probe(self, selection, samples, expected_revisions=None):
        if not self.gate.acquire(blocking=False):
            raise VoiceError('model_host_busy')
        try:
            if expected_revisions is not None and self.check(selection)['configuration']['revisions'] != expected_revisions:
                raise VoiceError('local_voice_revision_mismatch')
            configuration = self.activate(selection)
            if expected_revisions is not None and configuration['revisions'] != expected_revisions:
                raise VoiceError('local_voice_revision_mismatch')
            results = []
            for _ in range(samples):
                result = probe_summary(self.token, selection)
                if result.get('configuration') != configuration:
                    raise VoiceError('local_voice_revision_mismatch')
                results.append(result)
            return {'configuration': configuration, 'samples': results, 'business_ready': False,
                    'phone_audio_verified': False, 'source': 'local_model_host'}
        finally:
            self.gate.release()

    def close(self):
        if self.child is not None and self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=10)
        self.directory.cleanup()


def create_app(manager, token):
    if not TOKEN.fullmatch(token):
        raise VoiceError('invalid_model_host_token')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def authorize(request):
        if request.headers.get('origin') or not hmac.compare_digest(
                request.headers.get('authorization', '').encode(), ('Bearer ' + token).encode()):
            raise HTTPException(status_code=403, detail='local_service_auth_required')

    def execute(request, payload, action):
        authorize(request)
        try:
            selection = VoiceSelection.from_payload(payload.selection).validate()
            if payload.expected_revisions is not None:
                import re
                if set(payload.expected_revisions) != {'asr', 'llm', 'tts'} or any(
                        not re.fullmatch(r'[a-f0-9]{40}', revision) for revision in payload.expected_revisions.values()):
                    raise VoiceError('local_voice_revision_mismatch')
            return manager.check(selection) if action == 'check' else manager.probe(
                selection, payload.samples, payload.expected_revisions)
        except VoiceError as exc:
            code = str(exc).split(':', 1)[0]
            safe = {'local_voice_busy', 'model_host_busy', 'unmanaged_model_service_running',
                    'model_load_failed', 'model_load_timeout', 'managed_model_unreachable',
                    'local_voice_revision_mismatch', 'adapter_not_implemented'}
            raise HTTPException(status_code=409, detail=code if code in safe else 'model_not_prepared_or_invalid') from None
        except Exception:
            raise HTTPException(status_code=503, detail='model_host_unavailable') from None

    @app.post('/lab/check')
    def check(request: Request, payload: SelectionRequest):
        return execute(request, payload, 'check')

    @app.post('/lab/probe')
    def probe(request: Request, payload: SelectionRequest):
        return execute(request, payload, 'probe')

    @app.get('/lab/status')
    def status(request: Request):
        authorize(request)
        try:
            return manager.status()
        except Exception:
            raise HTTPException(status_code=503, detail='model_host_unavailable') from None

    return app


def doctor(profile='baseline'):
    """Read cached manifests and installed metadata only; no inference or downloads."""
    from .sip_lab import LabConfig
    checks = {}
    def check(name, operation):
        try:
            operation()
            checks[name] = 'passed'
        except Exception:
            checks[name] = 'not_ready'
    check('apple_silicon', mac_only)
    checks['python_312'] = 'passed' if sys.version_info[:2] == (3, 12) else 'not_ready'
    check('sip_lab_environment', LabConfig.from_environment)
    checks['local_voice_enabled'] = 'passed' if os.getenv('ENABLE_SIP_LAB_LOCAL_VOICE') == 'true' else 'not_ready'
    check('native_token', local_token)
    checks['host_credentials'] = 'passed' if os.getenv('SIP_LAB_MODEL_HOST_ENABLED') == 'true' and TOKEN.fullmatch(
        os.getenv('SIP_LAB_MODEL_HOST_TOKEN', '')) else 'not_ready'
    for package in ('mlx-audio', 'mlx-lm'):
        check(package, lambda package=package: importlib.metadata.version(package))
    check('cached_model_manifest', lambda: load_manifest(model_directory(), PROFILES[profile]))
    return {'profile': profile, 'checks': checks, 'ready_to_start': all(value == 'passed' for value in checks.values()),
            'business_ready': False, 'phone_audio_verified': False, 'pstn_enabled': False}


def main():
    parser = argparse.ArgumentParser(description='Mac 模型组合宿主（仅本机、已缓存模型）')
    parser.add_argument('--acknowledged', action='store_true')
    parser.add_argument('action', nargs='?', choices=('serve', 'doctor'), default='serve')
    parser.add_argument('--profile', choices=tuple(PROFILES))
    args = parser.parse_args()
    if args.action == 'doctor':
        result = doctor(args.profile or 'baseline')
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['ready_to_start'] else 2
    if args.profile is not None:
        parser.error('--profile 仅用于 doctor；运行时组合由管理员选择')
    if not args.acknowledged:
        raise VoiceError('local_voice_acknowledgement_required')
    mac_only()
    from .sip_lab import LabConfig
    LabConfig.from_environment()
    if os.getenv('ENABLE_SIP_LAB_LOCAL_VOICE') != 'true':
        raise VoiceError('local_voice_not_enabled')
    token = os.getenv('SIP_LAB_MODEL_HOST_TOKEN', '')
    if not TOKEN.fullmatch(token):
        raise VoiceError('invalid_model_host_token')
    manager = ModelHost(local_token())
    try:
        import uvicorn
        uvicorn.run(create_app(manager, token), host='127.0.0.1', port=8091, access_log=False,
                    log_level='critical')
    finally:
        manager.close()


if __name__ == '__main__':
    sys.exit(main())
