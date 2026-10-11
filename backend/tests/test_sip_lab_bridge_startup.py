"""Media startup must identify failures without exposing mounted credentials."""

import errno
import json
import socket
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from websockets.exceptions import InvalidStatus

from app import sip_lab_bridge as bridge

PRIVATE = "synthetic-private-password-url-and-token"


@pytest.mark.parametrize(
    "failure,exception,stage,code",
    [
        ("voice", FileNotFoundError(PRIVATE), "voice_config", "voice_connection_config_missing"),
        ("voice", PermissionError(PRIVATE), "voice_config", "media_permission_denied"),
        (
            "voice",
            bridge.BridgeError("invalid_voice_connection_config"),
            "voice_config",
            "invalid_voice_connection_config",
        ),
        ("config", bridge.SIPLabError(PRIVATE), "lab_config", "invalid_lab_configuration"),
        ("privilege", PermissionError(PRIVATE), "privilege_drop", "media_permission_denied"),
        ("dns", socket.gaierror(PRIVATE), "gateway_dns", "asterisk_dns_failed"),
        ("journal", sqlite3.OperationalError(PRIVATE), "journal_open", "media_journal_storage_failed"),
        ("bind", OSError(errno.EADDRINUSE, PRIVATE), "udp_bind", "media_udp_port_in_use"),
        (
            "connect",
            InvalidStatus(SimpleNamespace(status_code=401, reason_phrase=PRIVATE)),
            "ari_connect",
            "ari_auth_failed",
        ),
        (
            "connect",
            InvalidStatus(SimpleNamespace(status_code=404, reason_phrase=PRIVATE)),
            "ari_connect",
            "ari_event_handshake_rejected",
        ),
        ("loop", bridge.BridgeError("recovery_cleanup_unconfirmed"), "media_loop", "recovery_cleanup_unconfirmed"),
        ("loop", RuntimeError(PRIVATE), "media_loop", "media_internal_failed"),
    ],
)
def test_main_reports_precise_stage_without_raw_exception_or_credentials(
    monkeypatch, capsys, failure, exception, stage, code
):
    import websockets.sync.client

    closed, connections = [], []

    def step(name, result=None):
        def action(*args, **kwargs):
            if name == failure:
                raise exception
            return result

        return action

    class UDP:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        bind = step("bind")
        setblocking = step("blocking")

    class Events:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def connect(*args, **kwargs):
        connections.append(1)
        return step("connect", Events())()

    journal = SimpleNamespace(unfinished=lambda: [], close=lambda: closed.append(1))
    monkeypatch.setenv("SIP_LAB_MEDIA_MODE", "qwen")
    monkeypatch.setattr(bridge.signal, "signal", lambda *args: None)
    monkeypatch.setattr(bridge, "voice_factory", step("voice", (None, "1003")))
    monkeypatch.setattr(
        bridge, "load_container_config", step("config", SimpleNamespace(instance="a" * 32, password=PRIVATE))
    )
    monkeypatch.setattr(bridge, "drop_media_privileges", step("privilege"))
    monkeypatch.setattr(bridge.socket, "gethostbyname", step("dns", "127.0.0.1"))
    monkeypatch.setattr(bridge, "ResourceJournal", step("journal", journal))
    monkeypatch.setattr(bridge.socket, "socket", lambda *args: UDP())
    monkeypatch.setattr(bridge, "run_loop", step("loop"))
    monkeypatch.setattr(websockets.sync.client, "connect", connect)
    assert bridge.main() == 2
    output = capsys.readouterr().out
    assert PRIVATE not in output and "Authorization" not in output
    report = json.loads(output)
    assert report == {
        "event": "media_lab_unavailable",
        "stage": stage,
        "error": code,
        "media_mode": "qwen",
        "ai_dialogue_ready": False,
    }
    assert len(connections) == (1 if failure in {"connect", "loop"} else 0)
    assert len(closed) == (1 if failure in {"bind", "connect", "loop"} else 0)


def test_dependency_missing_can_be_reported_without_importing_the_missing_dependency(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def importing(name, *args, **kwargs):
        if name.startswith("websockets"):
            raise ModuleNotFoundError(PRIVATE)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    assert bridge.media_failure_code(ModuleNotFoundError(PRIVATE), "dependency_load") == "media_dependency_unavailable"


@pytest.mark.parametrize("status", [401, 403, 500])
def test_ari_request_errors_keep_status_category_without_body(status):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status, text=PRIVATE))) as client:
        expected = "ari_auth_failed" if status in {401, 403} else "ari_request_failed"
        with pytest.raises(bridge.BridgeError, match=expected) as error:
            bridge.request(client, "POST", "http://asterisk:8088/ari/bridges/synthetic")
    assert PRIVATE not in str(error.value)
