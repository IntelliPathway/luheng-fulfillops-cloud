"""Update an existing private SIP lab address without rotating any credentials."""

import argparse
import ipaddress
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

FILES = ("pjsip.conf", "lab.env", "compose.env", "linphone-account.txt")
NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


class UpdateError(ValueError):
    pass


def detect_host_address():
    """Read the default physical Mac interface without probing any remote host."""
    if sys.platform != 'darwin':
        raise UpdateError('auto_address_requires_mac')

    def output(arguments):
        try:
            result = subprocess.run(arguments, capture_output=True, text=True,
                                    timeout=3, check=False)
        except (OSError, subprocess.SubprocessError):
            raise UpdateError('auto_address_tool_failed') from None
        if result.returncode or len(result.stdout) > 4096:
            raise UpdateError('auto_address_tool_failed')
        return result.stdout

    route = output(['/sbin/route', '-n', 'get', 'default'])
    interfaces = re.findall(r'^\s*interface:\s*(\S+)\s*$', route, re.MULTILINE)
    if len(interfaces) != 1 or not re.fullmatch(r'en[0-9]+', interfaces[0]):
        raise UpdateError('auto_address_no_physical_interface')
    value = output(['/usr/sbin/ipconfig', 'getifaddr', interfaces[0]]).strip()
    address = address_value(value)
    if ipaddress.ip_address(address).is_loopback:
        raise UpdateError('auto_address_not_lan')
    return address


def address_value(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise UpdateError("invalid_lab_address") from None
    if address.version != 4 or not any(address in network for network in NETWORKS):
        raise UpdateError("invalid_lab_address")
    return str(address)


def replace_one(text, pattern, replacement):
    text, count = re.subn(pattern, lambda match: replacement, text, flags=re.MULTILINE)
    if count != 1:
        raise UpdateError("ambiguous_or_missing_address_field")
    return text


def revised_contents(contents, address):
    result = dict(contents)
    sip = contents["pjsip.conf"]
    for name in ("external_signaling_address", "external_media_address"):
        sip = replace_one(sip, rf"^{name}=[^\r\n]+$", name + "=" + address)
    if re.search(r"^media_address=", sip, re.MULTILINE):
        sip = replace_one(sip, r"^media_address=[^\r\n]+$", "media_address=" + address)
    else:
        # Upgrade the older generated template that omitted the endpoint media address.
        sip = replace_one(
            sip, r"^direct_media=no$", "direct_media=no\nmedia_address=" + address
        )
    result["pjsip.conf"] = sip
    result["lab.env"] = replace_one(
        contents["lab.env"],
        r"^export SIP_LAB_BIND_ADDRESS=[^\r\n]+$",
        "export SIP_LAB_BIND_ADDRESS=" + address,
    )
    result["compose.env"] = replace_one(
        contents["compose.env"],
        r"^SIP_LAB_BIND_ADDRESS=[^\r\n]+$",
        "SIP_LAB_BIND_ADDRESS=" + address,
    )
    account = replace_one(
        contents["linphone-account.txt"],
        r"^SIP identity: sip:1001@[^\r\n]+$",
        "SIP identity: sip:1001@" + address,
    )
    result["linphone-account.txt"] = replace_one(
        account, r"^Server: sip:[^\r\n]+:5060$", "Server: sip:" + address + ":5060"
    )
    return result


def read_private(path):
    with os.fdopen(
        os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK),
        "r",
        encoding="utf-8",
    ) as source:
        info = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > 32768
        ):
            raise UpdateError("invalid_private_config_file")
        return source.read(32769)


def write_private(path, text):
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
        "w",
        encoding="utf-8",
    ) as target:
        target.write(text)


def update(directory, host_address):
    address = address_value(host_address)
    directory = Path(directory)
    if (
        directory.is_symlink()
        or not directory.is_dir()
        or stat.S_IMODE(directory.stat().st_mode) != 0o700
    ):
        raise UpdateError("invalid_private_config_directory")
    original = {name: read_private(directory / name) for name in FILES}
    revised = revised_contents(original, address)
    changed = [name for name in FILES if revised[name] != original[name]]
    if not changed:
        return False
    staging = Path(tempfile.mkdtemp(prefix=".sip-address-update-", dir=directory))
    keep_backup = False
    published = []
    try:
        for name in changed:
            write_private(staging / (name + ".old"), original[name])
            write_private(staging / (name + ".new"), revised[name])
        try:
            for name in changed:
                os.replace(staging / (name + ".new"), directory / name)
                published.append(name)
        except OSError:
            for name in reversed(published):
                try:
                    os.replace(staging / (name + ".old"), directory / name)
                except OSError:
                    keep_backup = True
            if keep_backup:
                raise UpdateError(
                    "address_update_rollback_failed_backup_retained"
                ) from None
            raise
    finally:
        if not keep_backup:
            shutil.rmtree(staging)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host-address", required=True)
    parser.add_argument("--directory", default="deploy/sip-lab/generated")
    args = parser.parse_args()
    report = {"mode": "sip_lab_address_update", "external_calls": 0}
    try:
        changed = update(args.directory, args.host_address)
        report.update(
            update_state="updated" if changed else "unchanged",
            credentials_preserved=True,
            error=None,
        )
    except UpdateError as error:
        report.update(update_state="failed", error=str(error))
    except (OSError, UnicodeError):
        report.update(
            update_state="failed", error="private_config_read_or_write_failed"
        )
    print(json.dumps(report))
    return 1 if report["update_state"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
