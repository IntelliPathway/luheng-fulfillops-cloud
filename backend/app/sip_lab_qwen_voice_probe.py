"""Bounded Token Plan TTS -> ASR -> LLM -> TTS experiment; no phone or business writes."""

import argparse
import base64
import getpass
import io
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import uuid
import wave
from contextlib import contextmanager

import httpx

from .sip_lab_qwen_probe import (
    CASES,
    CHAT,
    KEY_ENV,
    MODELS,
    QwenProbeError,
    QwenTokenProbe,
    credential_state,
    normalize_key,
)
from .sip_lab_voice import connect_without_redirects

WS = "wss://token-plan.maas.qianwenaiapi.com/api-ws/v1/inference"
ASR_URL = "https://token-plan.maas.qianwenaiapi.com/api/v1/services/aigc/multimodal-generation/generation"
ASR = "qwen-audio-3.0-asr-flash"
TTS = "qwen-audio-3.0-tts-plus"
VOICE = "longanhuan_v3.6"
RATE = 8000
MAX_AUDIO_BYTES = RATE * 2 * 10
PHRASE = CASES["confirm"][0]


@contextmanager
def websocket(key, timeout):
    with connect_without_redirects(
        WS,
        additional_headers={"Authorization": "Bearer " + key},
        proxy=None,
        open_timeout=min(timeout, 5),
        close_timeout=1,
        max_size=65536,
        max_queue=16,
    ) as connection:
        yield connection


def wav_bytes(pcm):
    if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2 or len(pcm) > MAX_AUDIO_BYTES:
        raise QwenProbeError("invalid_synthetic_pcm")
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(RATE)
        audio.writeframes(pcm)
    return output.getvalue()


