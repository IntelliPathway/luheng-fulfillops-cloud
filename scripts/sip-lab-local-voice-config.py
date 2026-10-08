#!/usr/bin/env python3
"""Add separate owner-only local voice credentials without replacing SIP config."""
import os
import secrets
from pathlib import Path


def generate(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir() or not (directory / 'lab.env').is_file():
        raise ValueError('existing_lab_required')
    destination = directory / 'local-voice.env'
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write('export ENABLE_SIP_LAB_LOCAL_VOICE=true\n'
                         'export SIP_LAB_LOCAL_VOICE_TOKEN=' + secrets.token_hex(32) + '\n')
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def main():
    try:
        generate('deploy/sip-lab/generated')
    except (OSError, ValueError):
        print('生成失败：需要已有 SIP 实验室；已有本地语音凭据不会被覆盖。')
        return 1
    print('已生成本机私有 local-voice.env；不打印凭据、不替换 SIP 配置。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
