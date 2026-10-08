import json
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.sip_lab_local_voice import create_app, load_manifest, main, manifest_file, prepare_models
from app.sip_lab_voice import MODELS, LocalVoiceClient, VoiceError, digest
from app.sip_lab_voice_adapters import build_adapters
from app.sip_lab_voice_config import (
    CATALOG,
    PROFILES,
    VoiceSelection,
    read_config,
    select_config,
    service_configuration,
    validate_service_configuration,
)


def arguments(**overrides):
    return SimpleNamespace(**{'config': None, 'profile': None, 'asr': None, 'llm': None, 'tts': None,
                              'voice': None, 'style': None, **overrides})


def test_default_configuration_preserves_original_baseline():
    assert VoiceSelection().models == MODELS


def manifest(selection, revision='c' * 40):
    return {kind: {'model': model, 'revision': revision, 'path': 'not-an-output-path'}
            for kind, model in selection.models.items()}


def fake_hub(monkeypatch, root):
    calls = []
    hub = ModuleType('huggingface_hub')
    hub.HfApi = lambda: SimpleNamespace(model_info=lambda model: SimpleNamespace(sha=digest(model)[:40]))
    def download(model, *, revision, local_dir, allow_patterns):
        calls.append(model)
        assert '*.py' not in allow_patterns
        local_dir.mkdir(parents=True, exist_ok=True)
        (local_dir / 'config.json').write_text('{}')
    hub.snapshot_download = download
    monkeypatch.setitem(sys.modules, 'huggingface_hub', hub)
    monkeypatch.setattr('app.sip_lab_local_voice.mac_only', lambda: None)
    monkeypatch.setenv('SIP_LAB_MODELS_DIRECTORY', str(root))
    return hub, calls


@pytest.mark.parametrize('profile,changed', [('asr-fast', 'asr'), ('llm-4bit', 'llm'), ('tts-large', 'tts')])
def test_profiles_change_exactly_one_stage_and_have_unique_configuration(profile, changed):
    baseline, selection = PROFILES['baseline'], PROFILES[profile]
    assert {kind for kind in ('asr', 'llm', 'tts') if getattr(selection, kind) != getattr(baseline, kind)} == {changed}
    assert selection.voice == baseline.voice and selection.style == baseline.style
    assert selection.config_digest != baseline.config_digest
    assert VoiceSelection.from_payload(selection.payload()) == selection


def test_checked_in_json_profiles_match_cli_and_allow_single_stage_overrides():
    directory = Path(__file__).resolve().parents[2] / 'deploy/sip-lab/voice-profiles'
    for name, expected in PROFILES.items():
        assert read_config(directory / (name + '.json')) == expected
    selection = select_config(arguments(profile='baseline', asr='qwen-asr-0.6b'), {})
    assert selection == PROFILES['asr-fast']
    selection = select_config(arguments(tts='qwen-tts-1.7b', voice='Ryan', style='calm'), {})
    assert selection.style == 'calm' and selection.voice == 'Ryan'


def test_explicit_selection_precedence_and_ambiguous_environment_are_not_silent(tmp_path):
    config = tmp_path / 'selection.json'
    config.write_text(json.dumps(PROFILES['tts-large'].payload()))
    environment = {'SIP_LAB_VOICE_CONFIG': str(config), 'SIP_LAB_VOICE_PROFILE': 'asr-fast'}
    with pytest.raises(VoiceError, match='ambiguous_voice_configuration'):
        select_config(arguments(), environment)
    assert select_config(arguments(profile='baseline'), environment) == VoiceSelection()
    assert select_config(arguments(config=str(config)), environment) == PROFILES['tts-large']
    assert select_config(arguments(), {'SIP_LAB_VOICE_PROFILE': 'asr-fast'}) == PROFILES['asr-fast']
    with pytest.raises(VoiceError, match='invalid_voice_profile'):
        select_config(arguments(), {'SIP_LAB_VOICE_PROFILE': 'not-a-profile'})


@pytest.mark.parametrize('change', [
    {'url': 'https://outside'}, {'adapter': 'outside.module'}, {'asr': {'model': 'arbitrary/repository'}},
    {'asr': {'model': 'qwen-tts-0.6b'}}, {'schema_version': True},
    {'tts': {'model': 'qwen-tts-0.6b', 'style': 'calm'}},
    {'tts': {'model': 'qwen-tts-1.7b', 'voice': 'cloned-private-voice'}},
])
def test_config_rejects_unreviewed_models_code_urls_and_unsupported_presets(change):
    with pytest.raises(VoiceError):
        VoiceSelection.from_payload({**VoiceSelection().payload(), **change})