def play_audio(pcm):
    if platform.system() != "Darwin":
        raise QwenProbeError("playback_requires_macos")
    # Only an explicitly requested synthetic reply is briefly written for afplay.
    with tempfile.NamedTemporaryFile(suffix=".wav") as audio:
        audio.write(wav_bytes(pcm))
        audio.flush()
        subprocess.run(
            ["/usr/bin/afplay", audio.name],
            check=True,
            timeout=15,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def status(key=None, source=None):
    if key is None:
        key = os.getenv(KEY_ENV, "")
        source = "environment" if key else "none"
    return {
        "mode": "qwen_token_plan_voice_probe",
        "credential_state": credential_state(key),
        "credential_source": source,
        "models": {"asr": ASR, "llm": MODELS[0], "tts": TTS},
        "endpoints": {"asr": ASR_URL, "llm": CHAT, "tts": WS},
        "voice": VOICE,
        "sample_rate": RATE,
        "external_calls": 0,
        "request_state": "not_sent",
        "asr_verified": False,
        "llm_verified": False,
        "tts_verified": False,
        "service_chain_completed": False,
        "phone_audio_verified": False,
        "business_ready": False,
        "billing_verified": False,
        "retry_performed": False,
        "production_blocked": os.getenv("APP_ENV") == "production",
    }


class QwenVoiceProbe:
    def __init__(self, key, *, ws_factory=websocket, transport=None, clock=time.monotonic):
        if os.getenv("APP_ENV") == "production":
            raise QwenProbeError("production_probe_disabled")
        self.key = normalize_key(key)
        self.ws_factory, self.transport, self.clock = ws_factory, transport, clock
        self.deadline = clock() + 120
        self.credential_source = "direct"
        self.metrics = []
        self.external_calls = 0
        self.request_state = "not_sent"
        self.active_stage = None
        self.started = False
        self.passed = False
        self.playback_state = "not_requested"

    def remaining(self):
        value = self.deadline - self.clock()
        if value <= 0:
            raise QwenProbeError("voice_probe_deadline_exceeded")
        return value

    def begin(self, stage):
        self.remaining()
        if self.external_calls >= 4:
            raise QwenProbeError("probe_call_budget_exceeded")
        self.active_stage = stage
        self.external_calls += 1
        self.request_state = "unknown"
        return self.clock()

    @staticmethod
    def command(action, task, payload):
        return json.dumps(
            {"header": {"action": action, "task_id": task, "streaming": "duplex"}, "payload": payload},
            ensure_ascii=False,
        )

    def event(self, connection, task):
        raw = connection.recv(timeout=min(15, self.remaining()))
        if isinstance(raw, bytes):
            return raw
        if not isinstance(raw, str) or len(raw) > 65536:
            raise QwenProbeError("invalid_speech_event")
        try:
            data = json.loads(raw)
            header = data["header"]
            if header["task_id"] != task:
                raise QwenProbeError("speech_task_mismatch")
            if header.get("event") == "task-failed":
                self.request_state = "rejected"
                raise QwenProbeError("speech_task_failed")
            if header.get("event") not in {"task-started", "result-generated", "task-finished"}:
                raise QwenProbeError("unexpected_speech_event")
            return data
        except (ValueError, KeyError, TypeError):
            raise QwenProbeError("invalid_speech_event") from None

    def tts(self, text, stage):
        if not isinstance(text, str) or not 1 <= len(text) <= 60:
            raise QwenProbeError("invalid_synthetic_text")
        started = self.begin(stage)
        task = str(uuid.uuid4())
        first = None
        audio = bytearray()
        with self.ws_factory(self.key, self.remaining()) as connection:
            connection.send(
                self.command(
                    "run-task",
                    task,
                    {
                        "task_group": "audio",
                        "task": "tts",
                        "function": "SpeechSynthesizer",
                        "model": TTS,
                        "parameters": {"text_type": "PlainText", "voice": VOICE, "format": "pcm", "sample_rate": RATE},
                        "input": {},
                    },
                )
            )
            event = self.event(connection, task)
            if not isinstance(event, dict) or event["header"]["event"] != "task-started":
                raise QwenProbeError("speech_task_not_started")
            connection.send(self.command("continue-task", task, {"input": {"text": text}}))
            connection.send(self.command("finish-task", task, {"input": {}}))
            finished = False
            for _ in range(256):
                event = self.event(connection, task)
                if isinstance(event, bytes):
                    if not event:
                        continue
                    if len(audio) + len(event) > MAX_AUDIO_BYTES:
                        raise QwenProbeError("tts_audio_budget_exceeded")
                    if first is None:
                        first = self.clock()
                    audio.extend(event)
                elif event["header"]["event"] == "task-finished":
                    finished = True
                    self.request_state = "completed"
                    break
            if not finished or not audio or len(audio) % 2:
                raise QwenProbeError("tts_incomplete_audio")
        self.metrics.append(
            {
                "stage": stage,
                "model": TTS,
                "elapsed_ms": round((self.clock() - started) * 1000),
                "first_audio_ms": round((first - started) * 1000),
                "samples": len(audio) // 2,
            }
        )
        return bytes(audio)

    def asr(self, pcm):
        encoded = base64.b64encode(wav_bytes(pcm)).decode("ascii")
        started = self.begin("asr")
        payload = {
            "model": ASR,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_audio", "input_audio": {"data": "data:audio/wav;base64," + encoded}}
                        ],
                    }
                ]
            },
            "parameters": {"format": "wav", "sample_rate": str(RATE), "language_hints": ["zh"]},
        }
        with httpx.Client(
            transport=self.transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(min(15, self.remaining()), connect=min(5, self.remaining())),
        ) as client:
            with client.stream(
                "POST",
                ASR_URL,
                headers={"Authorization": "Bearer " + self.key, "X-DashScope-SSE": "disable"},
                json=payload,
            ) as response:
                if response.status_code != 200:
                    self.request_state = "rejected"
                    error = {
                        401: "authentication_failed",
                        403: "model_or_plan_denied",
                        429: "quota_or_rate_limited",
                    }.get(
                        response.status_code,
                        "redirect_denied" if 300 <= response.status_code < 400 else "provider_rejected",
                    )
                    raise QwenProbeError(error)
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=4096):
                    self.remaining()
                    if len(body) + len(chunk) > 65536:
                        raise QwenProbeError("asr_response_too_large")
                    body.extend(chunk)
                self.request_state = "completed"
        try:
            text = json.loads(body)["output"]["text"]
            if not isinstance(text, str) or len(text) > 120:
                raise QwenProbeError("invalid_asr_response")
            if re.sub(r"[\W_]", "", text) != re.sub(r"[\W_]", "", PHRASE):
                raise QwenProbeError("asr_synthetic_phrase_mismatch")
        except (ValueError, KeyError, TypeError):
            raise QwenProbeError("invalid_asr_response") from None
        self.metrics.append(
            {"stage": "asr", "model": ASR, "elapsed_ms": round((self.clock() - started) * 1000), "phrase_matched": True}
        )

    def run(self, *, play=False):
        if self.started:
            raise QwenProbeError("voice_probe_already_run")
        if play and platform.system() != "Darwin":
            raise QwenProbeError("playback_requires_macos")
        self.started = True
        started = self.clock()
        source = self.tts(PHRASE, "source_tts")
        self.asr(source)
        self.remaining()
        if self.external_calls >= 4:
            raise QwenProbeError("probe_call_budget_exceeded")
        self.active_stage = "llm"
        llm = QwenTokenProbe(self.key, transport=self.transport, clock=self.clock)
        try:
            # ASR must match the fixed source before any LLM request; never forward arbitrary text.
            row, reply, _ = llm._call_reply(MODELS[0], "confirm")
        finally:
            self.external_calls += llm.external_calls
            self.request_state = llm.request_state
        self.metrics.append(
            {
                "stage": "llm",
                **{
                    name: row[name]
                    for name in ("requested_model", "reported_model", "elapsed_ms", "usage", "reply_digest", "end")
                },
            }
        )
        self.remaining()
        output = self.tts(reply, "reply_tts")
        self.remaining()
        self.passed = True
        self.elapsed_ms = round((self.clock() - started) * 1000)
        if play:
            self.active_stage = "playback"
            self.playback_state = "failed_or_interrupted"
            play_audio(output)
            self.playback_state = "completed"
        return self.report()

    def report(self, error=None):
        stages = {row["stage"] for row in self.metrics}
        return {
            **status(self.key, self.credential_source),
            "external_calls": self.external_calls,
            "request_state": self.request_state,
            "active_stage": self.active_stage,
            "completed_stages": [row["stage"] for row in self.metrics],
            "stages": self.metrics,
            "service_chain_completed": self.passed,
            "asr_verified": "asr" in stages,
            "llm_verified": "llm" in stages,
            "tts_verified": {"source_tts", "reply_tts"} <= stages,
            "asr_phrase_matched": "asr" in stages,
            "elapsed_ms": getattr(self, "elapsed_ms", None),
            "timing_scope": "synthetic_tts_asr_nonstream_llm_tts_excludes_playback_and_phone",
            "first_token_ms": None,
            "playback_state": self.playback_state,
            "error": error,
        }


