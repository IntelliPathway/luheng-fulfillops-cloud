#!/usr/bin/env python3
"""Generate a separate host connection token; never store the Token Plan API key."""

import os
import re
import secrets
from pathlib import Path


def generate(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir() or not (directory / "lab.env").is_file():
        raise ValueError("existing_lab_required")
    destination = directory / "qwen-voice.env"
    if destination.exists() or destination.is_symlink():
        if (
            destination.is_symlink()
            or not destination.is_file()
            or destination.stat().st_mode & 0o777 != 0o600
            or destination.stat().st_size > 256
        ):
            raise ValueError("invalid_existing_qwen_voice_token")
        if not re.fullmatch(r"export SIP_LAB_QWEN_VOICE_TOKEN=[a-f0-9]{64}\n", destination.read_text()):
            raise ValueError("invalid_existing_qwen_voice_token")
        return False
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w") as output:
            output.write("export SIP_LAB_QWEN_VOICE_TOKEN=" + secrets.token_hex(32) + "\n")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return True


def main():
    try:
        created = generate("deploy/sip-lab/generated")
    except (OSError, ValueError):
        print("生成失败：需要已有 SIP 实验室及私有凭据目录；不覆盖已有配置。")
        return 1
    print("已生成独立 qwen-voice.env；不保存套餐 API Key。" if created else "已复用原 qwen-voice.env；凭据未改变。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
