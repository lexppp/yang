# 自建免费 X 读取：初步评估

用户已确认：来源是 X `@CoinWOfficial`，目标为 TG `@coinwofficialchannel`，只接受免费数据源，并愿意先评估使用自己有权使用的 X 账号登录态。这里不采用 TG 频道互转，也不要求购买 X API。

## 官方代码依据

核对的 RSSHub 官方仓库版本为 `343550485f11c8cfbde607cbdc2405fb66945942`：

- [X 用户时间线配置](https://github.com/DIYgod/RSSHub/blob/343550485f11c8cfbde607cbdc2405fb66945942/lib/routes/twitter/user.ts)：存在 `/twitter/user/:id/:routeParams?` 路由，`requirePuppeteer: false`。
- [X 登录方式说明](https://github.com/DIYgod/RSSHub/blob/343550485f11c8cfbde607cbdc2405fb66945942/lib/routes/twitter/namespace.ts)：支持 `TWITTER_AUTH_TOKEN` 网页登录态；旧用户名/密码移动端登录方式已注明在2025年10月失效，不应采用。
- [缓存配置](https://github.com/DIYgod/RSSHub/blob/343550485f11c8cfbde607cbdc2405fb66945942/lib/config.ts)：支持 `CACHE_TYPE=memory`，不强制 Redis。
- [官方 Compose](https://github.com/DIYgod/RSSHub/blob/343550485f11c8cfbde607cbdc2405fb66945942/docker-compose.yml) 默认同时包含 RSSHub、browserless Chromium、Redis，不适合直接照搬到现有2核2GB机器。

这是代码/配置层面具备尝试条件，不代表已实际读取成功，也不代表现有服务器已经有足够余量。公共订阅检测此前未找到可用来源，不要把那些结果或本地解析测试当作自建成功。

## 建议试跑方案

数据流：`X账号 → 本机RSSHub → 后续读取/转发模块 → TG目标频道`。

- 只运行一个非浏览器捆绑的 RSSHub 实例，内存缓存，不启动 Chromium/browserless 或 Redis。
- 先跟踪一个账号，建议10分钟一次读取。遇到限流退避，登录失效或访问验证时暂停，不反复强行请求。
- RSSHub 仅监听 `127.0.0.1:1200`，不开放新的公网端口，避免他人使用绑定了个人登录态的读取服务。
- 建议的初始测试限制：容器768MiB、Node堆512MiB、CPU0.5核。这些是保护原业务的试跑预算，**不是RSSHub实际使用量或可稳定运行的保证**。需观察启动峰值和真实读取后的占用。
- 不修改现有 CoinW、Caddy、机器人Token或数据库。独立实例可以单独停止。
- X 登录态只能在用户自己的服务器通过隐藏输入写入权限受限的文件，不能放进聊天、命令行参数、公开Compose或日志；不要要求用户分享 `docker inspect` 的环境变量输出。当前评估脚本不读取任何登录态。

## 当前可执行的只读评估

```bash
python3 /home/ubuntu/check_rsshub_host.py
```

不需要 sudo。脚本检查本机 `/proc` 资源、系统盘余量、Docker/Compose是否可访问、本地1200端口、现有 `127.0.0.1:8000/health`。不安装包、不拉取镜像、不启动容器、不读取 `.env`/Cookie/Token，也不调用 Telegram。

初步预算门槛为至少900MiB可用内存、5GiB系统盘余量，CPU没有明显高负载，原机器人健康，端口和Docker已确认。未知、缺少Docker/Compose或权限不足均不能解释成“已经能部署”。门槛不是上游资源承诺，最终仍需实际试跑。

取得真实服务器评估结果后，才准备独立实例；在安全配置自己的 X 登录态后，先只读请求 `/twitter/user/CoinWOfficial`，核对账号、发布时间、正文和图片，并观察下一条真实新帖是否进入。数据源验证之前不启用向真实 TG 频道自动发送。

## 已取得的实际服务器评估与下一步

2026-11-03用户提供的新加坡服务器检测结果：2核，可用内存1453MiB、Swap1988MiB、系统盘剩余31.97GiB、负载接近0；1200端口空闲、CoinW本地健康通过，未安装Docker。该结果满足资源初步门槛，仍未实际读取X。

`setup_rsshub_trial.py` 提供一个**没有登录凭证的独立运行测试**，不是自动转发安装包：

```bash
sudo python3 /home/ubuntu/setup_rsshub_trial.py prepare
```

此操作会通过现有Ubuntu 24.04 APT签名软件源安装`docker.io`（如果没有），启动Docker，并拉取官方非浏览器捆绑镜像。单个容器使用Docker CLI，因此**本次无需Compose**。不会添加用户到docker组，不修改Docker全局配置，不重启CoinW或Caddy，不新增公网监听，不调用Telegram。Docker安装本身会增加系统包、守护进程和Docker网络规则，不能描述成完全不改变系统。

固定镜像：`ghcr.io/diygod/rsshub@sha256:bd0b2ffa235aab44926f5014b99b6007d755f646acca5102111fcbb404b3457c`；官方镜像标签记录的源码为[`c24abfcedd66168ad47cf41d9e023cf2990c0b44`](https://github.com/DIYgod/RSSHub/tree/c24abfcedd66168ad47cf41d9e023cf2990c0b44)。该版本用户路由仍标记`requirePuppeteer:false`，且选择`TWITTER_AUTH_TOKEN`网页接口。amd64压缩层约127MiB；实际解压占用更大。

资源及隔离：768MiB内存（不额外使用容器Swap）、Node堆512MiB、CPU0.5核、128进程、非root用户、只读根文件系统、64MiB临时目录、去除Linux capabilities、禁止提权、仅127.0.0.1:1200、禁用容器日志。容器不自动重启，服务器重启后不会自动启动试跑实例。没有配置任何X登录态，不能据健康接口通过宣称能读取推文。

脚本会复核原机器人健康、内存/磁盘/CPU、端口和同名容器归属。启动期间最多等两分钟；失败或原机器人健康异常时移除本次容器。保留已装Docker和下载的镜像，不卸载共享系统包。原服务仍需自行恢复时，不会通过脚本擅自重启。

查看或停止：

```bash
sudo python3 /home/ubuntu/setup_rsshub_trial.py status
sudo python3 /home/ubuntu/setup_rsshub_trial.py stop
```

只检查带本工具标签的`coinw-rsshub-trial`容器，不打印容器环境变量/日志或服务器配置。`stop`仅删除此容器，保留Docker和镜像。

开发环境已用此官方镜像实际启动并清理无凭证容器，确认非root/只读目录/资源限制可以运行，`/healthz`返回`ok`；一次启动占用约197MiB。该占用是无凭证启动快照，不是X读取负载或用户服务器上的实测。仍需用户实际服务器完成试跑、再在自己服务器隐藏输入登录态并测试X来源，最后才接TG发布。
