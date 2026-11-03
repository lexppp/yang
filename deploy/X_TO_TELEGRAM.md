# X → Telegram 自动转发

固定来源 `@CoinWOfficial`，目标 `@coinwofficialchannel`，复用现有
`@CoinWCommunityHubBot`。需要已安装且健康的 CoinW、已登录的本机 RSSHub、
以及该机器人在目标频道的发帖权限。无需付费 X API。

## 腾讯云网页终端安装

将本版本的 `deploy/install_x_forward.py` 和 `deploy/x_to_telegram.py`
下载到同一目录，然后执行：

```bash
sudo python3 install_x_forward.py
```

安装器读取现有 `/etc/coinw/coinw.env` 的机器人绑定，只读验证机器人身份、
频道及发帖权限。不会要求再次输入 Token 或 Cookie，不打印凭据，
不启动第二个长轮询实例，不更改原应用、业务数据库或 X 登录态。
若前置检查失败，先处理对应服务或权限，不能把失败当作上线成功。

独立程序在 `/opt/coinw-x-forward`；新建的转发状态库在
`/var/lib/coinw-x-forward/forward.sqlite3`，权限仅 root 可访问。
两个自有 systemd 单元为 `coinw-x-forward.service` 和 `coinw-x-forward.timer`。
安装器只管理自己的单元，拒绝覆盖同名的其他单元。升级保留启用时间和去重记录。
安装失败会恢复原有工具文件和单元；不删除已生成的转发状态。

安装器将现有、有归属标签的 `coinw-rsshub-trial` 容器重启策略设为
`unless-stopped` 并启用 Docker 开机启动；不重建容器、不读取其环境变量。
这些系统设置即使后续安装失败也保留。原试跑工具 `login` 会重建容器并恢复
试跑重启策略；更新 X 登录态后需再次运行本安装器恢复开机启动策略。

## 转发规则与运行状态

- 默认每次运行结束约10分钟后再次检查，首次安装只记录当前订阅基线，
  不发布历史帖子。只处理发布时间严格晚于第一次启用时间的帖子。
- 中文、日文、英文及 emoji 原样保留为纯文字，附 `View on X` 按钮。
  保留正文中的公开链接；不执行源 HTML。
- 图片只接受 `https://pbs.twimg.com`，由 Telegram 下载；多图逐张发送。
  长正文按 Telegram UTF-16 长度限制拆分后发送，再发送图片，不截断正文。
  当前不上传视频或 GIF；来源没有提供的媒体和被上游删减的正文无法还原。
- 按帖子和消息分段持久化去重，进程锁防止并发发送。
  Telegram 限流按 `retry_after` 等待；已成功的分段不会重发。
- 网络超时、服务器错误、或发送中进程退出，无法确定 Telegram 是否已收件时，
  标记 `uncertain` 并停止后续发布，不自动重试；明确拒绝标记 `failed`。
  必须对照频道和状态库人工处理，不能删除状态库或盲目重跑来解除。
  Telegram Bot API 没有通用的发送幂等键，不能承诺网络故障下绝对不丢不重。
- RSSHub 失败会指数退避，最长约6小时；登录失效需在原服务器正常更新登录态，
  不绕过 X 验证。订阅返回空内容或无近期帖子时首次安装失败。

```bash
sudo systemctl status coinw-x-forward.timer --no-pager
sudo python3 /opt/coinw-x-forward/x_to_telegram.py status
sudo journalctl -u coinw-x-forward.service -n 20 --no-pager
```

日志只输出固定状态与公开帖子 ID，不输出 Token、Cookie 或上游响应。
原试跑工具的 `check` 仍会显示“未开启转发”，该文案仅描述试跑工具自身，
自动转发以这里的独立服务与状态库为准。

暂停（保留全部数据）：

```bash
sudo systemctl disable --now coinw-x-forward.timer
sudo systemctl stop coinw-x-forward.service
```

恢复：`sudo systemctl enable --now coinw-x-forward.timer`。
暂停期间的新帖若恢复时仍在订阅中会补发；已超出订阅窗口的帖子无法获取。

## 验收边界

开发环境新增测试覆盖：启用前不补发、重启去重、多图与长 Unicode 正文、
429 等待及分段续发、不确定发送与进程退出保护、来源/日期/图片地址校验、
机器人频道权限、凭据不进入异常输出，以及重复安装不停止原机器人。
systemd 单元已用 `systemd-analyze verify` 验证。

这些是本地模拟验收，未使用服务器的真实凭据，也未向真实频道发送测试消息。
服务器安装后，应等一条启用后发布的真实新帖，核对正文、图片、按钮及唯一性，
再确认端到端运行成功。服务器安装输出、定时器激活和来源读取成功都不能单独
证明真实新帖已经发布到 TG。
