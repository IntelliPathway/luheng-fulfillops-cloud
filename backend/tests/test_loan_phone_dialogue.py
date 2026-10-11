import importlib
import socket
import threading
import time
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from app.sip_lab_local_voice import create_app
from app.sip_lab_qwen_voice import TokenVoiceEngine, execute_qwen_turn
from app.sip_lab_qwen_voice_probe import ASR_URL
from app.sip_lab_voice import LocalVoiceClient, VoiceCancelled

TODAY = date(2026, 10, 12)
TOKEN = "a" * 64


def module():
    assert importlib.util.find_spec("app.loan_phone_dialogue"), "speech-to-intent adapter is missing"
    return importlib.import_module("app.loan_phone_dialogue")


def test_natural_amount_date_followup_change_and_explicit_confirmation():
    dialogue = module().SyntheticPhoneDialogue(today=TODAY, verified_fixture=True)
    assert dialogue.advance("我明天还五十元")["action"] == "readback_promise"
    assert dialogue.advance("改成后天还60.50元")["reply"] == "我确认一下，2026年10月14日还60.50元，对吗？"
    assert dialogue.result()["candidate"] is None
    assert dialogue.advance("确认这个安排")["action"] == "propose_record_promise"
    assert dialogue.result()["candidate"] == {"amount_cents": 6050, "due_date": "2026-10-14"}
    assert dialogue.advance("再改成30元")["action"] == "readback_promise"
    assert dialogue.result()["candidate"] is None


@pytest.mark.parametrize(
    "text", ["改成后天还30元", "先不记录", "不对", "如果发工资我明天还50元", "转人工", "我已经还了"]
)
def test_post_confirmation_corrections_invalidate_the_old_export(text):
    dialogue = module().SyntheticPhoneDialogue(today=TODAY, verified_fixture=True)
    dialogue.advance("明天还50元")
    dialogue.advance("确认")
    assert dialogue.result()["candidate"]["amount_cents"] == 5000
    dialogue.advance(text)
    assert dialogue.result()["candidate"] is None


def test_date_only_then_amount_and_cancel_never_export_promise():
    dialogue = module().SyntheticPhoneDialogue(today=TODAY, verified_fixture=True)
    assert dialogue.advance("10月15号")["action"] == "ask_amount"
    assert dialogue.advance("先还100元")["action"] == "readback_promise"
    assert dialogue.advance("先不记录")["action"] == "ask_arrangement"
    assert dialogue.advance("确认")["action"] == "clarify"
    assert dialogue.result()["candidate"] is None


@pytest.mark.parametrize(
    "text",
    [
        "如果发工资我明天还50元",
        "明天可能还50元",
        "明天还50元可以吗",
        "不是明天还50元",
        "明天还50元或者后天还60元",
        "还50.555元",
        "还一百万元",
    ],
)
def test_uncertain_or_unsupported_text_disarms_confirmation(text):
    dialogue = module().SyntheticPhoneDialogue(today=TODAY, verified_fixture=True)
    dialogue.advance("明天还50元")
    assert dialogue.advance(text)["action"] == "clarify"
    assert dialogue.advance("确认")["action"] == "clarify"
    assert dialogue.result()["candidate"] is None


@pytest.mark.parametrize("text", ["打错了", "我有异议", "我要投诉", "我失业了", "转人工"])
def test_exception_clears_slots_and_pauses(text):
    dialogue = module().SyntheticPhoneDialogue(today=TODAY, verified_fixture=True)
    dialogue.advance("明天还50元")
    assert dialogue.advance(text)["action"] == "handoff"
    assert dialogue.result()["stage"] == "paused"
    assert dialogue.result()["candidate"] is None


def test_identity_claim_never_verifies_and_interruption_requires_new_readback():
    unverified = module().SyntheticPhoneDialogue(today=TODAY)
    assert unverified.advance("我是本人")["action"] == "request_verification"
    assert unverified.advance("明天还50元")["action"] == "request_verification"
    assert unverified.result()["verification"] == "unverified"
    dialogue = module().SyntheticPhoneDialogue(today=TODAY, verified_fixture=True)
    dialogue.advance("明天还50元")
    dialogue.cancel()
    assert dialogue.advance("确认")["action"] == "clarify"
    assert dialogue.result()["candidate"] is None


def engine_for(monkeypatch, transcripts):
    monkeypatch.setenv("APP_ENV", "test")
    texts = iter(transcripts)
    requests = []

    def handle(request):
        requests.append(request)
        assert str(request.url) == ASR_URL
        return httpx.Response(200, json={"output": {"text": next(texts)}})

    engine = TokenVoiceEngine(
        "sk-sp-SYNTHETIC_PHONE_TEST_KEY_123456789", transport=httpx.MockTransport(handle), synthetic_dialogue=True
    )
    engine.dialogue = module().SyntheticPhoneDialogue(today=TODAY, verified_fixture=True)
    replies = []

    def tts(reply, check):
        check()
        replies.append(reply)
        yield b"\x00\x00" * 1600

    monkeypatch.setattr(engine, "tts", tts)
    return engine, replies, requests


