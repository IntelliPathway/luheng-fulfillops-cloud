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
           'dialogue_ended': '语音对话发出结束标记；千问路径仅明确结束短句可触发',
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
    def __init__(self, root=ROOT, *, session_seconds=180):
        self.root = Path(root)
        self.session_seconds = session_seconds
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
            if report.get('voice_session_limit_seconds', 60) < self.session_seconds:
                raise LabError('现有宿主为旧 60 秒版本；请在原终端 Ctrl+C 停止后重新运行 start，或加 --session-seconds 60。')
            print('复用已通过认证的 18092 宿主；退出时保留该进程。', flush=True)
            return
        with socket.socket() as connection:
            connection.settimeout(0.5)
            occupied = connection.connect_ex(('127.0.0.1', 18092)) == 0
        if occupied:
            raise LabError('18092 已占用但宿主未就绪；不接管现有服务。请检查其认证、忙碌或排空状态。')
        print('启动千问宿主；请在本终端输入 API Key（隐藏输入，仅存内存）…', flush=True)
        self.host = subprocess.Popen([str(self.python), '-m', 'app.sip_lab_qwen_voice', 'serve', '--acknowledged'],
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
        address = self.environment['SIP_LAB_BIND_ADDRESS']
        print('\n测试已就绪：Linphone 使用现有 1001 账号，服务器 ' + address + ':5060，拨打 1003。', flush=True)
        print('说：今天是语音链路测试。结束：结束测试。每通最多 '
              + str(self.session_seconds) + ' 秒 / 8 次请求 / 24 次云调用。', flush=True)
        print('电话结束后启动器继续运行，可再次手动拨打；Ctrl+C 停止媒体桥和本次启动的宿主。', flush=True)
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
                            display_event(event)
                    if len(pending) > 65536:
                        pending = b''
        raise LabError('媒体日志连接结束；请用 status 检查服务。')

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
                    'voice_turn_failed', 'voice_turn_timeout', 'local_voice_service_failed'}:
            print('语音错误码：' + code, flush=True)
    elif kind in {'media_lab_unavailable', 'media_setup_failed'}:
        for key in ('stage', 'error'):
            value = event.get(key)
            if isinstance(value, str) and re.fullmatch(r'[a-z_]{1,80}', value):
                print('媒体 ' + key + '：' + value, flush=True)


def main():
    parser = argparse.ArgumentParser(description='个人 Linphone/千问测试：一个终端启动，API Key 只存内存。')
    parser.add_argument('action', choices=('start', 'setup', 'status', 'stop', 'probe', 'account'))
    parser.add_argument('--host-address', help='首次生成或明确更新 Mac 的局域网 IPv4；保留已有凭据')
    parser.add_argument('--session-seconds', type=int, choices=(60, 180), default=180,
                        help='每通电话时长上限：默认 180 秒；仍保留 8 次请求 / 24 次云调用上限')
    args = parser.parse_args()
    lab = Lab(session_seconds=args.session_seconds)
    try:
        if args.host_address and args.action not in {'start', 'setup'}:
            raise LabError('--host-address 仅用于 start/setup；status/stop/probe/account 不修改配置。')
        if args.action in {'start', 'setup'}:
            lab.configure(args.host_address, exclusive=True)
        else:
            lab.environment = read_environment(lab.root)
        if args.action == 'start':
            lab.start()
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
        return 0
    except LabError as error:
        print('未就绪：' + str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError, subprocess.SubprocessError):
        print('未就绪：工具、私有配置或本机缓存构建失败；未打印原始异常和凭据。', file=sys.stderr)
        return 2
    finally:
        try:
            lab.close()
        except (OSError, subprocess.SubprocessError):
            print('清理未能确认；请用 status 检查媒体桥与宿主，未自动重启。', file=sys.stderr)


if __name__ == '__main__':
    raise SystemExit(main())
