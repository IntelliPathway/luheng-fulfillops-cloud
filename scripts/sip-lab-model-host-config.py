#!/usr/bin/env python3
"""Add owner-only model-host credentials without replacing lab or voice secrets."""
import os
import secrets
from pathlib import Path


def generate(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir() or any(
            not (directory / name).is_file() for name in ('lab.env', 'local-voice.env')):
        raise ValueError('existing_voice_lab_required')
    destination = directory / 'model-host.env'
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as output:
            output.write('export SIP_LAB_MODEL_HOST_ENABLED=true\n'
                         'export SIP_LAB_MODEL_HOST_TOKEN=' + secrets.token_hex(32) + '\n')
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def main():
    try:
        generate('deploy/sip-lab/generated')
    except (OSError, ValueError):
        print('生成失败：需要已有 SIP 与本地语音配置；已有模型宿主凭据不会被覆盖。')
        return 1
    print('已生成本机私有 model-host.env；不打印凭据、不替换已有配置。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
