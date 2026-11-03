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
