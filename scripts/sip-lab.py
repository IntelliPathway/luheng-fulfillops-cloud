"""One foreground entry for the fixed personal Qwen/Linphone lab. No saved API key."""

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import selectors
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABEL = 'org.repayguard.sip-lab.source'
ENV_KEYS = {'APP_ENV', 'ENABLE_SIP_LAB', 'SIP_LAB_INSTANCE_ID', 'SIP_LAB_ARI_PASSWORD',
            'SIP_LAB_BIND_ADDRESS', 'SIP_LAB_QWEN_VOICE_TOKEN'}
REASONS = {'duration_limit': '达到本通时长上限', 'turn_limit': '达到 8 次语音请求上限（含开场和打断）',
           'dialogue_ended': '语音对话发出结束标记；千问路径需两步结束确认',
           'voice_failed': '语音服务失败，请查看错误码', 'channel_ended': '电话或网关结束了通道',
           'service_stopped': '测试服务已停止'}


class LabError(Exception):
    pass


def helper(root, name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), root / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_environment(root):
    private = root / 'deploy/sip-lab/generated'
    if private.is_symlink() or not private.is_dir() or private.stat().st_mode & 0o777 != 0o700:
        raise LabError('需要私有 SIP 配置；首次启动加 --host-address <Mac局域网IPv4>。')
    reader = helper(root, 'sip-lab-address-update').read_private
    values = {}
    for name in ('lab.env', 'qwen-voice.env'):
        for line in reader(private / name).splitlines():
            matched = re.fullmatch(r'export ([A-Z_]+)=([A-Za-z0-9.]+)', line)
            if not matched or matched[1] not in ENV_KEYS or matched[1] in values:
                raise LabError('私有配置格式不匹配；请保留原凭据并检查配置文件。')
            values[matched[1]] = matched[2]
    if (set(values) != ENV_KEYS or values['APP_ENV'] != 'development' or values['ENABLE_SIP_LAB'] != 'true'
            or not re.fullmatch(r'[a-f0-9]{64}', values['SIP_LAB_QWEN_VOICE_TOKEN'])):
        raise LabError('私有 SIP/千问配置不完整。')
    helper(root, 'sip-lab-address-update').address_value(values['SIP_LAB_BIND_ADDRESS'])
    # Generated files are data, never sourced as shell code. Drop stale exported lab settings.
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith('SIP_LAB_') and key != 'QWEN_TOKEN_PLAN_API_KEY'}
    environment.update(values)
    return environment


