"""Bounded, data-only product speech settings; no credentials or service actions."""

import json
import os
import stat
import tempfile
from pathlib import Path

PROFILES = ('baseline', 'warm_female', 'clear_male')
FILENAME = 'speech-settings.json'


def payload(profile):
    if not isinstance(profile, str) or profile not in PROFILES:
        raise ValueError('invalid_speech_settings')
    return {'schema_version': 1, 'usage_scope': 'personal_qwen_phone_lab', 'speech_profile': profile}


def validate(data):
    if (not isinstance(data, dict) or set(data) != {'schema_version', 'usage_scope', 'speech_profile'}
            or type(data.get('schema_version')) is not int or data['schema_version'] != 1
            or data.get('usage_scope') != 'personal_qwen_phone_lab'):
        raise ValueError('invalid_speech_settings')
    return payload(data['speech_profile'])


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('invalid_speech_settings')
        result[key] = value
    return result


def read(path, *, private=False):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'r', encoding='utf-8') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_size > 2048
                or private and stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError('invalid_speech_settings')
        return validate(json.loads(source.read(2049), object_pairs_hook=pairs))


def save(directory, data):
    data = validate(data)
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir() or stat.S_IMODE(directory.stat().st_mode) != 0o700:
        raise ValueError('invalid_private_speech_directory')
    target = directory / FILENAME
    if target.exists() or target.is_symlink():
        read(target, private=True)
    descriptor, name = tempfile.mkstemp(prefix='.speech-settings-', dir=directory)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
            file.write(json.dumps(data, sort_keys=True) + '\n')
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return target
