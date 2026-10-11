"""Address migration must preserve the existing personal lab credentials."""

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def lab(tmp_path):
    directory = tmp_path / "generated"
    script("sip-lab-config").generate(directory, "192.168.0.199")
    (directory / "qwen-voice.env").write_text("export SIP_LAB_QWEN_VOICE_TOKEN=" + "a" * 64 + "\n")
    (directory / "qwen-voice.env").chmod(0o600)
    (directory / "calls.db").write_bytes(b"synthetic journal marker")
    return directory


def snapshot(directory):
    return {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}


def test_updates_all_addresses_preserves_credentials_and_is_idempotent(lab):
    module = script("sip-lab-address-update")
    before = snapshot(lab)
    assert module.update(lab, "192.168.0.200")
    after = snapshot(lab)
    for name, content in before.items():
        expected = content.replace(b"192.168.0.199", b"192.168.0.200") if name in module.FILES else content
        assert after[name] == expected
        if name in module.FILES:
            assert (lab / name).stat().st_mode & 0o777 == 0o600
    assert not module.update(lab, "192.168.0.200")
    assert snapshot(lab) == after
    assert not list(lab.glob(".sip-address-update-*"))


def test_upgrades_legacy_missing_endpoint_media_address(lab):
    module = script("sip-lab-address-update")
    path = lab / "pjsip.conf"
    # Remove specifically the endpoint field, not external_media_address.
    path.write_text(path.read_text().replace("\nmedia_address=192.168.0.199\n", "\n"))
    password_line = next(line for line in path.read_text().splitlines() if line.startswith("password="))
    assert module.update(lab, "192.168.0.200")
    updated = path.read_text()
    assert "\nmedia_address=192.168.0.200\n" in updated
    assert password_line in updated


@pytest.mark.parametrize("address", ["0.0.0.0", "8.8.8.8", "::1", "192.168.0.200\npassword=x"])
def test_invalid_address_leaves_every_file_unchanged(lab, address):
    module = script("sip-lab-address-update")
    before = snapshot(lab)
    with pytest.raises(module.UpdateError, match="invalid_lab_address"):
        module.update(lab, address)
    assert snapshot(lab) == before


@pytest.mark.parametrize("problem", ["duplicate", "missing", "permissions", "symlink", "nonregular"])
def test_ambiguous_or_unsafe_config_leaves_other_files_unchanged(lab, problem):
    module = script("sip-lab-address-update")
    path = lab / "compose.env"
    if problem == "duplicate":
        path.write_text(path.read_text() * 2)
    elif problem == "missing":
        path.unlink()
    elif problem == "permissions":
        path.chmod(0o644)
    elif problem == "nonregular":
        path.unlink()
        os.mkfifo(path, 0o600)
    else:
        outside = lab.parent / "outside.env"
        outside.write_text(path.read_text())
        path.unlink()
        path.symlink_to(outside)
    before = snapshot(lab)
    with pytest.raises((module.UpdateError, OSError)):
        module.update(lab, "192.168.0.200")
    assert snapshot(lab) == before
    assert not list(lab.glob(".sip-address-update-*"))


def test_partial_publish_failure_rolls_back_and_cleans_staging(lab, monkeypatch):
    module = script("sip-lab-address-update")
    before = snapshot(lab)
    real_replace = os.replace
    calls = 0

    def replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic write failure")
        real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", replace)
    with pytest.raises(OSError):
        module.update(lab, "192.168.0.200")
    assert snapshot(lab) == before
    assert not list(lab.glob(".sip-address-update-*"))


def test_rollback_failure_retains_private_backup(lab, monkeypatch):
    module = script("sip-lab-address-update")
    before = (lab / "pjsip.conf").read_bytes()
    real_replace = os.replace
    calls = 0

    def replace(source, destination):
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise OSError("synthetic unavailable filesystem")
        real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", replace)
    with pytest.raises(module.UpdateError, match="rollback_failed_backup_retained"):
        module.update(lab, "192.168.0.200")
    backups = list(lab.glob(".sip-address-update-*"))
    assert len(backups) == 1 and backups[0].stat().st_mode & 0o777 == 0o700
    assert (backups[0] / "pjsip.conf.old").read_bytes() == before
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in backups[0].iterdir())


@pytest.mark.parametrize("address", ["192.168.0.200", "8.8.8.8"])
def test_cli_never_prints_credentials_or_source_config(lab, monkeypatch, capsys, address):
    import json
    import sys

    module = script("sip-lab-address-update")
    password = next(line for line in (lab / "pjsip.conf").read_text().splitlines() if line.startswith("password="))
    monkeypatch.setattr(sys, "argv", ["address-update", "--directory", str(lab), "--host-address", address])
    code = module.main()
    output = capsys.readouterr().out
    assert password.removeprefix("password=") not in output
    report = json.loads(output)
    assert report["external_calls"] == 0
    assert code == (0 if address == "192.168.0.200" else 1)
    assert report["update_state"] == ("updated" if code == 0 else "failed")