def main():
    parser = argparse.ArgumentParser(description="Token Plan 固定合成语音链路，最多4次模型调用，不自动重试。")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    command = sub.add_parser("probe")
    command.add_argument("--acknowledged", action="store_true")
    command.add_argument("--play", action="store_true", help="Mac 播放合成回复，临时 WAV 随后删除")
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(status(), ensure_ascii=False))
        return 0
    key = os.getenv(KEY_ENV, "")
    source = "environment" if key else "none"
    probe = None
    try:
        if not args.acknowledged:
            raise QwenProbeError("probe_acknowledgement_required")
        if os.getenv("APP_ENV") == "production":
            raise QwenProbeError("production_probe_disabled")
        if args.play and platform.system() != "Darwin":
            raise QwenProbeError("playback_requires_macos")
        if not key:
            if not sys.stdin.isatty():
                raise QwenProbeError("credential_missing_use_local_prompt")
            source = "local_prompt"
            key = getpass.getpass("Token Plan 完整 API Key（不回显、不保存）：")
        probe = QwenVoiceProbe(key)
        probe.credential_source = source
        print(json.dumps(probe.run(play=args.play), ensure_ascii=False))
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        error = (
            str(exc)
            if isinstance(exc, QwenProbeError)
            else (
                "probe_cancelled"
                if isinstance(exc, KeyboardInterrupt)
                else "provider_timeout_result_unknown"
                if isinstance(exc, (httpx.TimeoutException, TimeoutError))
                else "voice_probe_unavailable_or_failed"
            )
        )
        result = probe.report(error) if probe else {**status(key, source), "error": error, "completed_stages": []}
        print(json.dumps(result, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
