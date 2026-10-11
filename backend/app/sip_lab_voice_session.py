"""Fixed 1003 inbound lab audio adapter, independent from business dispatch."""
from .sip_lab_bridge import MediaSession, pcmu
from .sip_lab_voice import LocalVoiceClient, Utterance, VoiceError


class VoiceMediaSession(MediaSession):
    def __init__(self, peer, now, transmit_ssrc, token, *, client=None, session_seconds=60):
        super().__init__(peer, now, transmit_ssrc, session_seconds=session_seconds)
        if session_seconds == 180 and getattr(client, 'expected_provider', None) != 'qwen_token_plan':
            raise VoiceError('invalid_voice_session_limit')
        self.tone_index = 50
        self.vad = Utterance()
        self.voice = client or LocalVoiceClient(token)
        self.failed = False
        self.closed = False
        self.voice.start()

    def process_audio(self, pcm):
        if self.closed:
            return
        try:
            for kind, audio in self.vad.feed(pcm):
                if kind == 'speech_start':
                    self.voice.interrupt()
                    self.queue.clear()
                else:
                    self.voice.submit(audio)
        except VoiceError as error:
            self.failed = True
            self.mark_ended('turn_limit' if str(error) == 'voice_turn_budget_exhausted' else 'voice_failed')

    def tick(self, udp, now):
        if self.closed or self.failed or self.voice.finished():
            self.mark_ended('voice_failed' if self.failed or getattr(self.voice, 'failed', False) else 'dialogue_ended')
            return False
        if now >= self.next_send:
            audio = self.voice.pop()
            self.queue.clear()
            if audio:
                self.queue.append(pcmu(audio))
        return super().tick(udp, now)

    def close(self):
        if not self.closed:
            self.closed = True
            self.vad.reset()
            self.voice.close()
            super().close()

    def summary(self):
        cloud = getattr(self.voice, 'expected_provider', None) == 'qwen_token_plan'
        return {**super().summary(), 'cloud_provider_calls': 0, **self.voice.summary(),
                'mode': 'qwen_token_plan_voice_lab' if cloud else 'local_voice_lab',
                'voice_session_failed': self.failed,
                'ai_dialogue_ready': False, 'business_ready': False}
