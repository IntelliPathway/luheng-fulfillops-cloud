"""Data-only local model selection. Adapter names never load arbitrary Python code."""
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

from .sip_lab_voice import VoiceError, digest


@dataclass(frozen=True)
class ModelSpec:
    kind: str
    model: str
    adapter: str
    available: bool = True


CATALOG = MappingProxyType({
    'qwen-asr-1.7b': ModelSpec('asr', 'mlx-community/Qwen3-ASR-1.7B-8bit', 'mlx-qwen-asr'),
    'qwen-asr-0.6b': ModelSpec('asr', 'mlx-community/Qwen3-ASR-0.6B-8bit', 'mlx-qwen-asr'),
    'qwen-llm-30b-8bit': ModelSpec('llm', 'mlx-community/Qwen3-30B-A3B-Instruct-2507-8bit', 'mlx-qwen-llm'),
    'qwen-llm-30b-4bit': ModelSpec('llm', 'mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit', 'mlx-qwen-llm'),
    'qwen-tts-0.6b': ModelSpec('tts', 'mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit', 'mlx-qwen-tts'),
    'qwen-tts-1.7b': ModelSpec('tts', 'mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit', 'mlx-qwen-tts'),
    'fun-asr-nano': ModelSpec('asr', 'FunAudioLLM/Fun-ASR-Nano-2512', 'funasr-native', False),
    'sensevoice-small': ModelSpec('asr', 'FunAudioLLM/SenseVoiceSmall', 'sensevoice-native', False),
    'cosyvoice3': ModelSpec('tts', 'FunAudioLLM/Fun-CosyVoice3-0.5B-2512', 'cosyvoice-native', False),
})
STYLES = MappingProxyType({'default': None, 'calm': '用平静、清晰、自然的语气说。'})


@dataclass(frozen=True)
class VoiceSelection:
    asr: str = 'qwen-asr-1.7b'
    llm: str = 'qwen-llm-30b-8bit'
    tts: str = 'qwen-tts-0.6b'
    voice: str = 'Vivian'
    style: str = 'default'

    def validate(self, *, require_available=True):
        for kind in ('asr', 'llm', 'tts'):
            alias = getattr(self, kind)
            spec = CATALOG.get(alias) if isinstance(alias, str) else None
            if spec is None or spec.kind != kind:
                raise VoiceError('invalid_model_selection')
            if require_available and not spec.available:
                raise VoiceError('adapter_not_implemented:' + kind + ':' + spec.adapter)
        if self.voice not in ('Vivian', 'Ryan') or not isinstance(self.style, str) or self.style not in STYLES:
            raise VoiceError('invalid_tts_preset')
        if self.style != 'default' and self.tts != 'qwen-tts-1.7b':
            raise VoiceError('tts_style_not_supported')
        return self

    def payload(self):
        return {'schema_version': 1, 'asr': {'model': self.asr}, 'llm': {'model': self.llm},
                'tts': {'model': self.tts, 'voice': self.voice, 'style': self.style}}

    @property
    def config_digest(self):
        return digest(json.dumps(self.payload(), sort_keys=True, separators=(',', ':')))

    @property
    def models(self):
        return {kind: CATALOG[getattr(self, kind)].model for kind in ('asr', 'llm', 'tts')}

    def describe(self):
        return {'config_digest': self.config_digest, 'selection': self.payload(), 'models': self.models,
                'adapters': {kind: CATALOG[getattr(self, kind)].adapter for kind in ('asr', 'llm', 'tts')}}

    @classmethod
    def from_payload(cls, data):
        if not isinstance(data, dict) or set(data) != {'schema_version', 'asr', 'llm', 'tts'}:
            raise VoiceError('invalid_voice_config')
        if type(data['schema_version']) is not int or data['schema_version'] != 1:
            raise VoiceError('invalid_voice_config')
        for kind in ('asr', 'llm', 'tts'):
            row = data[kind]
            allowed = {'model', 'voice', 'style'} if kind == 'tts' else {'model'}
            if not isinstance(row, dict) or 'model' not in row or set(row) - allowed:
                raise VoiceError('invalid_voice_config')
        return cls(data['asr']['model'], data['llm']['model'], data['tts']['model'],
                   data['tts'].get('voice', 'Vivian'), data['tts'].get('style', 'default')).validate()


PROFILES = MappingProxyType({
    'baseline': VoiceSelection(),
    'asr-fast': replace(VoiceSelection(), asr='qwen-asr-0.6b'),
    'llm-4bit': replace(VoiceSelection(), llm='qwen-llm-30b-4bit'),
    'tts-large': replace(VoiceSelection(), tts='qwen-tts-1.7b'),
})


def read_config(path):
    try:
        file = Path(path)
        if file.stat().st_size > 8192:
            raise VoiceError('voice_config_budget_exceeded')
        return VoiceSelection.from_payload(json.loads(file.read_text()))
    except (OSError, ValueError, TypeError) as exc:
        raise VoiceError('invalid_voice_config') from exc


def select_config(args, environment):
    # Explicit CLI selectors take precedence over environment selectors.
    if args.config:
        selection = read_config(args.config)
    elif args.profile:
        selection = PROFILES[args.profile]
    elif environment.get('SIP_LAB_VOICE_CONFIG'):
        if environment.get('SIP_LAB_VOICE_PROFILE'):
            raise VoiceError('ambiguous_voice_configuration')
        selection = read_config(environment['SIP_LAB_VOICE_CONFIG'])
    else:
        profile = environment.get('SIP_LAB_VOICE_PROFILE', 'baseline')
        if profile not in PROFILES:
            raise VoiceError('invalid_voice_profile')
        selection = PROFILES[profile]
    overrides = {kind: getattr(args, kind) for kind in ('asr', 'llm', 'tts', 'voice', 'style')
                 if getattr(args, kind) is not None}
    return replace(selection, **overrides).validate()


def service_configuration(selection, manifest):
    return {**selection.describe(), 'revisions': {kind: row['revision'] for kind, row in manifest.items()}}


def validate_service_configuration(data):
    if not isinstance(data, dict) or set(data) != {'config_digest', 'selection', 'models', 'adapters', 'revisions'}:
        raise VoiceError('invalid_service_configuration')
    selection = VoiceSelection.from_payload(data['selection'])
    description = selection.describe()
    if any(data[key] != value for key, value in description.items()):
        raise VoiceError('invalid_service_configuration')
    revisions = data['revisions']
    if not isinstance(revisions, dict) or set(revisions) != {'asr', 'llm', 'tts'}:
        raise VoiceError('invalid_service_configuration')
    if any(not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{40}', value) for value in revisions.values()):
        raise VoiceError('invalid_service_configuration')
    return data


def catalog_report():
    return {'models': {alias: {'kind': spec.kind, 'model': spec.model, 'adapter': spec.adapter,
                              'adapter_implemented': spec.available, 'device_verified': False}
                       for alias, spec in CATALOG.items()},
            'profiles': {name: selection.payload() for name, selection in PROFILES.items()},
            'voices': ['Vivian', 'Ryan'], 'styles': list(STYLES), 'business_ready': False}
