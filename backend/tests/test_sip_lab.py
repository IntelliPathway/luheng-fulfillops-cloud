import importlib.util
import os
from pathlib import Path

import httpx
import pytest

from app.sip_lab import CallJournal, LabConfig, SIPLab, SIPLabError

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_SIP_LAB", "true")
    monkeypatch.setenv("SIP_LAB_INSTANCE_ID", "a" * 32)
    monkeypatch.setenv("SIP_LAB_ARI_PASSWORD", "SYNTHETIC_TEST_PASSWORD_" + "x" * 32)
    return LabConfig.from_environment()


def test_dispatch_is_fixed_scope_and_duplicate_queries_without_redial(config, tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            assert request.url.params["endpoint"] == "PJSIP/1001"
            assert request.url.params["context"] == "lab-echo"
            assert request.url.params["extension"] == "1000"
            assert request.url.params["timeout"] == "20"
        return httpx.Response(200, json={"id": request.url.path.rsplit("/", 1)[-1], "state": "Up"})

    journal = CallJournal(tmp_path / "calls.db")
    lab = SIPLab(config, journal, httpx.MockTransport(handler))
    try:
        first = lab.call("stable", acknowledged=True)
        second = lab.call("stable", acknowledged=True)
        assert first["dispatch_state"] == "submitted"
        assert second["channel_state"] == "Up"
        assert [r.method for r in requests] == ["POST", "GET"]
        assert all(str(r.url).startswith("http://127.0.0.1:8088/ari/channels/RG-LAB-") for r in requests)
        assert not first["audio_verified"] and not first["ai_dialogue_ready"] and not first["pstn_enabled"]
        assert config.password not in str(first) + str(second)
    finally:
        lab.close()
        journal.close()


def test_timeout_and_restart_preserve_unknown_without_resend(config, tmp_path):
    requests = []

    def handler(request):
        requests.append(request.method)
        if request.method == "POST":
            raise httpx.ReadTimeout("synthetic possibly submitted request", request=request)
        return httpx.Response(404)

    path = tmp_path / "calls.db"
    journal = CallJournal(path)
    lab = SIPLab(config, journal, httpx.MockTransport(handler))
    assert lab.call("timeout", acknowledged=True)["dispatch_state"] == "unknown"
    lab.close()
    journal.close()
    journal = CallJournal(path)
    lab = SIPLab(config, journal, httpx.MockTransport(handler))
    try:
        result = lab.call("timeout", acknowledged=True)
        assert result["dispatch_state"] == "unknown" and result["channel_state"] == "not_found_or_ended"
        assert not result["retry_allowed"] and requests == ["POST", "GET"]
    finally:
        lab.close()
        journal.close()


def test_crash_after_reservation_never_reissues(config, tmp_path):
    journal = CallJournal(tmp_path / "calls.db")
    journal.reserve(config.instance, "before-send")
    methods = []
    lab = SIPLab(config, journal, httpx.MockTransport(lambda r: (methods.append(r.method), httpx.Response(404))[1]))
    try:
        assert lab.call("before-send", acknowledged=True)["dispatch_state"] == "reserved"
        assert methods == ["GET"]
    finally:
        lab.close()
        journal.close()


@pytest.mark.parametrize("code,expected", [(400, "rejected"), (401, "rejected"), (409, "unknown"), (500, "unknown"), (302, "unknown")])
def test_failed_or_redirected_submission_does_not_retry(config, tmp_path, code, expected):
    requests = []
    lab_journal = CallJournal(tmp_path / "calls.db")

    def handler(request):
        requests.append(request.method)
        return httpx.Response(code, headers={"Location": "https://denied.example.com"})

    lab = SIPLab(config, lab_journal, httpx.MockTransport(handler))
    try:
        assert lab.call("error", acknowledged=True)["dispatch_state"] == expected
        lab.call("error", acknowledged=True)
        assert requests == ["POST", "GET"]
    finally:
        lab.close()
        lab_journal.close()


def test_bad_response_and_unknown_state_cannot_claim_success(config, tmp_path):
    journal = CallJournal(tmp_path / "calls.db")
    lab = SIPLab(config, journal, httpx.MockTransport(lambda r: httpx.Response(200, json={"id": "wrong", "state": "secret-content"})))
    try:
        assert lab.call("mismatch", acknowledged=True)["dispatch_state"] == "unknown"
        assert lab.inspect("mismatch")["channel_state"] == "unknown"
        assert not lab.endpoint_status()["gateway_reachable"]
    finally:
        lab.close()
        journal.close()


def test_acknowledgement_scope_and_journal_quota(config, tmp_path):
    journal = CallJournal(tmp_path / "calls.db")
    lab = SIPLab(config, journal, httpx.MockTransport(lambda r: pytest.fail("no network expected")))
    try:
        with pytest.raises(SIPLabError):
            lab.call("no-ack")
        with pytest.raises(SIPLabError):
            lab.call("sip:someone@external.example", acknowledged=True)
        with pytest.raises(SIPLabError):
            lab.inspect("unknown-key")
        for index in range(20):
            journal.reserve(config.instance, f"slot-{index}")
        with pytest.raises(SIPLabError):
            journal.reserve(config.instance, "over-quota")
        assert not journal.reserve(config.instance, "slot-0")[1]
        assert journal.reserve("b" * 32, "separate-instance")[1]
        assert os.stat(tmp_path / "calls.db").st_mode & 0o777 == 0o600
    finally:
        lab.close()
        journal.close()


@pytest.mark.parametrize("environment,enabled", [("production", "true"), ("development", "false"), ("", "true")])
def test_deployment_defaults_fail_closed(monkeypatch, environment, enabled):
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("ENABLE_SIP_LAB", enabled)
    with pytest.raises(SIPLabError):
        LabConfig.from_environment()


@pytest.mark.parametrize("code,outcome", [(204, "requested"), (404, "not_found_or_ended"),
                                        (500, "unknown"), (302, "unknown")])
def test_hangup_is_scoped_and_never_releases_redial_reservation(config, tmp_path, code, outcome):
    journal = CallJournal(tmp_path / "calls.db")
    row, _ = journal.reserve(config.instance, "stop-test")
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(code)

    lab = SIPLab(config, journal, httpx.MockTransport(handler))
    try:
        with pytest.raises(SIPLabError):
            lab.hangup("another-instance-key")
        assert requests == []
        result = lab.hangup("stop-test")
        assert result["hangup_state"] == outcome and not result["retry_allowed"]
        assert requests[0].method == "DELETE"
        assert requests[0].url.path.endswith(row["channel_id"])
        lab.call("stop-test", acknowledged=True)
        assert [r.method for r in requests] == ["DELETE", "GET"]
    finally:
        lab.close()
        journal.close()


def generator():
    spec = importlib.util.spec_from_file_location("sip_lab_generator", ROOT / "scripts/sip-lab-config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_private_generated_config_is_readable_by_entrypoint_and_never_overwritten(tmp_path):
    output = tmp_path / "generated"
    generator().generate(output)
    assert output.stat().st_mode & 0o777 == 0o700
    for path in output.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600
    pjsip = (output / "pjsip.conf").read_text()
    assert "direct_media=no" in pjsip and "rtp_symmetric=yes" in pjsip and "max_contacts=1" in pjsip
    assert "local_net=127.0.0.1/32" in pjsip
    with pytest.raises(FileExistsError):
        generator().generate(output)
    assert (output / "pjsip.conf").read_text() == pjsip
    dialplan = (ROOT / "deploy/sip-lab/extensions.conf").read_text()
    assert "Echo()" in dialplan and "TIMEOUT(absolute)=60" in dialplan
    assert "Dial(" not in dialplan and "_X" not in dialplan
    compose = (ROOT / "deploy/sip-lab/compose.yml").read_text()
    assert "127.0.0.1:8088:8088" in compose and "generated:/run/sip-lab:ro" in compose
    entrypoint = (ROOT / "deploy/sip-lab/start.sh").read_text()
    assert "-m 600" in entrypoint and "-U asterisk -G asterisk" in entrypoint


@pytest.mark.parametrize("address", ["0.0.0.0", "8.8.8.8", "::1", "224.0.0.1", "bad\naddress",
                                     "169.254.1.1", "192.0.2.1", "240.0.0.1"])
def test_generator_denies_public_unspecified_or_invalid_binding(tmp_path, address):
    with pytest.raises(ValueError):
        generator().generate(tmp_path / "generated", address)
    assert not (tmp_path / "generated").exists()


def test_concurrent_reservations_share_one_dispatch_intent(config, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "calls.db"
    # Initialize before concurrent connections; production journal persists across processes.
    CallJournal(path).close()

    def reserve(_):
        journal = CallJournal(path)
        try:
            return journal.reserve(config.instance, "same-intent")
        finally:
            journal.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(reserve, range(8)))
    assert sum(created for _, created in results) == 1
    assert len({row["channel_id"] for row, _ in results}) == 1
