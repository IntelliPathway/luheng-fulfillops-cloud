"""Mac-native, single-session MLX test service. Optional imports; no business writes."""
import argparse
import asyncio
import contextlib
import hmac
import json
import os
import platform
import queue
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from .sip_lab import LabConfig
from .sip_lab_voice import (
    GREETING,
    PHRASE,
    TURN,
    VoiceCancelled,
    VoiceError,
    digest,
    local_token,
    validate_reply,
)
from .sip_lab_voice_config import (
    CATALOG,
    PROFILES,
    STYLES,
    VoiceSelection,
    catalog_report,
    select_config,
    service_configuration,
)


def mac_only():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise VoiceError('apple_silicon_required')


def model_directory():
    return Path(os.getenv('SIP_LAB_MODELS_DIRECTORY', str(Path.home() / '.cache/repayguard-voice'))).resolve()


def manifest_file(root, selection):
    return root / ('manifest-' + selection.config_digest + '.json')


def validate_snapshot(root, kind, model, row):
    if not isinstance(row, dict) or set(row) != {'model', 'revision', 'path'}:
        raise VoiceError('invalid_model_manifest')
    if row['model'] != model or not isinstance(row['revision'], str) or not re.fullmatch(
            r'[a-f0-9]{40}', row['revision']):
        raise VoiceError('invalid_model_manifest')
    path = Path(row['path']).resolve()
    expected = {root / kind / digest(model) / row['revision']}
    if model == VoiceSelection().models[kind]:
        expected.add(root / kind / row['revision'])  # Original baseline cache remains compatible.
    if path not in expected or not (path / 'config.json').is_file():
        raise VoiceError('invalid_local_model_path')
    if any(path.rglob('*.py')):
        raise VoiceError('unsupported_remote_model_code')
    def contains_mapping(value):
        if isinstance(value, dict):
            return 'auto_map' in value or any(contains_mapping(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_mapping(item) for item in value)
        return False
    for file in path.rglob('*.json'):
        if file.stat().st_size > 16000000:
            raise VoiceError('model_config_budget_exceeded')
        if contains_mapping(json.loads(file.read_text())):
            raise VoiceError('unsupported_remote_model_code')
    return row


def read_manifest(file):
    with file.open() as source:
        raw = source.read(65537)
    if len(raw) > 65536:
        raise VoiceError('model_manifest_budget_exceeded')
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise VoiceError('invalid_model_manifest')
    return data


def cached_snapshot(root, kind, model):
    candidates = [root / 'manifest.json', *sorted(root.glob('manifest-*.json'))[:32]]
    for file in candidates:
        if not file.is_file():
            continue
        try:
            data = read_manifest(file)
            rows = data['models'] if 'schema_version' in data else data
            row = rows.get(kind)
            if row and row['model'] == model:
                return validate_snapshot(root, kind, model, row)
        except (VoiceError, OSError, ValueError, TypeError, KeyError):
            continue
    return None


def prepare_models(selection=None, *, refresh=False):
    selection = (selection or VoiceSelection()).validate()
    mac_only()
    root = model_directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    existing = manifest_file(root, selection)
    legacy = selection == VoiceSelection() and (root / 'manifest.json').exists()
    if not refresh and (existing.exists() or legacy):
        rows = load_manifest(root, selection)
        print(json.dumps({'event': 'local_models_prepared', 'reused': True,
                          'configuration': service_configuration(selection, rows)}), flush=True)
        return
    from huggingface_hub import HfApi, snapshot_download
    manifest = {}
    for kind, model in selection.models.items():
        cached = None if refresh else cached_snapshot(root, kind, model)
        if cached:
            manifest[kind] = cached
            continue
        revision = HfApi().model_info(model).sha
        if not re.fullmatch(r'[a-f0-9]{40}', revision):
            raise VoiceError('invalid_model_revision')
        destination = root / kind / digest(model) / revision
        snapshot_download(model, revision=revision, local_dir=destination,
                          allow_patterns=['*.json', '*.safetensors', '*.model', '*.txt', '*.jinja', '*.tiktoken'])
        manifest[kind] = {'model': model, 'revision': revision, 'path': str(destination)}
        validate_snapshot(root, kind, model, manifest[kind])
        print(json.dumps({'event': 'local_model_downloaded', 'kind': kind, 'revision': revision}), flush=True)
    temporary = root / ('.manifest-' + uuid.uuid4().hex + '.pending')
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump({'schema_version': 1, 'configuration': selection.payload(), 'models': manifest}, output)
        temporary.replace(existing)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({'event': 'local_models_prepared', 'reused': False,
                      'configuration': service_configuration(selection, manifest)}), flush=True)