@pytest.mark.parametrize('kind,alias', [('asr', 'fun-asr-nano'), ('asr', 'sensevoice-small'), ('tts', 'cosyvoice3')])
def test_reserved_adapters_are_visible_but_never_downloaded_or_loaded(kind, alias, monkeypatch):
    selection = replace(VoiceSelection(), **{kind: alias})
    assert not CATALOG[alias].available
    assert selection.validate(require_available=False) == selection
    monkeypatch.setattr('app.sip_lab_local_voice.mac_only', lambda: pytest.fail('must reject before platform/download'))
    with pytest.raises(VoiceError, match='adapter_not_implemented:' + kind):
        prepare_models(selection)
    with pytest.raises(VoiceError, match='adapter_not_implemented:' + kind):
        build_adapters(selection, manifest(selection))


def test_config_file_has_size_limit_and_errors_do_not_include_contents(tmp_path):
    file = tmp_path / 'voice.json'
    file.write_text('PRIVATE_PAYLOAD' * 1000)
    with pytest.raises(VoiceError, match='voice_config_budget_exceeded'):
        read_config(file)
    file.write_text('PRIVATE_PAYLOAD')
    with pytest.raises(VoiceError, match='invalid_voice_config') as caught:
        read_config(file)
    assert 'PRIVATE_PAYLOAD' not in str(caught.value)


def test_prepare_reuses_unchanged_pins_and_preserves_multiple_configurations(tmp_path, monkeypatch):
    _, calls = fake_hub(monkeypatch, tmp_path)
    baseline, fast = VoiceSelection(), PROFILES['asr-fast']
    prepare_models(baseline)
    old = load_manifest(tmp_path, baseline)
    original = manifest_file(tmp_path, baseline).read_bytes()
    prepare_models(fast)
    new = load_manifest(tmp_path, fast)
    assert len(calls) == 4 and calls[-1] == fast.models['asr']
    assert new['asr']['model'] != old['asr']['model']
    assert new['llm'] == old['llm'] and new['tts'] == old['tts']
    assert manifest_file(tmp_path, baseline).read_bytes() == original
    assert manifest_file(tmp_path, fast).stat().st_mode & 0o777 == 0o600
    prepare_models(baseline)
    prepare_models(fast)
    assert len(calls) == 4
    prepare_models(fast, refresh=True)
    assert len(calls) == 7


def test_failed_download_does_not_replace_prepared_configuration(tmp_path, monkeypatch):
    hub, _ = fake_hub(monkeypatch, tmp_path)
    baseline = VoiceSelection()
    prepare_models(baseline)
    original = manifest_file(tmp_path, baseline).read_bytes()
    hub.snapshot_download = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('PRIVATE_DOWNLOAD_ERROR'))
    with pytest.raises(RuntimeError):
        prepare_models(PROFILES['tts-large'])
    assert manifest_file(tmp_path, baseline).read_bytes() == original
    assert not manifest_file(tmp_path, PROFILES['tts-large']).exists()
    assert load_manifest(tmp_path, baseline)['tts']['model'] == baseline.models['tts']
    with pytest.raises(VoiceError, match='model_configuration_not_prepared'):
        load_manifest(tmp_path, PROFILES['tts-large'])


def test_manifest_configuration_and_cache_paths_cannot_be_substituted(tmp_path, monkeypatch):
    fake_hub(monkeypatch, tmp_path)
    selection = PROFILES['asr-fast']
    prepare_models(selection)
    file = manifest_file(tmp_path, selection)
    data = json.loads(file.read_text())
    data['configuration'] = VoiceSelection().payload()
    file.write_text(json.dumps(data))
    with pytest.raises(VoiceError, match='model_configuration_mismatch'):
        load_manifest(tmp_path, selection)
    data['configuration'] = selection.payload()
    data['models']['asr']['path'] = data['models']['tts']['path']
    file.write_text(json.dumps(data))
    with pytest.raises(VoiceError, match='invalid_local_model_path'):
        load_manifest(tmp_path, selection)


def test_adapter_dispatch_uses_selected_stage_paths_and_fixed_factories(monkeypatch):
    seen = []
    def loader(path, selection):
        seen.append((path, selection.config_digest))
        return object()
    monkeypatch.setattr('app.sip_lab_voice_adapters.ADAPTER_LOADERS', {
        'mlx-qwen-asr': loader, 'mlx-qwen-llm': loader, 'mlx-qwen-tts': loader})
    selection = PROFILES['tts-large']
    rows = {kind: {'path': kind + '-prepared'} for kind in ('asr', 'llm', 'tts')}
    assert set(build_adapters(selection, rows)) == {'asr', 'llm', 'tts'}
    assert seen == [(kind + '-prepared', selection.config_digest) for kind in ('asr', 'llm', 'tts')]