def turn(engine, text_kind="turn", cancel=None, emit=None):
    return execute_qwen_turn(
        engine,
        text_kind,
        b"\x00\x00" * 1600 if text_kind == "turn" else None,
        [],
        cancel or threading.Event(),
        emit or (lambda _: None),
    )


def test_phone_mode_uses_existing_asr_tts_without_llm_and_keeps_two_step_stop(monkeypatch):
    engine, replies, requests = engine_for(monkeypatch, ["明天还50元", "确认", "结束测试", "确认结束测试"])
    assert turn(engine, "greeting")["test_case"] == "greeting"
    assert "合成案件" in replies[0] and "100元" in replies[0] and "AI测试助手" in replies[0]
    assert turn(engine)["test_case"] == "loan_dialogue"
    assert turn(engine)["end"] is False
    assert engine.dialogue.result()["candidate"] == {"amount_cents": 5000, "due_date": "2026-10-13"}
    assert turn(engine)["stop_control"] == "requested"
    assert turn(engine)["end"] is True
    assert engine.dialogue.result()["candidate"] == {"amount_cents": 5000, "due_date": "2026-10-13"}
    assert len(requests) == 4


def test_cancelled_tts_does_not_commit_confirmation(monkeypatch):
    engine, _, _ = engine_for(monkeypatch, ["明天还50元", "确认"])
    turn(engine)
    cancel = threading.Event()
    with pytest.raises(VoiceCancelled):
        turn(engine, cancel=cancel, emit=lambda _: cancel.set())
    engine.cancel_turn_state()
    assert engine.dialogue.result()["candidate"] is None
    assert engine.dialogue.state.stage == "collecting_promise"


def test_result_endpoint_is_private_and_busy_results_are_unavailable(monkeypatch):
    engine, _, _ = engine_for(monkeypatch, ["明天还50元", "确认"])
    turn(engine)
    turn(engine)
    with TestClient(create_app(engine, TOKEN, turn_executor=execute_qwen_turn)) as client:
        assert client.get("/lab/dialogue-result").status_code == 403
        headers = {"Authorization": "Bearer " + TOKEN}
        assert (
            client.get("/lab/dialogue-result", headers={**headers, "Origin": "https://example.com"}).status_code == 403
        )
        result = client.get("/lab/dialogue-result", headers=headers).json()
        assert result["candidate"] == {"amount_cents": 5000, "due_date": "2026-10-13"}
        assert result["business_ready"] is False and result["phone_audio_verified"] is False
        assert "明天还50元" not in str(result)
        with client.websocket_connect("/lab/voice", headers=headers) as ws:
            ws.receive_json()
            assert client.get("/lab/dialogue-result", headers=headers).status_code == 409
            assert engine.dialogue.result()["candidate"] is None


def test_completed_generation_but_interrupted_phone_playback_requires_new_readback(monkeypatch):
    import uvicorn

    from app import sip_lab_voice

    engine, _, _ = engine_for(monkeypatch, ["明天还50元", "确认", "明天还50元", "确认"])
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    monkeypatch.setattr(sip_lab_voice, "QWEN_HOST_URL", f"ws://127.0.0.1:{port}/lab/voice")
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(engine, TOKEN, turn_executor=execute_qwen_turn),
            loop="asyncio",
            http="h11",
            access_log=False,
            log_level="critical",
        )
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    voice = LocalVoiceClient(TOKEN, url=f"ws://127.0.0.1:{port}/lab/voice", expected_provider="qwen_token_plan")

    def wait(predicate):
        until = time.monotonic() + 3
        while not predicate():
            assert not voice.failed, voice.error_code
            assert time.monotonic() < until
            time.sleep(0.01)

    def empty_audio():
        while voice.pop():
            pass

    try:
        wait(lambda: server.started)
        voice.start()
        wait(lambda: voice.completed == 1)
        empty_audio()
        voice.submit(b"\x00\x00" * 1600)
        wait(lambda: voice.completed == 2)
        assert voice.audio  # Generation finished; the phone hasn't played the readback yet.
        voice.interrupt()
        voice.submit(b"\x00\x00" * 1600)
        wait(lambda: voice.completed == 3)
        assert engine.dialogue.result()["candidate"] is None
        assert engine.dialogue.state.stage == "collecting_promise"
        empty_audio()
        voice.submit(b"\x00\x00" * 1600)
        wait(lambda: voice.completed == 4)
        empty_audio()
        voice.submit(b"\x00\x00" * 1600)
        wait(lambda: voice.completed == 5)
        assert engine.dialogue.result()["candidate"]["amount_cents"] == 5000
    finally:
        voice.close()
        server.should_exit = True
        thread.join(timeout=3)
        listener.close()