def load_manifest(root, selection=None):
    selection = (selection or VoiceSelection()).validate()
    root = Path(root).resolve()
    file = manifest_file(root, selection)
    if file.exists():
        envelope = read_manifest(file)
        if set(envelope) != {'schema_version', 'configuration', 'models'} or type(
                envelope['schema_version']) is not int or envelope['schema_version'] != 1:
            raise VoiceError('invalid_model_manifest')
        if envelope['configuration'] != selection.payload():
            raise VoiceError('model_configuration_mismatch')
        data = envelope['models']
    elif selection == VoiceSelection() and (root / 'manifest.json').exists():
        data = read_manifest(root / 'manifest.json')
    else:
        raise VoiceError('model_configuration_not_prepared')
    if not isinstance(data, dict) or set(data) != {'asr', 'llm', 'tts'}:
        raise VoiceError('invalid_model_manifest')
    for kind, model in selection.models.items():
        validate_snapshot(root, kind, model, data[kind])
    return data


class MLXEngine:
    def __init__(self, selection=None):
        mac_only()
        self.selection = (selection or VoiceSelection()).validate()
        # Runtime loads prepared revision directories only, with no network fallback.
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['HF_DATASETS_OFFLINE'] = '1'
        self.manifest = load_manifest(model_directory(), self.selection)
        from .sip_lab_voice_adapters import build_adapters
        self.adapters = build_adapters(self.selection, self.manifest)
        self.configuration = service_configuration(self.selection, self.manifest)
        self.warmed = False

    def asr(self, pcm, check):
        return self.adapters['asr'].transcribe(pcm, check)

    def reply(self, text, history, check):
        return self.adapters['llm'].reply(text, history, check)

    def tts(self, text, check):
        return self.adapters['tts'].synthesize(text, check)

    def warmup(self):
        until = time.monotonic() + 120
        def check():
            if time.monotonic() >= until:
                raise VoiceError('warmup_deadline_exceeded')
        source = b''.join(self.tts(PHRASE, check))
        self.asr(source, check)
        self.reply(PHRASE, [], check)
        self.warmed = True