def test_configuration_identity_and_revisions_are_validated_without_paths():
    selection = PROFILES['asr-fast']
    report = service_configuration(selection, manifest(selection))
    assert validate_service_configuration(report) == report
    assert 'not-an-output-path' not in json.dumps(report)
    bad = {**report, 'config_digest': 'a' * 64}
    with pytest.raises(VoiceError, match='invalid_service_configuration'):
        validate_service_configuration(bad)
    bad = {**report, 'revisions': {'asr': 'bad', 'llm': 'c' * 40, 'tts': 'c' * 40}}
    with pytest.raises(VoiceError, match='invalid_service_configuration'):
        validate_service_configuration(bad)


def test_actual_ws_completion_reports_loaded_configuration_and_stage_timings():
    class Engine:
        warmed = True
        configuration = service_configuration(PROFILES['asr-fast'], manifest(PROFILES['asr-fast']))
        def tts(self, text, check):
            check()
            yield b'\0' * 320
    engine = Engine()
    with TestClient(create_app(engine, 'a' * 64)) as client, client.websocket_connect(
            '/lab/voice', headers={'Authorization': 'Bearer ' + 'a' * 64}) as ws:
        ws.send_json({'kind': 'greeting', 'turn': 'b' * 32, 'samples': 0})
        assert ws.receive_json()['kind'] == 'audio'
        assert len(ws.receive_bytes()) == 320
        event = ws.receive_json()
        assert event['configuration'] == engine.configuration
        assert set(event['stage_ms']) == {'source_tts', 'asr', 'llm', 'reply_tts'}
        assert event['stage_ms']['asr'] == event['stage_ms']['llm'] == 0


@pytest.mark.parametrize('reported', ['match', 'different', 'missing', 'old_revision'])
def test_probe_client_verifies_actual_service_instead_of_echoing_requested_config(reported):
    expected = VoiceSelection()
    actual = expected if reported in {'match', 'old_revision'} else PROFILES['asr-fast']
    config = service_configuration(actual, manifest(actual, 'd' * 40 if reported == 'old_revision' else 'c' * 40))
    class Socket:
        turn = None
        def send(self, raw):
            self.turn = json.loads(raw)['turn']
        def recv(self, timeout):
            if self.turn is None:
                raise TimeoutError
            event = {'kind': 'complete', 'turn': self.turn, 'end': False,
                     'reply_digest': digest('测试'), 'elapsed_ms': 10, 'first_audio_ms': 2}
            if reported != 'missing':
                event['configuration'] = config
            self.turn = None
            return json.dumps(event)
    @contextmanager
    def connect(*args, **kwargs):
        yield Socket()
    client = LocalVoiceClient('a' * 64, connect=connect, expected_config_digest=expected.config_digest,
                             expected_revisions={kind: 'c' * 40 for kind in ('asr', 'llm', 'tts')})
    client.start(kind='probe')
    try:
        until = time.monotonic() + 1
        while not client.failed and not client.completed and time.monotonic() < until:
            time.sleep(0.005)
        assert client.failed is (reported != 'match')
        if reported == 'match':
            assert client.completed == 1 and client.summary()['configuration'] == config
        else:
            assert client.completed == 0
            assert client.error_code == ('local_voice_revision_mismatch' if reported == 'old_revision'
                                         else 'local_voice_configuration_mismatch')
    finally:
        client.close()


def test_catalog_and_inspect_are_read_only_without_mac_or_lab_credentials(monkeypatch, capsys):
    monkeypatch.delenv('SIP_LAB_VOICE_CONFIG', raising=False)
    monkeypatch.delenv('SIP_LAB_VOICE_PROFILE', raising=False)
    monkeypatch.setattr('sys.argv', ['local-voice', 'catalog'])
    assert main() == 0
    report = json.loads(capsys.readouterr().out)
    assert not report['models']['cosyvoice3']['adapter_implemented']
    monkeypatch.setattr('sys.argv', ['local-voice', 'inspect', '--profile', 'asr-fast'])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)['config_digest'] == PROFILES['asr-fast'].config_digest
    monkeypatch.setattr('sys.argv', ['local-voice', 'inspect', '--refresh-revisions'])
    assert main() == 2
    assert json.loads(capsys.readouterr().out)['error'] == 'refresh_only_during_prepare'
