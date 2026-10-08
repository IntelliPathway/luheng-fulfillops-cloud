"""Optional CPU DSP checks: run with local-voice dependencies, no MLX weights required."""
import unittest
from dataclasses import replace
from types import SimpleNamespace

import numpy as np

from app.sip_lab_voice import PHRASE
from app.sip_lab_voice_adapters import QwenASRAdapter, QwenTTSAdapter
from app.sip_lab_voice_config import VoiceSelection


class NativeDSPTests(unittest.TestCase):
    def test_asr_receives_normalized_16k_mono_with_anti_image_filter(self):
        source = (12000 * np.sin(2 * np.pi * 1000 * np.arange(1600) / 8000)).astype('<i2')
        seen = []
        def generate(audio, **kwargs):
            seen.append(audio)
            self.assertEqual(kwargs['language'], 'Chinese')
            self.assertEqual(kwargs['max_tokens'], 128)
            return SimpleNamespace(text=PHRASE)
        engine = QwenASRAdapter(SimpleNamespace(sample_rate=16000, generate=generate))
        self.assertEqual(engine.transcribe(source.tobytes(), lambda: None), PHRASE)
        self.assertEqual(len(seen[0]), 3200)
        self.assertLess(np.max(np.abs(seen[0])), 1)
        self.assertLess(np.max(np.abs(seen[0][::2] - source / 32768)), 0.002)

    def test_tts_downsampling_rejects_aliases_and_resets_streaming_decoder(self):
        resets = []
        # 6 kHz is above phone Nyquist; unfiltered decimation would alias to 2 kHz.
        source = (0.8 * np.sin(2 * np.pi * 6000 * np.arange(24000) / 24000)).astype(np.float32)
        def generate(text, **kwargs):
            self.assertTrue(kwargs['stream'])
            self.assertEqual(kwargs['voice'], 'Vivian')
            self.assertEqual(kwargs['lang_code'], 'Chinese')
            yield SimpleNamespace(sample_rate=24000, audio=source)
        engine = QwenTTSAdapter(SimpleNamespace(generate=generate, speech_tokenizer=SimpleNamespace(
            decoder=SimpleNamespace(reset_streaming_state=lambda: resets.append(True)))), VoiceSelection())
        chunks = list(engine.synthesize(PHRASE, lambda: None))
        self.assertTrue(all(len(chunk) <= 3200 for chunk in chunks))
        output = np.frombuffer(b''.join(chunks), dtype='<i2')
        self.assertEqual(len(output), 8000)
        self.assertLess(float(np.sqrt(np.mean(output[64:-64].astype(float) ** 2))), 100)
        self.assertEqual(resets, [True])

    def test_selected_voice_and_supported_instruction_reach_tts_adapter(self):
        seen = []
        def generate(text, **kwargs):
            seen.append(kwargs)
            yield SimpleNamespace(sample_rate=24000, audio=np.zeros(720, dtype=np.float32))
        model = SimpleNamespace(generate=generate, speech_tokenizer=SimpleNamespace(
            decoder=SimpleNamespace(reset_streaming_state=lambda: None)))
        selection = replace(VoiceSelection(), tts='qwen-tts-1.7b', voice='Ryan', style='calm')
        self.assertTrue(list(QwenTTSAdapter(model, selection).synthesize(PHRASE, lambda: None)))
        self.assertEqual(seen[0]['voice'], 'Ryan')
        self.assertEqual(seen[0]['instruct'], '用平静、清晰、自然的语气说。')


if __name__ == '__main__':
    unittest.main()