def execute_turn(engine, kind, pcm, history, cancel, emit, clock=time.monotonic):
    start = clock()
    first = None
    stage_ms = {'source_tts': 0, 'asr': 0, 'llm': 0, 'reply_tts': 0}
    until = start + 20
    def check():
        if cancel.is_set():
            raise VoiceCancelled('turn_cancelled')
        if clock() >= until:
            raise VoiceError('turn_deadline_exceeded')
    check()
    if kind == 'greeting':
        text, reply, end = None, GREETING, False
    else:
        if kind == 'probe':
            stage_start = clock()
            pcm = b''.join(engine.tts(PHRASE, check))
            stage_ms['source_tts'] = round((clock() - stage_start) * 1000)
        stage_start = clock()
        text = engine.asr(pcm, check)
        stage_ms['asr'] = round((clock() - stage_start) * 1000)
        if kind == 'probe' and re.sub(r'[\W_]', '', text) != PHRASE:
            raise VoiceError('asr_probe_phrase_mismatch')
        # Stop requests are deterministic and precede the model.
        if any(word in text for word in ('结束测试', '停止测试', '别再说', '再见')):
            reply, end = '测试结束，再见。', True
        elif any(word in text for word in ('欠款', '本金', '余额', '还款', '合同', '催收')):
            reply, end = '这里只进行语音测试，请说一句测试短句。', False
        else:
            stage_start = clock()
            reply, end = engine.reply(text, list(history), check)
            reply, end = validate_reply({'reply': reply, 'end': end})
            stage_ms['llm'] = round((clock() - stage_start) * 1000)
    count = 0
    stage_start = clock()
    for audio in engine.tts(reply, check):
        check()
        if not isinstance(audio, bytes) or not audio or len(audio) % 2 or len(audio) > 3200:
            raise VoiceError('invalid_tts_chunk')
        count += len(audio) // 2
        if count > 80000:
            raise VoiceError('tts_audio_budget_exceeded')
        if first is None:
            first = clock()
        emit(audio)
    check()
    if not count:
        raise VoiceError('empty_tts_audio')
    stage_ms['reply_tts'] = round((clock() - stage_start) * 1000)
    return {'end': end, 'reply_digest': digest(reply), 'elapsed_ms': round((clock() - start) * 1000),
            'first_audio_ms': round((first - start) * 1000), 'stage_ms': stage_ms,
            '_history': [{'role': 'user', 'content': text}, {'role': 'assistant', 'content': reply}] if text else []}


