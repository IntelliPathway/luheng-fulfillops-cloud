"""Automatic LAN selection must never silently reuse a stale or unsafe address."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mac_network(monkeypatch, module, route="interface: en0\n", address="192.168.0.199\n"):
    monkeypatch.setattr(sys, "platform", "darwin")

    def run(arguments, **kwargs):
        if arguments == ["/sbin/route", "-n", "get", "default"]:
            return subprocess.CompletedProcess(arguments, 0, route, "")
        if arguments == ["/usr/sbin/ipconfig", "getifaddr", "en0"]:
            return subprocess.CompletedProcess(arguments, 0, address, "")
        pytest.fail("Unexpected network command")

    monkeypatch.setattr(subprocess, "run", run)


def test_detects_default_mac_interface_instead_of_hardcoding_wifi(monkeypatch):
    module = script("sip-lab-address-update")
    monkeypatch.setattr(sys, "platform", "darwin")

    def run(arguments, **kwargs):
        if arguments == ["/sbin/route", "-n", "get", "default"]:
            output = "gateway: 192.168.0.1\n interface: en7\n"
        elif arguments == ["/usr/sbin/ipconfig", "getifaddr", "en7"]:
            output = "192.168.0.199\n"
        else:
            pytest.fail("Incorrect interface selection")
        return subprocess.CompletedProcess(arguments, 0, output, "")

    monkeypatch.setattr(subprocess, "run", run)
    assert module.detect_host_address() == "192.168.0.199"


@pytest.mark.parametrize(
    "route,address",
    [
        ("interface: utun4\n", "192.168.0.199\n"),
        ("interface: lo0\n", "127.0.0.1\n"),
        ("", "192.168.0.199\n"),
        ("interface: en0\ninterface: en1\n", "192.168.0.199\n"),
        ("interface: en0\n", ""),
        ("interface: en0\n", "8.8.8.8\n"),
        ("interface: en0\n", "127.0.0.1\n"),
        ("interface: en0\n", "169.254.1.2\n"),
        ("interface: en0\n", "192.168.0.199\n10.0.0.2\n"),
    ],
)
def test_detection_rejects_unusable_or_ambiguous_networks(monkeypatch, route, address):
    module = script("sip-lab-address-update")
    mac_network(monkeypatch, module, route, address)
    with pytest.raises(module.UpdateError):
        module.detect_host_address()


@pytest.mark.parametrize("failure", ["timeout", "missing", "exit"])
def test_network_tools_fail_without_fallback_or_retry(monkeypatch, failure):
    module = script("sip-lab-address-update")
    monkeypatch.setattr(sys, "platform", "darwin")
    calls = []

    def run(arguments, **kwargs):
        calls.append(arguments)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(arguments, 3)
        if failure == "missing":
            raise FileNotFoundError()
        return subprocess.CompletedProcess(arguments, 1, "", "private diagnostic")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(module.UpdateError):
        module.detect_host_address()
    assert len(calls) == 1


def test_non_mac_detection_does_not_invoke_tools(monkeypatch):
    module = script("sip-lab-address-update")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Mac tools on Linux"))
    with pytest.raises(module.UpdateError):
        module.detect_host_address()


@pytest.mark.parametrize("busy", [False, True])
def test_auto_start_updates_real_configuration_only_when_gateway_idle(tmp_path, monkeypatch, capsys, busy):
    launcher = script("sip-lab")
    updater = script("sip-lab-address-update")
    mac_network(monkeypatch, updater)
    (tmp_path / "deploy/sip-lab").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    for name in ["sip-lab-config", "sip-lab-address-update", "sip-lab-qwen-voice-config", "sip-lab-speech-config"]:
        (tmp_path / "scripts" / (name + ".py")).write_bytes((ROOT / "scripts" / (name + ".py")).read_bytes())
    lab = launcher.Lab(tmp_path)
    lab.configure("192.168.1.4")
    private = tmp_path / "deploy/sip-lab/generated"
    (private / "calls.db").write_bytes(b"synthetic durable journal")
    before = {p.name: p.read_bytes() for p in private.iterdir()}
    helper = launcher.helper
    monkeypatch.setattr(
        launcher, "helper", lambda root, name: updater if name == "sip-lab-address-update" else helper(root, name)
    )
    monkeypatch.setattr(launcher, "Lab", lambda *a, **k: lab)
    monkeypatch.setattr(
        lab,
        "compose",
        lambda *a, **k: subprocess.CompletedProcess(
            a, 0, "asterisk\n" if a[0] == "ps" else ("1 active channels\n" if busy else "0 active channels\n"), ""
        ),
    )
    monkeypatch.setattr(lab, "start", lambda: None)
    monkeypatch.setattr(launcher.sys, "argv", ["sip-lab.py", "start", "--host-address", "auto"])
    assert launcher.main() == (2 if busy else 0)
    after = {p.name: p.read_bytes() for p in private.iterdir() if p.name != ".launcher.lock"}
    for name, content in before.items():
        assert after[name] == (
            content if busy or name not in updater.FILES else content.replace(b"192.168.1.4", b"192.168.0.199")
        )
    output = capsys.readouterr()
    assert "192.168.0.199" in output.out
    assert "SIP_LAB_ARI_PASSWORD" not in output.out


def test_failed_auto_detection_stops_before_configuration_or_service_writes(monkeypatch):
    launcher = script("sip-lab")
    updater = script("sip-lab-address-update")
    mac_network(monkeypatch, updater, address="")
    monkeypatch.setattr(launcher, "helper", lambda *a: updater)
    monkeypatch.setattr(launcher, "Lab", lambda *a, **k: pytest.fail("must not initialize lab"))
    monkeypatch.setattr(launcher.sys, "argv", ["sip-lab.py", "start", "--host-address", "auto"])
    assert launcher.main() == 2
