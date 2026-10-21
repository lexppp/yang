# 腾讯云 Ubuntu 24.04 部署演示站

此包提供网站演示：登录、管理员账号、密码和功能授权保存在服务器 SQLite 中；
积分、邀请、抽奖及公告是本浏览器模拟数据，不会同步到 Telegram，关闭浏览器后不执行定时任务。
不同浏览器的业务演示数据互不共享。它不是完整的社群生产系统。

## 1. 上传和安装

在腾讯云实例点击「登录」，通过 OrcaTerm / 浏览器终端上传 `CoinW-server-demo.tar.gz` 到用户主目录。
下列命令从上传目录执行：

```bash
tar -xzf CoinW-server-demo.tar.gz
cd CoinW-server-demo
sudo bash deploy/install.sh
```

脚本会在服务器内要求输入两次网站管理员密码（输入时不显示，至少12位）。
用户名是 `admin`。密码不需要发给任何人。不要开启 shell 的 `set -x` 调试输出。
安装器创建专用系统用户和开机启动服务，不读取上传目录的 `.env`，不包含开发环境密码。
重复运行保留 `/etc/coinw/coinw.env` 与数据库；不会通过重跑脚本重置管理员密码。

## 2. 绑定域名并开启 HTTPS

先购买普通域名，在 DNS 添加一个 A 记录，例如 `panel` 指向服务器公网 IPv4。
腾讯云实例「防火墙」放行 TCP 80、443；SSH 22 尽量仅允许你的管理来源。
不要开放 8000 或数据库端口。

安装 HTTPS 反向代理（Ubuntu 软件源，保留签名校验）：

```bash
sudo apt-get update
sudo apt-get install -y caddy
sudo nano /etc/caddy/Caddyfile
```

若是新服务器，将默认示例站点替换为下面内容，把 `panel.example.win` 换成自己的域名。
已有其他网站时应新增站点块并保留原有配置。

```caddy
panel.example.win {
    encode gzip
    reverse_proxy 127.0.0.1:8000
}
```

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl enable --now caddy
sudo systemctl reload caddy
```

DNS 生效、80/443 可达后，Caddy 会自动申请和续期证书。
打开 `https://你的实际域名`，使用 `admin` 和安装时自己设置的密码登录。
COOKIE_SECURE=1，因此不要改用 HTTP 来登录。域名尚未购买时先停留在本地健康检查步骤。

## 3. 检查与备份

```bash
sudo systemctl status coinw --no-pager
sudo journalctl -u coinw -n 30 --no-pager
```

需备份账号时，先停止 coinw 服务，再将 `/var/lib/coinw` 和 `/etc/coinw` 备份到受保护位置，随后启动服务。
备份含敏感账号信息，不要上传公开仓库或发到聊天。
浏览器中的业务演示数据不在服务器备份中。

本部署包已做本地浏览器登录和权限检查；安装器语法已检查。
腾讯云实际安装、DNS 和公网 HTTPS 需在用户服务器执行后验证，不代表已上线。