def create_app(engine, token):
    from fastapi import FastAPI, WebSocket
    from starlette.websockets import WebSocketDisconnect

    from .sip_lab_voice import TOKEN
    if not TOKEN.fullmatch(token):
        raise VoiceError('invalid_local_voice_token')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    gate = threading.Lock()
    draining = threading.Event()

    from fastapi import HTTPException, Request

    def authorize(request):
        supplied = request.headers.get('authorization', '')
        if request.headers.get('origin') or not hmac.compare_digest(
                supplied.encode(), ('Bearer ' + token).encode()):
            raise HTTPException(status_code=403, detail='local_service_auth_required')

    @app.get('/lab/status')
    def local_status(request: Request):
        authorize(request)
        return {'warmed': engine.warmed, 'busy': gate.locked(), 'draining': draining.is_set(),
                'configuration': getattr(engine, 'configuration', None), 'business_ready': False}

    @app.post('/lab/drain')
    def drain(request: Request):
        authorize(request)
        if not gate.acquire(blocking=False):
            raise HTTPException(status_code=409, detail='local_voice_busy')
        try:
            draining.set()
            return {'draining': True, 'business_ready': False}
        finally:
            gate.release()

    @app.websocket('/lab/voice')
    async def voice(ws: WebSocket):
        supplied = ws.headers.get('authorization', '')
        if not hmac.compare_digest(supplied.encode(), ('Bearer ' + token).encode()) or ws.headers.get('origin'):
            await ws.close(code=1008)
            return
        if draining.is_set() or not engine.warmed or not gate.acquire(blocking=False):
            await ws.close(code=1013)
            return
        if draining.is_set():
            gate.release()
            await ws.close(code=1013)
            return
        worker = None
        cancel = threading.Event()
        outgoing = queue.Queue(maxsize=8)
        outcome = {}
        active = waiting = None
        turns = 0
        receive = None
        history = deque(maxlen=8)
        until = time.monotonic() + 65

        def run(kind, pcm):
            def emit(audio):
                while not cancel.is_set():
                    try:
                        outgoing.put(audio, timeout=0.05)
                        return
                    except queue.Full:
                        pass
                raise VoiceCancelled('turn_cancelled')
            try:
                outcome['complete'] = execute_turn(engine, kind, pcm, history, cancel, emit)
            except VoiceCancelled:
                outcome['cancelled'] = True
            except Exception:
                # Do not retain model text, audio, exception bodies or paths in diagnostics.
                outcome['error'] = True

        def launch(kind, pcm):
            nonlocal worker
            cancel.clear()
            outcome.clear()
            worker = threading.Thread(target=run, args=(kind, pcm), daemon=True, name='mlx-test-turn')
            worker.start()

        try:
            await ws.accept()
            receive = asyncio.create_task(ws.receive())
            while time.monotonic() < until:
                done, _ = await asyncio.wait([receive], timeout=0.01)
                if done:
                    message = receive.result()
                    if message['type'] == 'websocket.disconnect':
                        break
                    if message.get('bytes') is not None:
                        pcm = message['bytes']
                        if waiting is None or len(pcm) != waiting['samples'] * 2:
                            raise VoiceError('invalid_turn_audio')
                        launch('turn', pcm)
                        waiting = None
                    else:
                        raw = message.get('text', '')
                        if len(raw) > 256:
                            raise VoiceError('invalid_voice_command')
                        command = json.loads(raw)
                        if not isinstance(command, dict) or not TURN.fullmatch(command.get('turn', '')):
                            raise VoiceError('invalid_turn_identifier')
                        if command.get('kind') == 'cancel':
                            if command['turn'] != active:
                                raise VoiceError('cancel_turn_mismatch')
                            cancel.set()
                            if waiting:
                                waiting = None
                                await ws.send_json({'kind': 'cancelled', 'turn': active})
                                active = None
                        else:
                            if active or turns >= 8 or set(command) != {'kind', 'turn', 'samples'}:
                                raise VoiceError('voice_session_busy_or_exhausted')
                            kind, samples = command['kind'], command['samples']
                            if type(samples) is not int or (kind == 'turn' and not 1600 <= samples <= 48000):
                                raise VoiceError('invalid_voice_samples')
                            if kind not in {'turn', 'greeting', 'probe'} or (kind != 'turn' and samples != 0):
                                raise VoiceError('invalid_voice_kind')
                            active = command['turn']
                            turns += 1
                            if kind == 'turn':
                                waiting = command
                            else:
                                launch(kind, None)
                    receive = asyncio.create_task(ws.receive())
                for _ in range(4):
                    try:
                        audio = outgoing.get_nowait()
                    except queue.Empty:
                        break
                    if not cancel.is_set():
                        # One writer keeps every audio header/binary pair adjacent.
                        await ws.send_json({'kind': 'audio', 'turn': active, 'samples': len(audio) // 2})
                        await ws.send_bytes(audio)
                if worker and not worker.is_alive() and outgoing.empty():
                    if cancel.is_set() or outcome.get('cancelled'):
                        event = {'kind': 'cancelled', 'turn': active}
                    elif outcome.get('error'):
                        event = {'kind': 'error', 'turn': active}
                    else:
                        complete = outcome['complete']
                        history.extend(complete.pop('_history'))
                        event = {'kind': 'complete', 'turn': active, **complete}
                        if hasattr(engine, 'configuration'):
                            event['configuration'] = engine.configuration
                    await ws.send_json(event)
                    worker = None
                    active = None
                    if event['kind'] == 'error':
                        break
        except (WebSocketDisconnect, VoiceError, ValueError, TypeError, KeyError, RuntimeError):
            pass
        finally:
            cancel.set()
            if receive:
                receive.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await receive
            with contextlib.suppress(Exception):
                await ws.close()
            # A cancelled MLX kernel may still be running. Keep the gate until it exits.
            if worker and worker.is_alive():
                def release_after_worker():
                    worker.join()
                    history.clear()
                    gate.release()
                threading.Thread(target=release_after_worker, daemon=True).start()
            else:
                history.clear()
                gate.release()
    return app


def main():
    parser = argparse.ArgumentParser(description='Apple Silicon 本地语音测试服务')
    parser.add_argument('action', choices=['catalog', 'inspect', 'prepare', 'serve', 'probe'])
    parser.add_argument('--acknowledged', action='store_true')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--profile', choices=list(PROFILES))
    group.add_argument('--config')
    for kind in ('asr', 'llm', 'tts'):
        parser.add_argument('--' + kind, choices=[alias for alias, spec in CATALOG.items() if spec.kind == kind])
    parser.add_argument('--voice', choices=['Vivian', 'Ryan'])
    parser.add_argument('--style', choices=list(STYLES))
    parser.add_argument('--refresh-revisions', action='store_true')
    args = parser.parse_args()
    stage = 'configuration'
    try:
        if args.action == 'catalog':
            print(json.dumps(catalog_report(), ensure_ascii=False))
            return 0
        selection = select_config(args, os.environ)
        if args.refresh_revisions and args.action != 'prepare':
            raise VoiceError('refresh_only_during_prepare')
        if args.action == 'inspect':
            report = {'mode': 'local_voice_configuration', **selection.describe(), 'prepared': False,
                      'business_ready': False}
            try:
                rows = load_manifest(model_directory(), selection)
                report.update(prepared=True, revisions={kind: row['revision'] for kind, row in rows.items()})
            except (VoiceError, OSError, ValueError, TypeError, KeyError) as exc:
                report['preparation_error'] = str(exc) if isinstance(exc, VoiceError) else 'invalid_model_manifest'
            print(json.dumps(report))
            return 0
        if not args.acknowledged:
            raise VoiceError('local_voice_acknowledgement_required')
        if args.action == 'prepare':
            stage = 'model_download'
            prepare_models(selection, refresh=args.refresh_revisions)
            return 0
        LabConfig.from_environment()
        if os.getenv('ENABLE_SIP_LAB_LOCAL_VOICE') != 'true':
            raise VoiceError('local_voice_not_enabled')
        token = local_token()
        if args.action == 'probe':
            stage = 'synthetic_probe'
            return probe_local(token, selection)
        stage = 'model_loading'
        engine = MLXEngine(selection)
        stage = 'warmup'
        engine.warmup()
        import uvicorn
        print(json.dumps({'event': 'local_voice_ready', 'configuration': engine.configuration,
                          'business_ready': False}), flush=True)
        # Native loopback endpoint; Docker Desktop forwards host.docker.internal to host services.
        uvicorn.run(create_app(engine, token), host='127.0.0.1', port=8090, access_log=False,
                    log_level='critical', ws_max_size=100000, ws_max_queue=8)
        return 0
    except Exception as exc:
        print(json.dumps({'event': 'local_voice_unavailable', 'stage': stage,
                          'error': str(exc) if isinstance(exc, VoiceError) else 'local_model_or_service_failed',
                          'business_ready': False}), flush=True)
        return 2


def probe_summary(token, selection=None):
    from .sip_lab_voice import HOST_URL, LocalVoiceClient
    selection = (selection or VoiceSelection()).validate()
    rows = load_manifest(model_directory(), selection)
    voice = LocalVoiceClient(token, url=HOST_URL, expected_config_digest=selection.config_digest,
                             expected_revisions={kind: row['revision'] for kind, row in rows.items()})
    count = 0
    voice.start(kind='probe')
    try:
        until = time.monotonic() + 25
        while time.monotonic() < until:
            audio = voice.pop()
            if audio:
                count += len(audio) // 2
            elif voice.completed:
                break
            if voice.failed:
                raise VoiceError(voice.error_code or 'local_voice_probe_failed')
            time.sleep(0.01)
        if voice.completed != 1 or count == 0 or voice.failed:
            raise VoiceError('local_voice_probe_incomplete')
        return {'mode': 'local_synthetic_probe', 'service_chain_completed': True,
                          'asr_phrase_matched': True, 'tts_samples': count,
                          **voice.summary(), 'phone_audio_verified': False, 'business_ready': False}
    finally:
        voice.close()


def probe_local(token, selection=None):
    print(json.dumps(probe_summary(token, selection)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
