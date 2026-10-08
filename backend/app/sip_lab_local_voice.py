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
from collections import deque
from pathlib import Path

from .sip_lab import LabConfig
from .sip_lab_voice import (
    GREETING,
    MODELS,
    PHRASE,
    TURN,
    VoiceCancelled,
    VoiceError,
    digest,
    local_token,
    validate_reply,
)

SYSTEM = ('你是内部本地语音连通测试助手。仅交流语音测试、复述测试数字和日期。'
          '只输出JSON，且只有reply（最多60字）和end（布尔值）两个字段。'
          '不执行任何工具，不提供金融、合同或催收内容，不索取身份或私人资料。'
          '用户要求结束时end=true。不要输出Markdown或思考过程。')


def mac_only():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise VoiceError('apple_silicon_required')


def model_directory():
    return Path(os.getenv('SIP_LAB_MODELS_DIRECTORY', str(Path.home() / '.cache/repayguard-voice'))).resolve()


def prepare_models():
    mac_only()
    from huggingface_hub import HfApi, snapshot_download
    root = model_directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest = {}
    for kind, model in MODELS.items():
        revision = HfApi().model_info(model).sha
        if not re.fullmatch(r'[a-f0-9]{40}', revision):
            raise VoiceError('invalid_model_revision')
        destination = root / kind / revision
        snapshot_download(model, revision=revision, local_dir=destination,
                          allow_patterns=['*.json', '*.safetensors', '*.model', '*.txt', '*.jinja', '*.tiktoken'])
        manifest[kind] = {'model': model, 'revision': revision, 'path': str(destination)}
        print(json.dumps({'event': 'local_model_downloaded', 'kind': kind, 'revision': revision}), flush=True)
    temporary = root / 'manifest.pending.json'
    temporary.write_text(json.dumps(manifest))
    temporary.chmod(0o600)
    temporary.replace(root / 'manifest.json')


def load_manifest(root):
    data = json.loads((root / 'manifest.json').read_text())
    if set(data) != set(MODELS):
        raise VoiceError('invalid_model_manifest')
    for kind, model in MODELS.items():
        row = data[kind]
        if row.get('model') != model or not re.fullmatch(r'[a-f0-9]{40}', row.get('revision', '')):
            raise VoiceError('invalid_model_manifest')
        path = Path(row['path']).resolve()
        if path != root / kind / row['revision'] or not (path / 'config.json').is_file():
            raise VoiceError('invalid_local_model_path')
        # ASR upstream enables trust_remote_code internally; deny custom mappings/code
        # in our data-only snapshots before passing them to any loader.
        if any(path.rglob('*.py')):
            raise VoiceError('unsupported_remote_model_code')
        for file in path.rglob('*.json'):
            if file.stat().st_size > 16000000:
                raise VoiceError('model_config_budget_exceeded')
            def contains_mapping(value):
                if isinstance(value, dict):
                    return 'auto_map' in value or any(contains_mapping(item) for item in value.values())
                if isinstance(value, list):
                    return any(contains_mapping(item) for item in value)
                return False
            if contains_mapping(json.loads(file.read_text())):
                raise VoiceError('unsupported_remote_model_code')
    return data


