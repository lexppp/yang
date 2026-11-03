#!/usr/bin/env python3
"""Independent, credential-free RSSHub trial on the existing Ubuntu server."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

IMAGE = 'ghcr.io/diygod/rsshub@sha256:bd0b2ffa235aab44926f5014b99b6007d755f646acca5102111fcbb404b3457c'
IMAGE_REVISION = 'c24abfcedd66168ad47cf41d9e023cf2990c0b44'
NAME = 'coinw-rsshub-trial'
LABEL = 'io.coinw.component=rsshub-trial'
DOCKER = ['docker', '--host', 'unix:///var/run/docker.sock']
MIB = 1024 ** 2
GIB = 1024 ** 3
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


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


def start_args():
    return ['run', '-d', '--name', NAME, '--label', LABEL, '--restart=no',
            '--memory=768m', '--memory-swap=768m', '--cpus=0.5', '--pids-limit=128',
            '--security-opt=no-new-privileges', '--cap-drop=ALL', '--user=1000:1000',
            '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m', '--log-driver=none',
            '-p', '127.0.0.1:1200:1200', '-e', 'CACHE_TYPE=memory',
            '-e', 'NODE_ENV=production', '-e', 'NODE_OPTIONS=--max-old-space-size=512', IMAGE]


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
    print('本阶段没有配置 X 登录态，没有验证推文读取，也没有启用 TG 自动转发。')


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
    parser.add_argument('action', choices=('prepare', 'status', 'stop'))
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise TrialError('请用 sudo python3 运行此脚本。')
    if args.action == 'prepare':
        prepare()
    elif args.action == 'status':
        status()
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
