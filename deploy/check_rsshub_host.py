#!/usr/bin/env python3
"""Read-only capacity assessment on the actual CoinW server; never reads credentials."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request

MIB=1024**2
GIB=1024**3


def memory_info(path=Path('/proc/meminfo')):
    result={}
    try:
        for line in path.read_text().splitlines():
            name,_,value=line.partition(':')
            if name in ('MemTotal','MemAvailable','SwapTotal','SwapFree'):
                result[name]=int(value.strip().split()[0])*1024
    except (OSError,ValueError,IndexError):return {}
    return result


def command(args):
    try:
        result=subprocess.run(args,capture_output=True,text=True,timeout=8,check=False)
        # Do not expose raw daemon errors, environment variables or any configuration.
        return result.stdout.strip()[:100] if result.returncode==0 else None
    except (OSError,subprocess.TimeoutExpired):return None


def port_listening(port=1200):
    readable=False
    for filename in ('/proc/net/tcp','/proc/net/tcp6'):
        try:lines=Path(filename).read_text().splitlines()[1:];readable=True
        except OSError:continue
        for line in lines:
            parts=line.split()
            if len(parts)>3 and parts[3]=='0A' and int(parts[1].rsplit(':',1)[1],16)==port:return True
    return False if readable else None


def assessment(memory_available,disk_free,docker_ready,port_busy,bot_healthy,cpu_busy=False):
    issues=[]
    # These are conservative admission budgets for a test, NOT measured RSSHub usage.
    if memory_available is None:issues.append('无法读取可用内存，暂不能评估')
    elif memory_available<900*MIB:issues.append('可用内存不足900MiB，先不要与现有机器人一起启动RSSHub')
    if disk_free<5*GIB:issues.append('磁盘可用空间不足5GiB，先不要拉取或构建容器镜像')
    if port_busy:issues.append('1200端口已占用，后续须选择其他本地端口')
    elif port_busy is None:issues.append('未能确认1200端口状态')
    if bot_healthy is not True:issues.append('现有机器人本地健康检查未通过，先确认其状态')
    if cpu_busy:issues.append('当前CPU负载较高，先观察现有业务负载')
    resource_ok=memory_available is not None and memory_available>=900*MIB and disk_free>=5*GIB
    if resource_ok and port_busy is False and bot_healthy is True and docker_ready and not cpu_busy:
        verdict='可进入受限试跑；仍需验证X读取和实际内存占用'
    else:verdict='先处理上述条件，尚不能确认适合共存试跑'
    if not docker_ready:issues.append('当前用户未确认Docker及Compose可用；可能未安装、未启动或缺少访问权限')
    return {'resource_budget_ok':resource_ok,'verdict':verdict,'issues':issues,
            'trial_plan':{'rsshub_memory_limit_mib':768,'node_heap_limit_mib':512,'cpu_limit':0.5,
                          'browser_service':False,'redis_service':False,'bind':'127.0.0.1:1200',
                          'poll_interval_minutes':10},
            'note':'试跑预算不是实际RSSHub内存数据，不代表已部署或长期稳定。登录态验证前不启用TG转发。'}


def collect():
    mem=memory_info();disk=shutil.disk_usage('/')
    docker=shutil.which('docker')
    version=command([docker,'version','--format','{{.Server.Version}}']) if docker else None
    compose=command([docker,'compose','version','--short']) if docker else None
    try:load=list(os.getloadavg())
    except OSError:load=None
    healthy=False
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3) as response:
            healthy=json.load(response).get('ok') is True
    except (OSError,ValueError,AttributeError):pass
    busy=port_listening()
    return {'cpu_count':os.cpu_count(),'load_average':load,
            'memory_total_mib':round(mem['MemTotal']/MIB) if 'MemTotal' in mem else None,
            'memory_available_mib':round(mem['MemAvailable']/MIB) if 'MemAvailable' in mem else None,
            'swap_total_mib':round(mem.get('SwapTotal',0)/MIB),
            'root_disk_free_gib':round(disk.free/GIB,2),'docker_installed':bool(docker),
            'docker_server_version':version,'docker_compose_version':compose,
            'port_1200_listening':busy,'coinw_health_ok':healthy,
            'assessment':assessment(mem.get('MemAvailable'),disk.free,bool(version and compose),busy,healthy,bool(load and load[0]>1.5*(os.cpu_count() or 1)))}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--json',action='store_true');args=parser.parse_args()
    result=collect()
    if args.json:print(json.dumps(result,ensure_ascii=False,indent=2));return
    print('RSSHub共存评估：只读取本机资源和本地健康状态，不安装、不重启、不读取登录凭证。')
    print('CPU核数：'+str(result['cpu_count']))
    print('系统负载（1/5/15分钟）：'+str(result['load_average']))
    print(f"内存：总计 {result['memory_total_mib']} MiB，可用 {result['memory_available_mib']} MiB；Swap {result['swap_total_mib']} MiB")
    print(f"系统盘剩余：{result['root_disk_free_gib']} GiB")
    print('Docker：'+(result['docker_server_version'] or ('已找到命令，但未确认服务可用' if result['docker_installed'] else '未安装')))
    print('Docker Compose：'+(result['docker_compose_version'] or '未确认可用'))
    print('本地1200端口：'+('已占用' if result['port_1200_listening'] else '未发现占用' if result['port_1200_listening'] is False else '未知'))
    print('现有CoinW机器人健康检查：'+('通过' if result['coinw_health_ok'] else '未通过/未确认'))
    print('\n评估：'+result['assessment']['verdict'])
    for issue in result['assessment']['issues']:print('- '+issue)
    print('\n建议试跑预算：单RSSHub、无Chromium/Redis、内存上限768MiB、Node堆512MiB、CPU上限0.5核，仅监听127.0.0.1。')
    print(result['assessment']['note'])
    print('以上检测结果可以分享；不要分享Cookie、Token、密码或配置文件。')


if __name__=='__main__':main()
