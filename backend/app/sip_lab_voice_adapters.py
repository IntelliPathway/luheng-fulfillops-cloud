"""Stage adapter contracts. Only reviewed, built-in loaders can enter this registry."""
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol

from .sip_lab_voice import VoiceError, validate_reply
from .sip_lab_voice_config import CATALOG, STYLES, VoiceSelection


class ASRAdapter(Protocol):
    def transcribe(self, pcm: bytes, check: Callable[[], None]) -> str: ...


class LLMAdapter(Protocol):
    def reply(self, text: str, history: list, check: Callable[[], None]) -> tuple[str, bool]: ...


class TTSAdapter(Protocol):
    def synthesize(self, text: str, check: Callable[[], None]) -> Iterator[bytes]: ...


SYSTEM = ('你是内部本地语音连通测试助手。仅交流语音测试、复述测试数字和日期。'
          '只输出JSON，且只有reply（最多60字）和end（布尔值）两个字段。'
          '不执行任何工具，不提供金融、合同或催收内容，不索取身份或私人资料。'
          '用户要求结束时end=true。不要输出Markdown或思考过程。')



@dataclass
class QwenASRAdapter:
    model: Any

    def transcribe(self, pcm, check):
        import numpy as np
        from scipy.signal import resample_poly
        check()
        audio = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768
        rate = self.model.sample_rate
        if rate != 16000:
            raise VoiceError('unexpected_asr_sample_rate')
        result = self.model.generate(resample_poly(audio, 2, 1), language='Chinese',
                                         max_tokens=128, temperature=0, verbose=False)
        check()
        text = result.text.strip()
        if not text or len(text) > 120:
            raise VoiceError('invalid_asr_text')
        return text


@dataclass
class QwenLLMAdapter:
    model: Any
    tokenizer: Any

    def reply(self, text, history, check):
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler
        check()
        prompt = self.tokenizer.apply_chat_template(
            [{'role': 'system', 'content': SYSTEM}, *history, {'role': 'user', 'content': text}],
            tokenize=False, add_generation_prompt=True, enable_thinking=False)
        output = ''
        finish = None
        generator = stream_generate(self.model, self.tokenizer, prompt, max_tokens=128,
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


@dataclass
class QwenTTSAdapter:
    model: Any
    selection: VoiceSelection

    def synthesize(self, text, check):
        import numpy as np
        from scipy.signal import resample_poly
        # Fixed preset voice; no voice cloning or arbitrary reference audio.
        check()
        generator = self.model.generate(text, voice=self.selection.voice,
                                            instruct=STYLES[self.selection.style], lang_code='Chinese',
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
            self.model.speech_tokenizer.decoder.reset_streaming_state()


def load_asr(path, selection):
    from mlx_audio.stt.utils import load
    return QwenASRAdapter(load(path, strict=True))


def load_llm(path, selection):
    from mlx_lm import load
    model, tokenizer = load(path, trust_remote_code=False)
    return QwenLLMAdapter(model, tokenizer)


def load_tts(path, selection):
    from mlx_audio.tts.utils import load
    return QwenTTSAdapter(load(path, strict=True), selection)


ADAPTER_LOADERS = MappingProxyType({
    'mlx-qwen-asr': load_asr,
    'mlx-qwen-llm': load_llm,
    'mlx-qwen-tts': load_tts,
})


def build_adapters(selection, manifest):
    selection.validate()
    result = {}
    for kind in ('asr', 'llm', 'tts'):
        spec = CATALOG[getattr(selection, kind)]
        loader = ADAPTER_LOADERS.get(spec.adapter)
        if loader is None:
            raise VoiceError('adapter_not_implemented:' + kind + ':' + spec.adapter)
        result[kind] = loader(manifest[kind]['path'], selection)
    return result
