"""Fixed 1003 inbound lab audio adapter, independent from business dispatch."""
from .sip_lab_bridge import MediaSession, pcmu
from .sip_lab_voice import LocalVoiceClient, Utterance, VoiceError


class VoiceMediaSession(MediaSession):
    def __init__(self, peer, now, transmit_ssrc, token, *, client=None):
        super().__init__(peer, now, transmit_ssrc)
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
        except VoiceError:
            self.failed = True

    def tick(self, udp, now):
        if self.closed or self.failed or self.voice.finished():
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
        return {**super().summary(), **self.voice.summary(), 'mode': 'local_voice_lab',
                'voice_session_failed': self.failed, 'cloud_provider_calls': 0,
                'ai_dialogue_ready': False, 'business_ready': False}