class MLXEngine:
    def __init__(self):
        mac_only()
        # Runtime loads only the already-prepared revision directories, no network fallback.
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['HF_DATASETS_OFFLINE'] = '1'
        from mlx_audio.stt.utils import load as load_asr
        from mlx_audio.tts.utils import load as load_tts
        from mlx_lm import load as load_llm
        self.manifest = load_manifest(model_directory())
        self.asr_model = load_asr(self.manifest['asr']['path'], strict=True)
        self.llm_model, self.tokenizer = load_llm(self.manifest['llm']['path'], trust_remote_code=False)
        self.tts_model = load_tts(self.manifest['tts']['path'], strict=True)
        self.warmed = False

    def asr(self, pcm, check):
        import numpy as np
        from scipy.signal import resample_poly
        check()
        audio = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768
        rate = self.asr_model.sample_rate
        if rate != 16000:
            raise VoiceError('unexpected_asr_sample_rate')
        result = self.asr_model.generate(resample_poly(audio, 2, 1), language='Chinese',
                                         max_tokens=128, temperature=0, verbose=False)
        check()
        text = result.text.strip()
        if not text or len(text) > 120:
            raise VoiceError('invalid_asr_text')
        return text

    def reply(self, text, history, check):
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler
        check()
        prompt = self.tokenizer.apply_chat_template(
            [{'role': 'system', 'content': SYSTEM}, *history, {'role': 'user', 'content': text}],
            tokenize=False, add_generation_prompt=True, enable_thinking=False)
        output = ''
        finish = None
        generator = stream_generate(self.llm_model, self.tokenizer, prompt, max_tokens=128,
                                    sampler=make_sampler(temp=0))
        try:
            for piece in generator:
                check()
                output += piece.text
                finish = piece.finish_reason
                if len(output) > 512:
                    raise VoiceError('llm_output_budget_exceeded')
        finally:
            generator.close()
        check()
        if finish != 'stop':
            raise VoiceError('llm_incomplete')
        try:
            return validate_reply(json.loads(output))
        except (ValueError, TypeError) as exc:
            raise VoiceError('llm_invalid_json') from exc

    def tts(self, text, check):
        import numpy as np
        from scipy.signal import resample_poly
        # Fixed preset voice; no voice cloning or arbitrary reference audio.
        check()
        generator = self.tts_model.generate(text, voice='Vivian', lang_code='Chinese',
                                            max_tokens=160, verbose=False, stream=True,
                                            streaming_interval=0.24)
        total = 0
        try:
            for piece in generator:
                check()
                rate = piece.sample_rate
                if rate != 24000:
                    raise VoiceError('unexpected_tts_sample_rate')
                samples = np.asarray(piece.audio, dtype=np.float32)
                if samples.ndim != 1 or not np.isfinite(samples).all() or len(samples) > 240000:
                    raise VoiceError('invalid_tts_audio')
                # Polyphase filtering prevents aliasing when returning to phone 8 kHz.
                output = resample_poly(samples, 1, 3)
                total += len(output)
                if total > 80000:
                    raise VoiceError('tts_audio_budget_exceeded')
                pcm = (np.clip(output, -1, 1) * 32767).astype('<i2').tobytes()
                for offset in range(0, len(pcm), 3200):
                    check()
                    yield pcm[offset:offset + 3200]
        finally:
            generator.close()
            # A cancelled streaming generator may not reach its upstream reset.
            self.tts_model.speech_tokenizer.decoder.reset_streaming_state()

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
            pcm = b''.join(engine.tts(PHRASE, check))
        text = engine.asr(pcm, check)
        if kind == 'probe' and re.sub(r'[\W_]', '', text) != PHRASE:
            raise VoiceError('asr_probe_phrase_mismatch')
        # Stop requests are deterministic and precede the model.
        if any(word in text for word in ('结束测试', '停止测试', '别再说', '再见')):
            reply, end = '测试结束，再见。', True
        elif any(word in text for word in ('欠款', '本金', '余额', '还款', '合同', '催收')):
            reply, end = '这里只进行语音测试，请说一句测试短句。', False
        else:
            reply, end = engine.reply(text, list(history), check)
            reply, end = validate_reply({'reply': reply, 'end': end})
    count = 0
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
    return {'end': end, 'reply_digest': digest(reply), 'elapsed_ms': round((clock() - start) * 1000),
            'first_audio_ms': round((first - start) * 1000),
            '_history': [{'role': 'user', 'content': text}, {'role': 'assistant', 'content': reply}] if text else []}


def create_app(engine, token):
    from fastapi import FastAPI, WebSocket
    from starlette.websockets import WebSocketDisconnect

    from .sip_lab_voice import TOKEN
    if not TOKEN.fullmatch(token):
        raise VoiceError('invalid_local_voice_token')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    gate = threading.Lock()

    @app.websocket('/lab/voice')
    async def voice(ws: WebSocket):
        supplied = ws.headers.get('authorization', '')
        if not hmac.compare_digest(supplied.encode(), ('Bearer ' + token).encode()) or ws.headers.get('origin'):
            await ws.close(code=1008)
            return
        if not engine.warmed or not gate.acquire(blocking=False):
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
    parser.add_argument('action', choices=['prepare', 'serve', 'probe'])
    parser.add_argument('--acknowledged', action='store_true')
    args = parser.parse_args()
    stage = 'configuration'
    try:
        if not args.acknowledged:
            raise VoiceError('local_voice_acknowledgement_required')
        if args.action == 'prepare':
            stage = 'model_download'
            prepare_models()
            return 0
        LabConfig.from_environment()
        if os.getenv('ENABLE_SIP_LAB_LOCAL_VOICE') != 'true':
            raise VoiceError('local_voice_not_enabled')
        token = local_token()
        if args.action == 'probe':
            stage = 'synthetic_probe'
            return probe_local(token)
        stage = 'model_loading'
        engine = MLXEngine()
        stage = 'warmup'
        engine.warmup()
        import uvicorn
        print(json.dumps({'event': 'local_voice_ready', 'models': MODELS, 'business_ready': False,
                          'revisions': {key: value['revision'] for key, value in engine.manifest.items()}}), flush=True)
        # Native loopback endpoint; Docker Desktop forwards host.docker.internal to host services.
        uvicorn.run(create_app(engine, token), host='127.0.0.1', port=8090, access_log=False,
                    log_level='critical', ws_max_size=100000, ws_max_queue=8)
        return 0
    except Exception as exc:
        print(json.dumps({'event': 'local_voice_unavailable', 'stage': stage,
                          'error': str(exc) if isinstance(exc, VoiceError) else 'local_model_or_service_failed',
                          'business_ready': False}), flush=True)
        return 2


def probe_local(token):
    from .sip_lab_voice import HOST_URL, LocalVoiceClient
    voice = LocalVoiceClient(token, url=HOST_URL)
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
                raise VoiceError('local_voice_probe_failed')
            time.sleep(0.01)
        if voice.completed != 1 or count == 0 or voice.failed:
            raise VoiceError('local_voice_probe_incomplete')
        print(json.dumps({'mode': 'local_synthetic_probe', 'service_chain_completed': True,
                          'asr_phrase_matched': True, 'models': MODELS, 'tts_samples': count,
                          **voice.summary(), 'phone_audio_verified': False, 'business_ready': False}))
        return 0
    finally:
        voice.close()


if __name__ == '__main__':
    raise SystemExit(main())
