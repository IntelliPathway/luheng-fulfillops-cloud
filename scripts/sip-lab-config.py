#!/usr/bin/env python3
"""Create private local lab credentials; never overwrite or print secrets."""
import argparse
import ipaddress
import os
import secrets
import shutil
from pathlib import Path


def generate(destination, host_address="127.0.0.1"):
    address = ipaddress.ip_address(host_address)
    lab_networks = ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    if address.version != 4 or not any(address in ipaddress.ip_network(network) for network in lab_networks):
        raise ValueError("仅接受本机或私有 LAN IPv4")
    destination = Path(destination)
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    sip_password = secrets.token_hex(24)
    ari_password = secrets.token_hex(24)
    instance = secrets.token_hex(16)
    files = {
        "pjsip.conf": f"""[transport-udp]
type=transport
protocol=udp
bind=0.0.0.0:5060
external_signaling_address={address}
external_media_address={address}
; Docker bridge peers must receive the advertised host media address.
local_net=127.0.0.1/32

[1001]
type=endpoint
transport=transport-udp
context=lab-echo
disallow=all
allow=ulaw,alaw
auth=auth-1001
aors=1001
direct_media=no
force_rport=yes
rewrite_contact=yes
rtp_symmetric=yes

[auth-1001]
type=auth
auth_type=userpass
username=1001
password={sip_password}

[1001]
type=aor
max_contacts=1
remove_existing=yes
qualify_frequency=30
""",
        "ari.conf": f"""[general]
enabled=yes
pretty=no

[repayguard_lab]
type=user
read_only=no
password={ari_password}
""",
        "lab.env": f"""export APP_ENV=development
export ENABLE_SIP_LAB=true
export SIP_LAB_INSTANCE_ID={instance}
export SIP_LAB_ARI_PASSWORD={ari_password}
export SIP_LAB_BIND_ADDRESS={address}
""",
        "compose.env": f"SIP_LAB_BIND_ADDRESS={address}\n",
        "linphone-account.txt": f"SIP identity: sip:1001@{address}\nServer: sip:{address}:5060\nUsername: 1001\nPassword: {sip_password}\nTransport: UDP\nCodecs: PCMU/PCMA\nEncryption: none (isolated lab only)\nEcho extension: 1000\n",
    }
    try:
        for name, content in files.items():
            descriptor = os.open(destination / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w") as output:
                output.write(content)
    except BaseException:
        shutil.rmtree(destination)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="deploy/sip-lab/generated")
    parser.add_argument("--host-address", default="127.0.0.1")
    args = parser.parse_args()
    try:
        generate(args.output, args.host_address)
    except (OSError, ValueError):
        print("生成失败：使用尚不存在的目录及本机/私有 LAN IPv4；已有配置不会被覆盖。")
        return 1
    print("实验室配置已生成；请在本机读取 linphone-account.txt，不上传其中凭据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
