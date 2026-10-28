# 03 · NapCat（QQ 那一半）

这个插件不会自己连 QQ。QQ 的登录、协议、掉线重连都由 [NapCat](https://github.com/NapNeko/NapCatQQ)
负责，它把 QQ 的消息转成 [OneBot v11](https://github.com/botuniverse/onebot-11) 的 JSON，
通过 WebSocket 交给我们。

所以关系是这样：

```
QQ 服务器  ←→  NapCat（登录着你的小号）  ←─ WebSocket ─→  channels/（这个插件）
                                        你配的就是这一根线
```

**这一层不带进包里**，因为它要扫码登录、要保存登录态，属于"你的账号"而不是"这个项目"。

## 两种连法，我们只要一种

OneBot 的 WebSocket 有两种方向：

| 方向 | 谁连谁 | 这个插件用吗 |
|---|---|---|
| **正向 WebSocket（服务器）** | 我们去连 NapCat | ✅ 用这个 |
| 反向 WebSocket（客户端） | NapCat 来连我们 | ❌ 不支持 |

插件里的 `QQ_ONEBOT_WS` 是一个**我们去连的地址**。所以 NapCat 那边必须开着
「WebSocket 服务器」，端口要和 `QQ_ONEBOT_WS` 对得上。

## 方式 A：Docker（最快）

一个只跑 NapCat 的 compose 片段：

```yaml
services:
  napcat:
    image: mlikiowa/napcat-docker:latest
    container_name: napcat
    restart: unless-stopped
    # QQ NT 在默认的 seccomp/AppArmor 下会 SIGTRAP 崩掉。这两条不是"为了权限"，
    # 是让它在容器里能启动的最小让步。
    security_opt:
      - seccomp:unconfined
      - apparmor=unconfined
    # Chromium 内核默认的 /dev/shm 只有 64M，登录后的页面会白屏。
    shm_size: 256m
    environment:
      MODE: ws
      WEBUI_TOKEN: 换成一个只有你知道的长字符串
      ACCOUNT: "机器人QQ号"        # 登录成功后填上，重启不用再扫码
      TZ: Asia/Shanghai
    volumes:
      - napcat-qq:/app/.config/QQ
      - napcat-config:/app/napcat/config
    ports:
      - "127.0.0.1:6099:6099"      # WebUI：扫码用
      - "127.0.0.1:3001:3001"      # OneBot WebSocket：插件连这个
    networks: [scbot]

volumes:
  napcat-qq:
  napcat-config:

networks:
  scbot:
```

如果 scbot 也在 compose 里，`QQ_ONEBOT_WS` 写服务名：`ws://napcat:3001`，
并且两个服务要在同一个 `networks` 里。

起容器：

```bash
docker compose up -d napcat
docker compose logs -f napcat | head -50
```

日志里会出现 WebUI 地址和 token。浏览器（或 SSH 隧道）打开 `http://127.0.0.1:6099`
→ 用 `WEBUI_TOKEN` 登录 → **二维码** → 手机 QQ 扫码。

> 小号建议：不要用主号。QQ 对"新设备 + 自动化"有风控，刚登录的号可能要求短信验证，
> 也可能在几天后被要求重新登录。这不是插件能解决的问题。

## 方式 B：已经有 NapCat

打开 NapCat 的 WebUI → **网络配置** → **添加配置** → 选 **WebSocket 服务器**：

| 项 | 值 |
|---|---|
| 名称 | 随便（`scbot`） |
| 端口 | `3001`（或者你喜欢的，要和 `.env` 一致） |
| Host | `0.0.0.0`（同机可以 `127.0.0.1`） |
| Token | 留空，或设一个，然后填到 `QQ_ONEBOT_TOKEN` |
| 消息格式 | `array`（默认） |
| 启用 | ✅ |

保存后**重启 NapCat**，然后确认端口确实在听：

```bash
curl -sv http://127.0.0.1:3001 2>&1 | head -5
# 出现 "Empty reply" 或 upgrade required 是正常的：它是 WebSocket，不是 HTTP 页面
# "Connection refused" 就是没起来 / 端口不对
```

## 验证这根线

不用启动内核也能测：

```bash
python - <<'EOF'
import asyncio, sys
sys.path.insert(0, "/path/to/scbot-new")      # 换成你的内核目录
from channels.onebot import OneBot

async def main():
    client = OneBot("ws://127.0.0.1:3001", "", on_log=print)
    task = asyncio.create_task(client.run(lambda event: None))
    for _ in range(50):                       # 最多等 10 秒
        await asyncio.sleep(0.2)
        if client.connected:
            break
    if not client.connected:
        print("连不上：地址 / 端口 / token 有一个不对，或者 NapCat 没开「WebSocket 服务器」")
        print("last_error:", client.last_error)
    else:
        print("登录的是：", await client.call("get_login_info"))
    await client.stop()
    task.cancel()

asyncio.run(main())
EOF
```

打出 QQ 号和昵称，说明线是通的；`last_error` 里通常直接写着原因（401、Connection refused、
handshake 失败）。

## 三个最常见的坑

1. **端口对但 token 不对** —— 日志里是 `401` / `403`，或者连上又立刻断。检查
   `QQ_ONEBOT_TOKEN` 与 NapCat 里那个配置的 Token **完全一致**（包括空格）。
2. **NapCat 容器崩** —— 日志 `Trace/breakpoint trap`。缺 `seccomp:unconfined` 和
   `shm_size: 256m`，照上面的 compose 抄。
3. **网页能登，OneBot 连不上** —— 登的是 WebUI，不是开了 OneBot。WebUI 登录 ≠ 开了
   WebSocket 服务器，两个是独立的配置页。

## 用 docker compose 一起跑的时候

scbot 那边只需要两件事：

```yaml
environment:
  QQ_ONEBOT_WS: ws://napcat:3001
depends_on:
  - napcat
networks:
  - scbot
```

`3001` 不需要发布到宿主机（插件在容器网络里连服务名），只把网页端和 WebUI 绑到
`127.0.0.1` 就够。
