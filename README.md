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
docker exec steamgifts-qinglong python3 -c 'import requests, bs4, curl_cffi'
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

## 游戏质量与点数分配

机器人只考虑同时满足以下条件的 Steam 游戏：

- Steam 总评测数不少于 `100`；
- 好评率（向下取整）不少于 `70%`；
- Steam 公开评测摘要可正常读取。

缺少 App ID、评测不可用或未达到硬门槛的赠品会直接跳过。合格候选收集完成后，机器人先只使用好评率不低于 `80%` 的游戏，通过 0/1 背包选择不超过当前点数且总花费最大的组合；如果无法把余额降到 `50` 以下，再依次把门槛放宽到 `75%`、`70%`。平局时依次比较最低好评率、总评测数和赠品代码。某次参加失败后，会刷新余额并只在剩余候选中重新计算，且不会突破 `70%` 的硬下限。

门槛位于本地 `settings.cfg`：

```ini
preferred_positive_percent=80
min_positive_percent=70
min_review_count=100
target_remaining_points=50
```

配置可以把门槛调得更严格，但不能把优先好评率降到 `80%` 以下、把硬下限降到 `70%` 以下、把总评测数降到 `100` 以下，或把目标余额提高到 `50` 以上。

## macOS 保活检测

watchdog 每 5 分钟检查 Docker、容器健康、面板、青龙任务配置、任务新鲜度、卡住的进程和机器人心跳。安装命令：

```bash
./scripts/install-watchdog.sh
```

安装器先执行一次无写入检查，再生成并加载 `~/Library/LaunchAgents/com.guiyu.steamgifts-qinglong-watchdog.plist`。它会把 Python、Docker、项目和日志位置解析为绝对路径，适用于 `launchd` 的最小环境。

常用检查：

```bash
launchctl print gui/$UID/com.guiyu.steamgifts-qinglong-watchdog
python3 scripts/watchdog.py --project-dir "$PWD" --check-only --no-notify
tail -n 50 data/watchdog/watchdog.log
```

运行状态保存在 `data/watchdog/state.json`，机器人心跳保存在 `data/watchdog/bot-heartbeat.json`，LaunchAgent 输出位于 `data/watchdog/launchd.stdout.log` 和 `data/watchdog/launchd.stderr.log`。这些路径均被 Git 忽略。

| 检测结果 | 自动动作 |
| --- | --- |
| 容器缺失或停止 | `docker compose up -d` |
| 容器不健康或面板不可用 | 重启一次青龙服务 |
| 任务缺失、禁用或配置漂移 | 创建、启用或修复唯一同名任务 |
| 超过 90 分钟无运行且无进程 | 触发一次任务 |
| `sg.py` 运行超过 50 分钟 | 停止该青龙任务 |
| SteamGifts 认证或 Cloudflare 阻断 | 不重启、不重复运行，只在状态变化时通知 |
| Steam 评测服务连续失败 | 保持停止参加并在连续失败后通知 |

watchdog 只使用 `127.0.0.1` 上的青龙 API。青龙访问令牌过期时，它会在容器内调用青龙自带的本地令牌脚本刷新令牌；令牌不会写入 watchdog 状态或日志。SteamGifts Cookie 不会自动获取，认证阻断后仍需在浏览器完成验证并手动更新本地配置。