class Lab:
    def __init__(self, root=ROOT, *, session_seconds=180, verifying=False, speech_profile=None, speech_config=None):
        self.root = Path(root)
        self.session_seconds = session_seconds
        self.verifying = verifying
        if speech_profile is not None and speech_profile not in {"baseline", "warm_female", "clear_male"}:
            raise LabError("不支持该语音档。")
        self.requested_speech_profile = speech_profile
        self.speech_profile = speech_profile or "baseline"
        self.imported_speech = None
        if speech_config is not None:
            try:
                reader = helper(self.root, "sip-lab-speech-config")
            except FileNotFoundError:
                raise LabError("缺少音色导入工具；先在仓库根目录 git pull 更新代码。") from None
            try:
                self.imported_speech = reader.read(speech_config)
            except (OSError, ValueError, TypeError) as error:
                raise LabError("产品音色配置无效：" + reader.error_message(error) + " 未修改本机偏好。") from None
            self.speech_profile = self.imported_speech["speech_profile"]
        self.verification_passed = False
        self.environment = None
        self.host = None
        self.media_started = False
        self.logs = None
        self.launcher_lock = None
        self.python = self.root / 'backend/.venv/bin/python'

    def run(self, arguments, *, capture=True, timeout=30, environment=None):
        return subprocess.run(arguments, cwd=self.root, env=environment or self.environment,
                              capture_output=capture, text=True, timeout=timeout, check=False)

    def compose(self, *arguments, capture=True, timeout=30):
        return self.run(['docker', 'compose', '--env-file', str(self.root / 'deploy/sip-lab/generated/compose.env'),
                         '-f', str(self.root / 'deploy/sip-lab/compose.yml'), '--profile', 'media', *arguments],
                        capture=capture, timeout=timeout)

    def configure(self, address=None, *, exclusive=False):
        private = self.root / 'deploy/sip-lab/generated'
        if not private.exists() and not private.is_symlink():
            if not address:
                raise LabError('首次启动请加 --host-address <Mac局域网IPv4>；已有配置不需要此参数。')
            helper(self.root, 'sip-lab-config').generate(private, address)
        helper(self.root, 'sip-lab-qwen-voice-config').generate(private)
        self.environment = read_environment(self.root)
        if exclusive:
            descriptor = os.open(private / '.launcher.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                    raise LabError('启动器锁文件不是私有普通文件。')
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise LabError('已有启动器正在运行；在原终端 Ctrl+C 退出，或用 status 查看状态。') from None
            except BaseException:
                os.close(descriptor)
                raise
            self.launcher_lock = descriptor
        saved_speech = private / 'speech-settings.json'
        if saved_speech.exists() or saved_speech.is_symlink():
            try:
                saved = helper(self.root, 'sip-lab-speech-config').read(saved_speech, private=True)
            except (OSError, ValueError, TypeError):
                raise LabError('已保存的音色配置权限或内容无效；未启动服务，请保留文件并检查。') from None
            if self.imported_speech is None and self.requested_speech_profile is None:
                self.speech_profile = saved['speech_profile']
        if address and address != self.environment['SIP_LAB_BIND_ADDRESS']:
            running = self.compose('ps', '--status', 'running', '--services')
            if running.returncode:
                raise LabError('地址更新前无法确认网关状态；未修改地址。')
            if 'asterisk' in running.stdout.splitlines():
                self.ensure_idle()
            helper(self.root, 'sip-lab-address-update').update(private, address)
            self.environment = read_environment(self.root)
        self.environment.update(SIP_LAB_MEDIA_ACKNOWLEDGED='true', SIP_LAB_MEDIA_MODE='qwen',
                                SIP_LAB_QWEN_VOICE_ACKNOWLEDGED='true',
                                SIP_LAB_QWEN_SESSION_SECONDS=str(self.session_seconds))

    def dependencies(self, install=False):
        if not self.python.exists():
            if not install:
                raise LabError('缺少 backend/.venv；运行 python3 scripts/sip-lab.py setup。')
            print('创建本项目 Python 虚拟环境…', flush=True)
            if self.run([sys.executable, '-m', 'venv', str(self.root / 'backend/.venv')], capture=False).returncode:
                raise LabError('虚拟环境创建失败。')
        check = self.run([str(self.python), '-c',
                          "import fastapi, uvicorn, httpx, websockets; assert websockets.__version__ == '17.2'"])
        if check.returncode:
            if not install:
                raise LabError('宿主依赖缺失；运行 python3 scripts/sip-lab.py setup。')
            print('安装本项目依赖（不修改 Homebrew 系统 Python）…', flush=True)
            if self.run([str(self.python), '-m', 'pip', 'install', '-r', 'backend/requirements.txt'],
                        capture=False, timeout=600).returncode:
                raise LabError('依赖安装失败；没有自动重试。')

    def doctor(self):
        result = self.run([str(self.python), '-m', 'app.sip_lab_qwen_voice', 'doctor'],
                          environment=dict(self.environment, PYTHONPATH=str(self.root / 'backend')))
        try:
            report = json.loads(result.stdout)
            if not isinstance(report, dict):
                raise TypeError()
            return report
        except (ValueError, TypeError):
            raise LabError('宿主检查未返回有效状态。') from None

    def images(self):
        builder = helper(self.root, 'sip-lab-media-local-build')
        recipes = [('sip-lab-asterisk:latest', 'ubuntu:24.04',
                    ['deploy/sip-lab/Dockerfile', 'deploy/sip-lab/start.sh']),
                   (builder.IMAGE, builder.BASE, sorted(builder.FILES) + ['deploy/sip-lab/media.Dockerfile'])]
        for image, base, names in recipes:
            source = hashlib.sha256()
            for name in names:
                path = self.root / name
                if path.is_symlink() or not path.is_file():
                    raise LabError('镜像源文件缺失或为符号链接。')
                source.update(name.encode() + b'\0' + path.read_bytes())
            revision = source.hexdigest()
            inspected = self.run(['docker', 'image', 'inspect', image, '--format',
                                  '{{ index .Config.Labels "' + LABEL + '" }}'])
            if inspected.returncode == 0 and inspected.stdout.strip() == revision:
                continue
            if self.run(['docker', 'image', 'inspect', base]).returncode:
                raise LabError('缺少本机基础镜像；请先执行 docker pull ' + base + '，然后重新启动。')
            print('更新测试镜像 ' + image + '（只用本机基础镜像）…', flush=True)
            if image == builder.IMAGE:
                builder.build(self.root, source_digest=revision, environment=self.environment)
            else:
                with tempfile.TemporaryDirectory(prefix='sip-lab-asterisk-') as directory:
                    for name in names:
                        (Path(directory) / Path(name).name).write_bytes((self.root / name).read_bytes())
                    built = self.run(['docker', 'build', '--pull=false', '--tag', image,
                                      '--label', LABEL + '=' + revision, directory], capture=False, timeout=600,
                                     environment=dict(self.environment, DOCKER_BUILDKIT='0'))
                    if built.returncode:
                        raise LabError('Asterisk 本机缓存构建失败；没有自动重试。')

    def ensure_idle(self):
        result = self.compose('exec', '-T', 'asterisk', 'asterisk', '-rx', 'core show channels count')
        if result.returncode or not re.search(r'^0 active channels\s*$', result.stdout, re.MULTILINE):
            raise LabError('电话通道仍在使用或无法确认；请先挂断电话再启动/停止。')

    def ensure_host(self):
        report = self.doctor()
        if report.get('host_ready') is True:
            if report.get('provider', {}).get('speech_profile', 'baseline') != self.speech_profile:
                raise LabError('现有宿主音色不同；先挂断，在原启动终端 Ctrl+C 退出，再用所选语音档启动。')
            if report.get('voice_session_limit_seconds', 60) < self.session_seconds:
                raise LabError('现有宿主为旧 60 秒版本；请在原终端 Ctrl+C 停止后重新运行 start，或加 --session-seconds 60。')
            if self.verifying and report.get('voice_test_metadata_version') != 1:
                raise LabError('现有宿主缺少场景验收标记；请在原终端 Ctrl+C 退出后重新运行 verify。')
            if report.get('voice_test_matcher_version') != 3 or report.get('stop_confirmation_required') is not True:
                raise LabError('现有宿主仍使用旧短句匹配/单步结束；请在原终端 Ctrl+C 退出后重新运行 start/verify。')
            print('复用已通过认证的 18092 宿主；退出时保留该进程。', flush=True)
            return
        with socket.socket() as connection:
            connection.settimeout(0.5)
            occupied = connection.connect_ex(('127.0.0.1', 18092)) == 0
        if occupied:
            raise LabError('18092 已占用但宿主未就绪；不接管现有服务。请检查其认证、忙碌或排空状态。')
        print('语音档：' + self.speech_profile, flush=True)
        print('启动千问宿主；请在本终端输入 API Key（隐藏输入，仅存内存）…', flush=True)
        self.host = subprocess.Popen([str(self.python), '-m', 'app.sip_lab_qwen_voice', 'serve', '--acknowledged',
                                     '--speech-profile', self.speech_profile],
                                     cwd=self.root / 'backend', env=self.environment)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if self.host.poll() is not None:
                raise LabError('千问宿主退出；请查看上方固定错误码。')
            if self.doctor().get('host_ready') is True:
                return
            time.sleep(0.5)
        raise LabError('等待宿主就绪超时（180 秒）；未启动媒体测试。')

    def status(self):
        report = self.doctor()
        print('千问宿主：' + ('就绪' if report.get('host_ready') else '未就绪'), flush=True)
        print('连接错误：' + str(report.get('error') or '无'), flush=True)
        result = self.run([str(self.python), '-m', 'app.sip_lab', 'status'],
                          environment=dict(self.environment, PYTHONPATH=str(self.root / 'backend')))
        try:
            sip = json.loads(result.stdout)
            print('SIP 网关：' + ('可达' if sip.get('gateway_reachable') else '不可达'))
            print('Linphone 1001：' + ('已注册' if sip.get('endpoint_state') == 'online' else '未注册'))
        except (ValueError, AttributeError):
            print('SIP 网关：检查失败')
        if self.compose('ps', '-a', capture=False).returncode:
            raise LabError('Docker 服务状态检查失败。')
        print('服务就绪仅证明连接；真实听音、打断和业务验收需分别确认。')

    def start(self):
        self.dependencies(install=True)
        if self.run(['docker', 'info']).returncode:
            raise LabError('Docker/OrbStack 尚未运行。')
        # Do not recreate a working gateway or media app while a phone call is active.
        running = self.compose('ps', '--status', 'running', '--services')
        if running.returncode:
            raise LabError('Docker Compose 状态检查失败。')
        if 'asterisk' in running.stdout.splitlines():
            self.ensure_idle()
        self.images()
        if 'asterisk' in running.stdout.splitlines():
            self.ensure_idle()  # Builds may take minutes; recheck before gateway changes.
        if self.compose('up', '--no-build', '--pull', 'never', '-d', 'asterisk').returncode:
            raise LabError('SIP 网关启动失败；请检查地址/端口。')
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            checked = self.compose('exec', '-T', 'asterisk', 'asterisk', '-rx', 'dialplan show 1003@lab-echo')
            if checked.returncode == 0 and 'Stasis(repayguard_media_lab)' in checked.stdout:
                break
            time.sleep(0.5)
        else:
            raise LabError('1003 拨号规则未就绪；请检查 Asterisk 日志。')
        # A running container retains its loaded dialplan after a bind-mounted file changes.
        if self.compose('exec', '-T', 'asterisk', 'asterisk', '-rx', 'dialplan reload').returncode:
            raise LabError('1003 拨号规则重载失败。')
        checked = self.compose('exec', '-T', 'asterisk', 'asterisk', '-rx', 'dialplan show 1003@lab-echo')
        if checked.returncode or 'TIMEOUT(absolute)=180' not in checked.stdout:
            raise LabError('1003 新时长上限未加载；未启动模型媒体测试。')
        self.ensure_host()
        self.ensure_idle()
        self.media_started = True
        if self.compose('up', '--no-deps', '--no-build', '--pull', 'never', '-d', '--force-recreate', 'media').returncode:
            raise LabError('媒体桥启动失败。')
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            logged = self.compose('logs', '--no-log-prefix', '--tail', '30', 'media')
            events = parse_events(logged.stdout)
            if any(event.get('event') == 'media_lab_unavailable' for event in events):
                for event in events:
                    display_event(event)
                raise LabError('媒体桥未就绪；已停止本次启动的媒体服务。')
            if any(event.get('event') == 'awaiting_linphone_1003' for event in events):
                break
            time.sleep(0.5)
        else:
            raise LabError('等待媒体桥就绪超时。')
        self.remember_speech()
        address = self.environment['SIP_LAB_BIND_ADDRESS']
        print('\n测试已就绪：Linphone 使用现有 1001 账号，服务器 ' + address + ':5060，拨打 1003。', flush=True)
        print('说：今天是语音链路测试。结束：先说结束测试，再说确认结束测试。每通最多 '
              + str(self.session_seconds) + ' 秒 / 8 次请求 / 24 次云调用。', flush=True)
        print(('验收本通结束后保存报告并退出；' if self.verifying else '电话结束后启动器继续运行，可再次手动拨打；')
              + 'Ctrl+C 停止媒体桥和本次启动的宿主。', flush=True)
        if self.verifying:
            print('本次为单通验收；除第4步插话外，等待回复播完再说下一句：\n'
                  '1. 今天是语音链路测试，请确认。\n'
                  '2. 复述1234和10月9日。\n'
                  '3. 不要结束测试。\n'
                  '4. 再说：今天是语音链路测试，请确认；在这轮回复播放时插话：换一句测试短句。\n'
                  '5. 结束测试。\n'
                  '6. 等确认提示播完，再说：确认结束测试。\n'
                  '结束语播完后，本终端会收集听音确认并保存报告；不记录原始转写。', flush=True)
        self.logs = subprocess.Popen(self.compose_command_logs(), cwd=self.root, env=self.environment,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        pending = b''
        with selectors.DefaultSelector() as selector:
            selector.register(self.logs.stdout, selectors.EVENT_READ)
            while self.logs.poll() is None:
                if self.host is not None and self.host.poll() is not None:
                    raise LabError('本次启动的千问宿主已退出。')
                for key, _ in selector.select(timeout=1):
                    pending += os.read(key.fileobj.fileno(), 4096)
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        for event in parse_events(line.decode('utf-8', errors='replace')):
                            if self.handle_event(event):
                                return
                    if len(pending) > 65536:
                        pending = b''
        raise LabError('媒体日志连接结束；请用 status 检查服务。')

    def remember_speech(self):
        if self.imported_speech is None:
            return
        if self.launcher_lock is None:
            raise LabError('保存音色需要当前启动器持有配置锁。')
        try:
            helper(self.root, 'sip-lab-speech-config').save(
                self.root / 'deploy/sip-lab/generated', self.imported_speech)
        except (OSError, ValueError, TypeError):
            raise LabError('音色已用于本次宿主，但本机偏好保存失败；未报告持久化成功。') from None
        print('产品音色配置已应用并记住：' + self.speech_profile + '；下次直接运行 start。', flush=True)

    def handle_event(self, event):
        display_event(event)
        if event.get('event') != 'media_session_summary' or event.get('mode') != 'qwen_token_plan_voice_lab':
            return False
        reporter = helper(self.root, 'sip-lab-report')
        try:
            # Validate measured data before asking for hearing feedback.
            report = reporter.make_report(event)
            path = reporter.save_report(self.root / 'deploy/sip-lab/generated', report)
            if self.verifying:
                print('程序测量已保存：' + str(path), flush=True)
            if self.verifying:
                answers = {}
                for key, label in reporter.ATTESTATIONS.items():
                    while True:
                        if not sys.stdin.isatty():
                            answers[key] = None
                            break
                        try:
                            answer = input(label + '？[y=是 / n=否 / s=跳过]：').strip().lower()
                        except EOFError:
                            answers[key] = None
                            break
                        if answer in {'y', 'n', 's', ''}:
                            answers[key] = {'y': True, 'n': False}.get(answer)
                            break
                        print('请输入 y、n 或 s；不会保存输入文本。', flush=True)
                reviewed = reporter.make_report(event, answers)
                reviewed['measurement_report_id'] = report['report_id']
                report = reviewed
                path = reporter.save_report(self.root / 'deploy/sip-lab/generated', report)
            reporter.describe(report)
            self.verification_passed = report['assessment'] == 'passed_with_operator_attestation'
            print('本机脱敏报告：' + str(path), flush=True)
        except (ValueError, OSError):
            print('电话报告无法保存：数据或私有目录不符合要求；未输出原始事件。', file=sys.stderr)
        return self.verifying

    def compose_command_logs(self):
        return ['docker', 'compose', '--env-file', str(self.root / 'deploy/sip-lab/generated/compose.env'),
                '-f', str(self.root / 'deploy/sip-lab/compose.yml'), '--profile', 'media',
                'logs', '--follow', '--no-log-prefix', '--tail', '0', 'media']

    def close(self):
        try:
            self._close_services()
        finally:
            if self.launcher_lock is not None:
                os.close(self.launcher_lock)
                self.launcher_lock = None

    def _close_services(self):
        try:
            if self.logs is not None and self.logs.poll() is None:
                self.logs.terminate()
                self.logs.wait(timeout=5)
        finally:
            try:
                if self.media_started and self.compose('stop', 'media', timeout=40).returncode:
                    print('媒体桥停止未确认；请用 status 检查服务。', file=sys.stderr)
            finally:
                if self.host is not None and self.host.poll() is None:
                    self.host.terminate()
                    try:
                        self.host.wait(timeout=25)
                    except subprocess.TimeoutExpired:
                        print('宿主仍在退出；请等待其退出后再启动，未强制接管或重试。', flush=True)


def parse_events(text):
    events = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
        except ValueError:
            pass
    return events


def display_event(event):
    kind = event.get('event')
    if kind == 'program_media_connected':
        print('电话已进入媒体桥，正在处理语音。', flush=True)
    elif kind == 'voice_turn_diagnostic':
        labels = {'greeting': '开场', 'confirm': '确认', 'numbers_date': '数字日期', 'negation': '否定结束',
                  'stop_pending': '结束待确认', 'stop': '已确认结束', 'out_of_scope': '未匹配'}
        number, audio = event.get('request_number'), event.get('input_audio_ms')
        case = event.get('test_case')
        if type(number) is int and 1 <= number <= 8 and type(audio) is int and 0 <= audio <= 6000:
            print('第{}次请求：{}，输入音频{}ms。'.format(number, labels.get(case, '未知场景') if isinstance(case, str) else '未知场景', audio), flush=True)
            if event.get('stop_control') == 'requested':
                print('尚未挂断；若刚才说的是不要结束，请说继续测试；确需结束再说确认结束测试。', flush=True)
            if event.get('llm_end_ignored') is True:
                print('模型结束标记已忽略；通话由确定性结束控制器管理。', flush=True)
    elif kind == 'media_session_summary':
        print('本通结束：' + REASONS.get(event.get('end_reason'), '原因未记录（旧媒体镜像或未分类退出）'), flush=True)
        if event.get('session_limit_seconds') in {60, 180}:
            print('本通时长上限：' + str(event['session_limit_seconds']) + ' 秒。', flush=True)
        # Only known counters and error codes are shown; never print raw log lines or model text.
        received = event.get('received_packets')
        completed = event.get('local_turns_completed')
        calls = event.get('cloud_provider_calls')
        counters = [value if type(value) is int and value >= 0 else '?' for value in (received, completed, calls)]
        print('收到 RTP：{}；完成语音请求：{}；云调用：{}。'.format(*counters), flush=True)
        code = event.get('local_voice_error')
        if code in {'qwen_host_connection_refused', 'qwen_host_auth_failed', 'qwen_host_busy',
                    'voice_turn_budget_exhausted', 'voice_session_timeout', 'provider_call_budget_exhausted',
                    'voice_turn_failed', 'voice_turn_timeout', 'local_voice_service_failed',
                    'end_intent_mismatch', 'numbers_date_mismatch', 'invalid_model_json', 'off_scope_reply',
                    'turn_deadline_exceeded', 'qwen_session_call_budget_exceeded',
                    'speech_provider_unavailable_result_unknown', 'invalid_voice_completion',
                    'voice_playback_budget_exceeded', 'qwen_host_inference_failed'}:
            print('语音错误码：' + code, flush=True)
        stage = event.get('provider_active_stage')
        if isinstance(stage, str) and stage in {'asr', 'llm', 'tts'}:
            print('最后云调用阶段：' + stage, flush=True)
    elif kind in {'media_lab_unavailable', 'media_setup_failed'}:
        for key in ('stage', 'error'):
            value = event.get(key)
            if isinstance(value, str) and re.fullmatch(r'[a-z_]{1,80}', value):
                print('媒体 ' + key + '：' + value, flush=True)


def main():
    parser = argparse.ArgumentParser(description='个人 Linphone/千问测试：一个终端启动，API Key 只存内存。')
    parser.add_argument('action', choices=('start', 'verify', 'report', 'setup', 'status', 'stop', 'probe', 'account', 'dialogue', 'speech-check'))
    parser.add_argument('--host-address', help='首次生成或明确更新 Mac 的局域网 IPv4；保留已有凭据')
    parser.add_argument('--session-seconds', type=int, choices=(60, 180), default=180,
                        help='每通电话时长上限：默认 180 秒；仍保留 8 次请求 / 24 次云调用上限')
    speech = parser.add_mutually_exclusive_group()
    speech.add_argument("--speech-profile", choices=("baseline", "warm_female", "clear_male"),
                        help="本次覆盖音色；默认沿用已导入的产品配置，切换前退出旧宿主")
    speech.add_argument("--speech-config", type=Path, help="导入产品页面下载的音色 JSON，启动成功后记住")
    args = parser.parse_args()
    if args.action == 'speech-check':
        if args.speech_config is None or args.speech_profile is not None or args.host_address:
            parser.error('speech-check 只接受 --speech-config <实际文件路径>')
        try:
            reader = helper(ROOT, 'sip-lab-speech-config')
        except FileNotFoundError:
            print('缺少音色导入工具；先在仓库根目录 git pull 更新代码。', file=sys.stderr)
            return 2
        try:
            data = reader.read(args.speech_config)
        except (OSError, ValueError, TypeError) as error:
            print('音色配置检查未通过：' + reader.error_message(error), file=sys.stderr)
            return 2
        print(json.dumps({'mode': 'sip_lab_speech_config_check', 'valid': True,
                          'speech_profile': data['speech_profile'], 'external_calls': 0,
                          'configuration_written': False}, ensure_ascii=False))
        return 0
    if (args.speech_profile is not None or args.speech_config is not None) and args.action not in {"start", "verify"}:
        parser.error("音色参数仅用于 start/verify/speech-check")
    if args.action == "dialogue":
        if args.host_address:
            parser.error("dialogue 不需要 --host-address")
        return helper(ROOT, "loan-dialogue-replay").main()
    lab = None
    try:
        lab = Lab(session_seconds=args.session_seconds, verifying=args.action == 'verify',
                  **({'speech_profile': args.speech_profile} if args.speech_profile is not None else {}),
                  **({'speech_config': args.speech_config} if args.speech_config is not None else {}))
        if args.host_address and args.action not in {'start', 'verify', 'setup'}:
            raise LabError('--host-address 仅用于 start/verify/setup；诊断和报告不修改配置。')
        if args.action in {'start', 'verify', 'setup'}:
            lab.configure(args.host_address, exclusive=True)
        else:
            lab.environment = read_environment(lab.root)
        if args.action in {'start', 'verify'}:
            lab.start()
            if lab.verifying:
                return 0 if lab.verification_passed else 2
        elif args.action == 'report':
            reporter = helper(lab.root, 'sip-lab-report')
            try:
                reporter.describe(reporter.latest_report(lab.root / 'deploy/sip-lab/generated'))
            except FileNotFoundError:
                raise LabError('尚无电话报告；先运行 verify，或在新版 start 下完成一通电话。') from None
            except reporter.ReportError as error:
                if str(error) == 'phone_report_legacy_acceptance_rules':
                    raise LabError('旧报告已保留，但缺少新版插话/耗时验收规则；运行 verify 生成当前报告，不能补写旧结果。') from None
                raise LabError('报告缺失或权限/摘要/结构检查未通过；保留原文件，运行 verify 生成新报告。') from None
        elif args.action == 'setup':
            lab.dependencies(install=True)
            lab.images()
            print('配置和依赖准备完成；运行 python3 scripts/sip-lab.py start。')
        elif args.action == 'status':
            lab.dependencies()
            lab.status()
        elif args.action == 'stop':
            if lab.compose('stop', 'media', timeout=40).returncode:
                raise LabError('媒体停止失败。')
            print('媒体桥已停止；独立宿主及 Asterisk 保留。启动终端用 Ctrl+C 退出宿主。')
        elif args.action == 'account':
            # Explicit local request only. No password in status/start logs.
            print(helper(lab.root, 'sip-lab-address-update').read_private(
                lab.root / 'deploy/sip-lab/generated/linphone-account.txt'))
        elif args.action == 'probe':
            lab.dependencies()
            if not lab.doctor().get('host_ready'):
                raise LabError('宿主未就绪；先运行 start，或在原终端启动宿主。')
            print('执行一次固定合成自测（最多四次云调用，不重试）…', flush=True)
            return lab.run([str(lab.python), '-m', 'app.sip_lab_qwen_voice', 'probe', '--acknowledged'],
                           capture=False, timeout=35,
                           environment=dict(lab.environment, PYTHONPATH=str(lab.root / 'backend'))).returncode
        return 0
    except KeyboardInterrupt:
        print('\n正在停止本次测试；保留账号、Asterisk 与历史 journal。', flush=True)
        return 130 if lab is not None and lab.verifying else 0
    except LabError as error:
        print('未就绪：' + str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError, subprocess.SubprocessError):
        print('未就绪：工具、私有配置或本机缓存构建失败；未打印原始异常和凭据。', file=sys.stderr)
        return 2
    finally:
        try:
            if lab is not None:
                lab.close()
        except (OSError, subprocess.SubprocessError):
            print('清理未能确认；请用 status 检查媒体桥与宿主，未自动重启。', file=sys.stderr)


if __name__ == '__main__':
    raise SystemExit(main())
