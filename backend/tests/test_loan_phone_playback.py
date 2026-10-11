import socket
import threading
import time

import httpx
import pytest
import uvicorn
from test_loan_phone_dialogue import TOKEN, engine_for

from app import sip_lab_voice
from app.sip_lab_local_voice import create_app
from app.sip_lab_qwen_voice import execute_qwen_turn
from app.sip_lab_voice import LocalVoiceClient


@pytest.mark.parametrize("explicit_interrupt", [True, False, "fully_played"])
def test_hangup_with_unplayed_confirmation_invalidates_draft_without_a_next_turn(monkeypatch, explicit_interrupt):
    engine, _, _ = engine_for(monkeypatch, ["明天还50元", "确认"])
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
    voice = LocalVoiceClient(TOKEN, url=sip_lab_voice.QWEN_HOST_URL, expected_provider="qwen_token_plan")
    thread.start()

    def wait(predicate):
        until = time.monotonic() + 3
        while not predicate():
            assert not voice.failed, voice.error_code
            assert time.monotonic() < until
            time.sleep(0.01)

    def drain_audio():
        while voice.pop():
            pass

    try:
        wait(lambda: server.started)
        voice.start()
        wait(lambda: voice.completed == 1)
        drain_audio()
        voice.submit(b"\0\0" * 1600)
        wait(lambda: voice.completed == 2)
        drain_audio()
        voice.submit(b"\0\0" * 1600)
        wait(lambda: voice.completed == 3)
        assert voice.audio and engine.dialogue.result()["candidate"]["amount_cents"] == 5000
        if explicit_interrupt == "fully_played":
            drain_audio()
        elif explicit_interrupt:
            voice.interrupt()
        voice.close()  # No subsequent ASR can carry the earlier interruption.
        with httpx.Client(
            trust_env=False, base_url=f"http://127.0.0.1:{port}", headers={"Authorization": "Bearer " + TOKEN}
        ) as client:
            wait(lambda: client.get("/lab/status").json()["busy"] is False)
            result = client.get("/lab/dialogue-result")
            assert result.status_code == 200
            if explicit_interrupt == "fully_played":
                assert result.json()["candidate"] == {"amount_cents": 5000, "due_date": "2026-10-13"}
            else:
                assert result.json()["candidate"] is None
    finally:
        voice.close()
        server.should_exit = True
        thread.join(timeout=3)
        listener.close()
