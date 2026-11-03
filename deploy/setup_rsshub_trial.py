#!/usr/bin/env python3
"""Independent RSSHub trial and optional read-only X login verification."""
import argparse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import getpass
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from urllib.parse import urlsplit
import urllib.error
import urllib.request
import warnings
import xml.etree.ElementTree as ET

IMAGE = 'ghcr.io/diygod/rsshub@sha256:bd0b2ffa235aab44926f5014b99b6007d755f646acca5102111fcbb404b3457c'
IMAGE_REVISION = 'c24abfcedd66168ad47cf41d9e023cf2990c0b44'
NAME = 'coinw-rsshub-trial'
LABEL = 'io.coinw.component=rsshub-trial'
DOCKER = ['docker', '--host', 'unix:///var/run/docker.sock']
MIB = 1024 ** 2
GIB = 1024 ** 3
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
PRIVATE = Path('/etc/coinw-rsshub')
ACCOUNT = 'CoinWOfficial'
FEED_URL = 'http://127.0.0.1:1200/twitter/user/' + ACCOUNT
MAX_FEED = 2_000_000


class TrialError(Exception):
    pass


def run(args, timeout=60, required=True, visible=False):
    try:
        result = subprocess.run(args, timeout=timeout, check=False, text=True,
                                stdout=None if visible else subprocess.PIPE,
                                stderr=None if visible else subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        raise TrialError('命令无法执行或超时；没有读取或打印任何登录凭证。') from None
    if required and result.returncode:
        raise TrialError('步骤执行失败；请分享本脚本的提示，不要分享配置或登录凭证。')
    return result


def docker(*args, **kwargs):
    return run(DOCKER + list(args), **kwargs)


def bot_ok():
    try:
        with HTTP.open('http://127.0.0.1:8000/health', timeout=3) as response:
            return json.loads(response.read(65536)).get('ok') is True
    except (OSError, ValueError, AttributeError):
        return False


def rsshub_ok():
    try:
        with HTTP.open('http://127.0.0.1:1200/healthz', timeout=3) as response:
            return response.status == 200 and response.read(4096).strip() == b'ok'
    except OSError:
        return False


def memory_available():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:'):
            return int(line.split()[1]) * 1024
    raise TrialError('无法确认可用内存，停止试跑。')


def check_budget():
    if not bot_ok():
        raise TrialError('现有 CoinW 健康检查未通过，先检查原服务。')
    memory = memory_available()
    free = shutil.disk_usage('/').free
    if memory < 900 * MIB or free < 5 * GIB:
        raise TrialError('可用内存需至少900MiB、系统盘至少5GiB，当前不满足试跑门槛。')
    if os.getloadavg()[0] > 1.5 * (os.cpu_count() or 1):
        raise TrialError('当前CPU负载偏高，停止试跑。')
    print(f'资源复核通过：可用内存 {memory // MIB} MiB，磁盘 {free / GIB:.1f} GiB。', flush=True)


def check_free_port():
    with socket.socket() as listener:
        try:
            listener.bind(('127.0.0.1', 1200))
        except OSError:
            raise TrialError('本机1200端口已占用；不会停止占用该端口的服务。') from None


def check_ubuntu():
    values = {}
    for line in Path('/etc/os-release').read_text().splitlines():
        key, sep, value = line.partition('=')
        if sep:
            values[key] = value.strip('"')
    if values.get('ID') != 'ubuntu' or values.get('VERSION_ID') != '24.04':
        raise TrialError('自动安装 Docker 仅适用于已评估的 Ubuntu 24.04。')


def ensure_docker():
    if not shutil.which('docker'):
        check_ubuntu()
        print('从现有 Ubuntu 软件源安装 docker.io；不执行在线安装脚本。', flush=True)
        # APT verifies repository signatures. List unrelated restart needs instead of restarting services.
        old = {key: os.environ.get(key) for key in ('DEBIAN_FRONTEND', 'NEEDRESTART_MODE')}
        os.environ.update(DEBIAN_FRONTEND='noninteractive', NEEDRESTART_MODE='l')
        try:
            run(['apt-get', '-o', 'DPkg::Lock::Timeout=60', 'update'], timeout=300, visible=True)
            run(['apt-get', '-o', 'DPkg::Lock::Timeout=60', 'install', '-y',
                 '--no-install-recommends', 'docker.io'], timeout=600, visible=True)
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    if docker('version', '--format', '{{.Server.Version}}', required=False).returncode:
        run(['systemctl', 'start', 'docker'])
        docker('version', '--format', '{{.Server.Version}}')


def owned_exists():
    result = docker('container', 'ls', '-a', '--filter', 'name=^/' + NAME + '$',
                    '--format', '{{.Names}}|{{.Label "io.coinw.component"}}')
    rows = result.stdout.strip().splitlines()
    if not rows:
        return False
    if rows != [NAME + '|rsshub-trial']:
        raise TrialError('发现同名但不属于本工具的容器，停止操作。')
    return True


def start_args(authenticated=False):
    args = ['run', '-d', '--name', NAME, '--label', LABEL, '--restart=no',
            '--memory=768m', '--memory-swap=768m', '--cpus=0.5', '--pids-limit=128',
            '--security-opt=no-new-privileges', '--cap-drop=ALL', '--user=1000:1000',
            '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m', '--log-driver=none',
            '-p', '127.0.0.1:1200:1200', '-e', 'CACHE_TYPE=memory',
            '-e', 'NODE_ENV=production', '-e', 'NODE_OPTIONS=--max-old-space-size=512']
    if authenticated:
        args += ['--env-file', str(PRIVATE / 'x.env')]
    return args + [IMAGE]


def status():
    print('现有 CoinW 健康检查：' + ('通过' if bot_ok() else '未通过'))
    print('RSSHub 本机健康检查：' + ('通过' if rsshub_ok() else '未通过'))
    if shutil.which('docker') and owned_exists():
        state = docker('container', 'ls', '-a', '--filter', 'name=^/' + NAME + '$',
                       '--format', '{{.State}}').stdout.strip()
        if state in ('running', 'exited', 'created', 'dead', 'paused', 'restarting', 'removing'):
            print('试跑容器状态：' + state)
        stats = docker('stats', '--no-stream', '--format', '{{json .}}', NAME, required=False)
        try:
            values = json.loads(stats.stdout)
            print('试跑占用：' + json.dumps({key: values.get(key) for key in ('MemUsage', 'CPUPerc')}, ensure_ascii=False))
        except (TypeError, ValueError):
            print('试跑占用：暂未取得')
    print('X 登录态文件：' + ('已配置（不显示内容）' if (PRIVATE / 'x.env').exists() else '未配置'))
    print('健康检查通过不代表推文读取成功；当前没有启用 TG 自动转发。')


def private_dir():
    PRIVATE.mkdir(mode=0o700, parents=False, exist_ok=True)
    info = PRIVATE.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise TrialError('独立配置目录必须是root拥有的真实目录，权限700。')


def write_private(name, value):
    private_dir()
    fd, filename = tempfile.mkstemp(prefix='.new-', dir=PRIVATE)
    try:
        with os.fdopen(fd, 'w') as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.replace(filename, PRIVATE / name)
    finally:
        Path(filename).unlink(missing_ok=True)


def check_cooldown():
    private_dir()
    path = PRIVATE / 'last-check'
    if path.exists():
        try:
            remaining = 600 - (time.time() - float(path.read_text()))
        except ValueError:
            raise TrialError('上次读取时间记录异常，停止重复请求。') from None
        if remaining > 0:
            raise TrialError(f'距离上次读取不足10分钟，请约{max(1, int(remaining / 60) + 1)}分钟后再检查。')


def hidden_token():
    if not sys.stdin.isatty():
        raise TrialError('需要交互式Shell隐藏输入；不接受管道或命令行中的Cookie。')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            value = getpass.getpass('Paste X auth_token (input hidden; not BotFather token): ').strip()
    except (getpass.GetPassWarning, EOFError):
        raise TrialError('无法保证隐藏输入，未读取登录态。') from None
    if not re.fullmatch(r'[a-fA-F0-9]{40}', value):
        raise TrialError('格式不符：请只复制X网站名为auth_token的Cookie值，通常为40位十六进制字符；不要复制整段Cookie。')
    return value


class FeedText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.words = []
        self.images = set()
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag in ('p', 'div', 'br'):
            self.words.append(' ')
        if tag == 'img':
            src = dict(attrs).get('src', '')
            if src.startswith('https://'):
                self.images.add(src)

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.words.append(data)


def inspect_feed(body, now=None):
    if len(body) > MAX_FEED or b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
        raise TrialError('订阅内容超限或包含不安全XML声明，未解析。')
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise TrialError('没有取得有效RSS，可能返回了网页或验证页面。') from None
    if root.tag != 'rss':
        raise TrialError('返回内容不是RSSHub预期的RSS订阅。')
    posts = []
    now = now or datetime.now(timezone.utc)
    for item in root.findall('./channel/item'):
        link = item.findtext('link', '').strip()
        url = urlsplit(link)
        match = re.fullmatch(r'/CoinWOfficial/status/(\d+)/?', url.path, re.I)
        if url.hostname not in ('x.com', 'twitter.com', 'www.x.com', 'www.twitter.com') or not match:
            continue
        try:
            date = parsedate_to_datetime(item.findtext('pubDate', ''))
            date = date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date.astimezone(timezone.utc)
        except (ValueError, TypeError, OverflowError):
            continue
        parser = FeedText()
        parser.feed(item.findtext('description', '') or item.findtext('title', ''))
        # Only public post previews; strip terminal control characters from source content.
        text = ''.join(c for c in ' '.join(parser.words) if not unicodedata.category(c).startswith('C'))
        text = re.sub(r'\s+', ' ', text).strip()[:160]
        if not text and not parser.images:
            continue
        posts.append({'url': f'https://x.com/{ACCOUNT}/status/{match[1]}',
                      'published': date.isoformat(), 'preview': text, 'images': len(parser.images),
                      'recent': timedelta(minutes=-10) <= now - date <= timedelta(days=7)})
    posts = list({post['url']: post for post in posts}.values())
    if not posts:
        raise TrialError('没有发现账号、内容和发布时间均可核对的CoinWOfficial帖子；未确认X读取成功。')
    posts.sort(key=lambda p: p['published'], reverse=True)
    return {'posts': len(posts), 'recent': sum(p['recent'] for p in posts), 'samples': posts[:3]}


def check_feed():
    if not owned_exists() or not rsshub_ok() or not bot_ok():
        raise TrialError('请先确认独立RSSHub和原机器人均健康；本次未请求X。')
    if not (PRIVATE / 'x.env').is_file():
        raise TrialError('尚未配置X登录态，请先运行login。')
    check_cooldown()
    write_private('last-check', str(time.time()))
    print('只读检查 @CoinWOfficial，一次请求，最多等待60秒；不向TG发消息。', flush=True)
    try:
        with HTTP.open(FEED_URL, timeout=60) as response:
            body = response.read(MAX_FEED + 1)
    except urllib.error.HTTPError as error:
        # Do not display response body: upstream errors can contain credentials or request headers.
        raise TrialError(f'RSSHub返回HTTP {error.code}。可能涉及登录失效、访问验证、限流或上游错误；没有取得有效推文。不要连续重试，至少等10分钟。') from None
    except OSError:
        raise TrialError('本机订阅请求失败或超时，未确认X读取成功；至少等10分钟再检查。') from None
    report = inspect_feed(body)
    print(f"已核对帖子 {report['posts']} 条，其中7天内 {report['recent']} 条。")
    for post in report['samples']:
        print(post['published'] + ' | ' + post['url'])
        print(f"图片引用 {post['images']} 个 | " + post['preview'])
    if not report['recent']:
        raise TrialError('仅有旧帖子，尚未确认来源足够新。')
    print('本次取得了对应账号的近期帖子。仍需对照X原帖，并观察下一条新帖；尚未启用TG自动转发。')


def login():
    check_cooldown()
    check_budget()
    if not owned_exists() or not rsshub_ok():
        raise TrialError('未发现健康的独立试跑容器，请先完成prepare。')
    value = hidden_token()
    write_private('x.env', 'TWITTER_AUTH_TOKEN=' + value + '\n')
    del value
    docker('rm', '-f', NAME)
    try:
        check_free_port()
        docker(*start_args(authenticated=True))
        for _ in range(60):
            if not bot_ok():
                raise TrialError('原机器人健康检查未通过，已停止独立试跑。')
            if rsshub_ok():
                break
            time.sleep(2)
        else:
            raise TrialError('RSSHub启动未通过健康检查，已停止独立试跑。')
    except BaseException:
        if owned_exists():
            docker('rm', '-f', NAME, required=False)
        raise
    print('X登录态已存入服务器root专用文件；本机RSSHub已重建，不显示登录态。')
    try:
        check_feed()
    finally:
        status()


def prepare():
    check_budget()
    check_free_port()
    ensure_docker()
    if owned_exists():
        raise TrialError('试跑容器已存在；请使用 status 查看，或 stop 单独停止试跑。')
    print('拉取已锁定摘要的官方 RSSHub 镜像（下载约127MiB）。', flush=True)
    docker('pull', IMAGE, timeout=600, visible=True)
    check_budget()
    check_free_port()
    created = False
    try:
        created = True  # Also clean up a partially created container if docker run fails.
        docker(*start_args())
        for _ in range(60):
            if not bot_ok():
                raise TrialError('原机器人健康检查未通过，已停止本次试跑。')
            if rsshub_ok():
                status()
                print('独立试跑启动完成：仅127.0.0.1:1200；不随服务器重启自动启动。')
                return
            time.sleep(2)
        raise TrialError('RSSHub未在两分钟内通过健康检查，已停止试跑；尚未验证 X。')
    except BaseException:
        if created and owned_exists():
            docker('rm', '-f', NAME, required=False)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'status', 'stop', 'login', 'check', 'forget'))
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise TrialError('请用 sudo python3 运行此脚本。')
    if args.action == 'prepare':
        prepare()
    elif args.action == 'status':
        status()
    elif args.action == 'login':
        login()
    elif args.action == 'check':
        check_feed()
    elif args.action == 'forget':
        private_dir()
        if shutil.which('docker') and owned_exists():
            docker('rm', '-f', NAME)
        (PRIVATE / 'x.env').unlink(missing_ok=True)
        print('已移除独立RSSHub容器和本工具保存的X登录态文件。原CoinW服务未改动。')
    elif shutil.which('docker') and owned_exists():
        docker('rm', '-f', NAME)
        print('已移除独立试跑容器；保留 Docker 和已下载镜像。')
        print('现有 CoinW 健康检查：' + ('通过' if bot_ok() else '未通过'))
    else:
        print('没有本工具创建的试跑容器。')


if __name__ == '__main__':
    try:
        main()
    except (TrialError, OSError, ValueError) as error:
        # Never print subprocess command/environment, container logs, or arbitrary exception details.
        print('停止：' + (str(error) if isinstance(error, TrialError) else '本机检查失败。'), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('已中断。', file=sys.stderr)
        sys.exit(130)
