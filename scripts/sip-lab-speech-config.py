"""Bounded, data-only product speech settings; no credentials or service actions."""

import errno
import json
import os
import stat
import tempfile
from pathlib import Path

PROFILES = ('baseline', 'warm_female', 'clear_male')
FILENAME = 'speech-settings.json'
MESSAGES = {
    'speech_config_missing': '找不到配置文件。请先在页面下载本机配置，检查实际下载目录/文件名；重下时可能带 (1)，请把实际文件拖入终端指定路径。',
    'speech_config_permission_denied': '无法读取配置文件，检查本机文件及下载目录的读取权限。',
    'speech_config_symlink': '配置是符号链接，请指定实际下载的普通 JSON 文件。',
    'speech_config_not_regular': '配置路径不是普通文件，请选择下载的 JSON 文件。',
    'speech_config_too_large': '配置超过 2 KB；请重新下载千问电话测试音色配置。',
    'speech_config_private_permissions': '已保存的音色文件需要 0600 私有权限。',
    'speech_config_encoding': '配置不是有效 UTF-8 文本，请重新下载原始 JSON 文件。',
    'speech_config_html': '该文件是网页，不是配置 JSON；请点击千问电话测试音色中的“下载本机配置”。',
    'speech_config_json': 'JSON 格式有误；请重新下载原始配置，不要复制页面文字或修改标点。',
    'speech_config_duplicate_field': 'JSON 存在重复字段，请重新下载原始配置。',
    'speech_config_wrong_kind': '这是本地模型组合配置；请在“千问电话测试音色”中点击“下载本机配置”。',
    'speech_config_fields': '配置字段不匹配；只接受千问电话测试音色下载的三字段 JSON。',
    'speech_config_version': '音色配置版本不支持，请从当前页面重新下载。',
    'speech_config_scope': '配置不是个人千问电话测试范围，请下载对应的音色配置。',
    'speech_config_profile': '音色档不支持，只接受 baseline、warm_female 或 clear_male。',
    'speech_config_io': '配置文件读取失败，请检查文件是否已完整下载。',
}


class SpeechConfigError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def error_message(error):
    code = error.code if isinstance(error, SpeechConfigError) else 'speech_config_io'
    return MESSAGES.get(code, MESSAGES['speech_config_io'])


def payload(profile):
    if not isinstance(profile, str) or profile not in PROFILES:
        raise SpeechConfigError('speech_config_profile')
    return {'schema_version': 1, 'usage_scope': 'personal_qwen_phone_lab', 'speech_profile': profile}


def validate(data):
    if isinstance(data, dict) and set(data) == {'schema_version', 'asr', 'llm', 'tts'}:
        raise SpeechConfigError('speech_config_wrong_kind')
    if not isinstance(data, dict) or set(data) != {'schema_version', 'usage_scope', 'speech_profile'}:
        raise SpeechConfigError('speech_config_fields')
    if type(data['schema_version']) is not int or data['schema_version'] != 1:
        raise SpeechConfigError('speech_config_version')
    if data['usage_scope'] != 'personal_qwen_phone_lab':
        raise SpeechConfigError('speech_config_scope')
    return payload(data['speech_profile'])


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise SpeechConfigError('speech_config_duplicate_field')
        result[key] = value
    return result


def read(path, *, private=False):
    try:
        descriptor = os.open(Path(path).expanduser(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise SpeechConfigError('speech_config_not_regular')
            if info.st_size > 2048:
                raise SpeechConfigError('speech_config_too_large')
            if private and stat.S_IMODE(info.st_mode) != 0o600:
                raise SpeechConfigError('speech_config_private_permissions')
        except BaseException:
            os.close(descriptor)
            raise
        # utf-8-sig accepts a normal UTF-8 export as well as a UTF-8 BOM.
        with os.fdopen(descriptor, 'r', encoding='utf-8-sig') as source:
            text = source.read(2049)
            if len(text.encode('utf-8')) > 2048:
                raise SpeechConfigError('speech_config_too_large')
        if text.lstrip().lower().startswith(('<!doctype html', '<html')):
            raise SpeechConfigError('speech_config_html')
        return validate(json.loads(text, object_pairs_hook=pairs))
    except SpeechConfigError:
        raise
    except UnicodeError:
        raise SpeechConfigError('speech_config_encoding') from None
    except json.JSONDecodeError:
        raise SpeechConfigError('speech_config_json') from None
    except OSError as error:
        code = {errno.ENOENT: 'speech_config_missing', errno.EACCES: 'speech_config_permission_denied',
                errno.EPERM: 'speech_config_permission_denied', errno.ELOOP: 'speech_config_symlink'}.get(
                    error.errno, 'speech_config_io')
        raise SpeechConfigError(code) from None


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
