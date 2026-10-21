# 从演示切换到 Telegram 核心版

本流程连接现有真实后端：社群设置、英文欢迎词/图片、链接/图片/关键词拦截、
入群申请算术验证、coinw 每日签到、/link 专属入群申请链接、成功邀请去重奖励及看板同步。

当前真实核心看板显示社群、积分排行、邀请、日志与管理员权限。
它采用服务器 SQLite 数据，不再使用浏览器演示模型。原来的演示界面不代表高级功能已上线。
每月清零可用积分、历史月份查询、手动调分、邀请 Excel、真实抽奖与公告/定时置顶的后端仍待实现。
当前真实后端有每日签到重置、本月与累计积分展示、签到/邀请奖励数设置和积分 CSV 导出。

## 创建机器人

在 Telegram 搜索官方 @BotFather，核对账号和认证标识，发送 `/newbot`。
建议显示名称 `CoinW Community Hub`，用户名 `CoinWCommunityHubBot`（是否可用以提示为准）。
用户名须以 bot 结尾。Token 只保存在自己手里，不能贴到聊天、浏览器网址、命令参数或截图中。

可在 BotFather 用 `/setcommands` 为这个机器人配置英文菜单：

```text
link - Get your personal invite link
fileid - Get a welcome photo ID in private chat
start - Show check-in and invitation help
```

coinw 是普通群消息，不是 slash 命令。管理员机器人可接收普通群消息；如收不到，核对它的群管理员权限。

## 服务器接入

服务器已按 deploy/README.md 安装 coinw，域名为 panel.chloehub.top。
在腾讯云网页终端运行（下载成功后才会执行）：

```bash
cd /home/ubuntu &&
curl -fL --retry 3 https://raw.githubusercontent.com/lexppp/yang/refs/heads/deploy/hosted-demo/deploy/connect_telegram.py -o connect_telegram.py &&
sudo python3 connect_telegram.py
```

出现 `Paste your BotFather Token` 后粘贴 Token 并按回车。输入隐藏是正常现象。
脚本先只查询 getMe 和 getWebhookInfo；Token 或网络错误不会修改配置。
已有 Webhook 时会停止，保留旧部署，不会自动移除 Webhook。
不要让同一 Token 在其他服务器上同时运行长轮询，否则 Telegram 会返回 Conflict / 409。

检查通过后脚本会短暂停止 coinw，备份账号库和配置，将现有账号及当前密码迁移到独立 live 数据库。
演示样例仅从新复制的数据库中移除，原 demo 数据库保留；再次运行不会清空已有 live 数据。
它会开启 DEMO=0 和真实核心页面，重启服务。配置在 /etc/coinw/coinw.env，权限 600。
备份含密码哈希和配置秘密，保存在 /var/lib/coinw/backups，不能公开。
改 Token 前请确认它对应原来的机器人；不同机器人不应共享邀请链接和业务库。

## 在测试群验收

先用管理员和第二个测试账号建立测试群，加入机器人并设为管理员。
至少允许删除消息、邀请用户/管理入群申请。现有群历史成员不会被全量导入，看板只记录接入后观察到的用户。

1. 刷新网站重新登录。看板应显示机器人在线和测试群，而不是6个演示社群。
2. 群里发送 coinw，应收到英文成功回复；同一天再发一次不重复加分。
3. 查看网站签到数和积分，默认每15秒刷新，也可手动刷新。
4. 群里发送 /link，得到机器人创建的个人专属入群申请链接。
5. 第二个尚未加入的账号点链接并申请加入，在机器人私聊中完成4分钟有效的算术验证。
6. Telegram 批准成功后才记有效邀请、给邀请者奖励；重复加入不重复奖励，同一人不能奖励自己。
7. 普通成员（不是群管理员）发链接，开启拦截时机器人应删除并留下日志。管理员豁免拦截。
8. 在看板修改欢迎英文文案/HTTPS 图片地址或 Telegram file_id、签到与邀请奖励数，再测试之后的新事件。

验证仅覆盖机器人处理的入群申请。其他直接入群链接、管理员直接拉人或手动批准可能绕过验证；
在确认测试通过后再按实际需求撤销其他直接入群链接。不要拿生产主群第一次试配置。
文字只向 Telegram 发送英文；对图片中的语言不做 OCR 检查，运营者需自行选用英文或无文字图片。

排障在服务器执行 `sudo journalctl -u coinw -n 40 --no-pager`，不要展示 /etc/coinw/coinw.env 内容。
出现已有 Webhook 或409冲突，先确定原部署的用途与停止方案，再切换；不要自动删除或强占。

邀请命令以 /link 为准，/ink 保留为兼容别名，两者复用同一用户的有效邀请链接。

## 已部署服务器更新 /link

在腾讯云终端执行下面整段。无需重新输入 Token 或管理员密码。
更新器仅替换 app.py 与 static/app.js，先校验固定版本 SHA256 并备份旧代码；
服务启动检查失败时恢复旧代码。已有账号、Token、签到和邀请数据保留。
原内置欢迎词中的 /ink 会改为 /link，管理员自定义的欢迎词保持原样。

```bash
cd /home/ubuntu &&
curl -fL --retry 3 https://raw.githubusercontent.com/lexppp/yang/refs/heads/deploy/hosted-demo/deploy/update_link.py -o update_link.py &&
sudo python3 update_link.py
```

更新后在群内发送 /link 测试。需要在 Telegram 菜单显示它时，在官方 BotFather 的
/setcommands 中把邀请项设为 `link - Get your personal invite link`。

## 获取欢迎图片的 file_id

安装当前更新后，在 Telegram 私聊自己的机器人，点击 Start，并按「照片」方式发送图片。
机器人会用英文返回 `Photo file_id:` 和对应标识；也可以回复照片发送 `/fileid`。
复制标识本身，填入看板「社群管理 → 设置 → 欢迎图片」，保存设置。
无需下载 Telegram 文件，不需要 Token 或额外图床。
以「文件」方式发送的图片不适用于这个 sendPhoto 欢迎图字段，机器人会提示改为照片发送。
群内照片不会自动回显 file_id。图片中的语言仍须运营者自行检查。

已部署服务器可再次下载并执行上面的 update_link.py 更新器；当前更新同时包含 /link 和 /fileid。
它保留账号、Token 与现有积分/邀请数据，短暂重启 coinw 服务。
