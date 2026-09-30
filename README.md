# SteamGifts Qinglong

本项目在独立的本地青龙容器中运行 SteamGifts 自动抽奖脚本。面板只监听 `127.0.0.1:5700`，不会向局域网公开。

## 容器操作

```bash
docker compose build --pull
docker compose up -d
docker compose ps
docker compose logs -f qinglong
```

启动后访问 <http://127.0.0.1:5700/>。停止容器但保留面板数据：

```bash
docker compose down
```

首次启动后，在本地面板完成管理员初始化。账号密码只在面板中设置，不应写入仓库或操作记录。

## 健康检查

```bash
docker compose ps
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5700/
docker exec steamgifts-qinglong python3 -c 'import requests, bs4'
docker exec steamgifts-qinglong sh -lc 'command -v flock'
```

面板应返回 HTTP 200，容器端口应显示为 `127.0.0.1:5700->5700/tcp`。

## 运行方式

机器人脚本位于 `src/steam_gift`，挂载到容器内的 `/ql/data/scripts/steam_gift`。脚本每次只扫描一轮；青龙使用 `0 * * * *` 每小时触发一次，并用 `flock` 防止任务重叠。任务命令为：

```bash
flock -n /tmp/steamgifts.lock bash -lc 'cd /ql/data/scripts/steam_gift && python3 sg.py'
```

SteamGifts 的 `PHPSESSID` 与浏览器 `User-Agent` 只保存在被 Git 忽略的 `src/steam_gift/settings.cfg`。如果只用这两个字段会触发 Cloudflare 挑战，还需要填写同一浏览器会话中的 `cf_clearance`；未遇到挑战时保持为空即可。该文件应保持 `0600` 权限，不要提交或粘贴到日志。

首次配置可以复制占位模板后在本地编辑：

```bash
cp src/steam_gift/settings.cfg.example src/steam_gift/settings.cfg
chmod 600 src/steam_gift/settings.cfg
git check-ignore src/steam_gift/settings.cfg
```

启用青龙任务前，必须用配置中的 Cookie 对 `https://www.steamgifts.com/account/settings/profile` 发起一次只读 GET。只有请求未跳转、最终路径仍为 `/account/settings/profile` 且没有站点挑战时，才视为认证有效。若返回 Cloudflare 挑战，先在相同 `User-Agent` 的浏览器中完成验证，再将新的 `cf_clearance` 写入本地配置。检查时不得输出 Cookie、请求头或网页正文。

自动参加 SteamGifts 抽奖存在账号受限或封禁风险。启用任务前应先完成非写入式登录状态检查。
