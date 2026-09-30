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

## 运行方式

机器人脚本位于 `src/steam_gift`，挂载到容器内的 `/ql/data/scripts/steam_gift`。脚本每次只扫描一轮；青龙使用 `0 * * * *` 每小时触发一次，并用 `flock` 防止任务重叠。

SteamGifts 的 `PHPSESSID` 与浏览器 `User-Agent` 只保存在被 Git 忽略的 `src/steam_gift/settings.cfg`。该文件应保持 `0600` 权限，不要提交或粘贴到日志。

自动参加 SteamGifts 抽奖存在账号受限或封禁风险。启用任务前应先完成非写入式登录状态检查。
