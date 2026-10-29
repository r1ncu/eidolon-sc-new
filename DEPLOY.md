# 部署到 PVE 宿主机（Docker）

两个容器：`scbot`（对话内核 + QQ 群通道）和 `scbot-napcat`（登录着 QQ 小号的 NapCat）。
数据在 Docker 卷里，代码在镜像里，重启用 `docker compose up -d` 就够。

> 只在**一个群**里说话，只回应**被 @**，不做私聊。所有开关在 `.env`。

---

## 0. 这个部署长什么样

```
PVE 宿主机 your-host
├── docker
│   ├── scbot:1.2.0        :8100（只绑 127.0.0.1）  网页端 + QQ 通道
│   │     └── 卷 scbot-data → /data（scbot.db、media/）
│   └── napcat             :6099（只绑 127.0.0.1）  WebUI，扫码登录用
│         └── ws :3001  ←── 只在 compose 网络里，不对外
└── 群里 @ 她 → NapCat → ws://napcat:3001 → 内核 → 回复
```

两个端口都只绑 `127.0.0.1`：网页端用 SSH 隧道看，WebUI 用 SSH 隧道扫码。
OneBot 的 3001 根本不发布到宿主机。

---

## 1. 装 Docker（PVE 8 / Debian 12）

```bash
apt-get update
apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker --version && docker compose version
```

两点提醒：

- **PVE 宿主机装 Docker 会往 iptables 里加链**。端口只绑 `127.0.0.1` 时不会和 `pve-firewall` 打架；
  不要图省事把 6099 映射成 `0.0.0.0`，那等于把 QQ 登录入口挂到局域网上。
- 拉不动 Docker Hub 时，给 **Docker daemon** 配代理或镜像源（NapCat 官方文档也强调：
  GitHub 加速地址对镜像仓库无效）。写在 `/etc/docker/daemon.json`，然后
  `systemctl restart docker`。

---

## 2. 把代码放上去

在开发机上打包（排除本地库、密钥、缓存）：

```bash
tar czf scbot-qq.tgz \
  --exclude='__pycache__' --exclude='.env' --exclude='*.db*' --exclude='media' \
  -C /path/to/localbot-sc scbot-new
scp scbot-qq.tgz root@your-host:/opt/
ssh root@your-host 'mkdir -p /opt/scbot-docker && tar xzf /opt/scbot-qq.tgz -C /opt/scbot-docker --strip-components=1'
```

结果：`/opt/scbot-docker/{Dockerfile,docker-compose.yml,serve.py,channels/,characters/,...}`

---

## 3. 填 `.env`

```bash
cd /opt/scbot-docker
cp .env.example .env
chmod 600 .env
vi .env
```

必填五项：

| 变量 | 填什么 |
|---|---|
| `CHAT_LLM_BASE_URL` / `CHAT_LLM_API_KEY` / `CHAT_LLM_MODEL` | 对话模型（任何 OpenAI 兼容服务） |
| `QQ_SELF_ID` | **小号**的 QQ 号（机器人自己） |
| `QQ_GROUP_IDS` | 那个群的**群号**（不是群名）。先不挂群就留空，改用下面的私聊 |
| `QQ_OWNER_IDS` | 你自己的 QQ 号——她据此决定叫你「プロデューサーさん」还是「あなた」 |
| `QQ_IGNORE_IDS` | 空 | 完全不理的号：不进上下文，也不回 |
| `QQ_REST_TZ` | `Asia/Tokyo` | 作息按哪个时区算（她是日本人）。只影响作息，不影响额度与空间 |
| `QQ_HOLIDAYS` / `QQ_WORKDAYS` | 空 | 日历纠正（`YYYY-MM-DD`）：这些天按休息日 / 上班算。只影响她**主动开口**的时段，不影响 @ 和私聊 |
| `QQ_BOTS` | 空 | 别的角色的机器人：`号码=角色id`（如 `3824843517=luca`）。她据此按「认识的人」而不是陌生人接话 |
| `QQ_PRIVATE` / `QQ_PRIVATE_IDS` | 想先一对一测就把 `QQ_PRIVATE=1`，名单填你的号；私聊里没有外人，她用平时的语气（不是「ふゆ」） |
| `NAPCAT_WEBUI_TOKEN` | 自己设一个强口令，NapCat 的 WebUI 用它登录 |

还有一项决定「她跟陌生人熟不熟」，不是必填：

| 变量 | 默认 | 说明 |
|---|---|---|
| `SCCORE_AFFINITY_START` | `20` | 群里**没说过话的人**起步的好感度。40 是「信任」，所以默认落在「疏远」：陌生人听到的是营业声线（夹子），聊熟了才慢慢爬到「信任」，那时候私下的短句（「ハ？」「バーカ」）才会出来。静置回升也停在这个值——几天没人说话不会自己变成熟人。页面和私聊不受影响（仍是 50） |
| `SCCORE_CALL` | `auto` | 电话这件事**跟着语音模型走**：`auto` = 配了 `FISH_AUDIO_API_KEY` 就能打（页面画出电话键、她也会主动提「想听你的声音」），没配就打不通（键不画、她也不会提，因为叫不来的电话是句空头承诺）。`off` = 手动闸，配了语音也不打。`on` = 强制允许（没有语音时只剩看提示词的意义）。**这条不是人设里的死规矩**：写在 `fuyuko-dm.md` 里的「电话打不通」配上语音以后仍然在，所以它每轮现问一次（`App.call_ready()`，页面用的也是同一个答案） |

#### QQ 空间（可选，默认全关）

| 变量 | 默认 | 说明 |
|---|---|---|
| `QQ_QZONE` | `0` | 自己发说说。打开前先写 `characters/world/<QQ_CHARACTER>-qzone.md`（写作口径），没有就拒发 |
| `QQ_QZONE_HOUR` | `21` | 几点之后可以发，按 `SCCORE_TIMEZONE` 算（不是容器时区） |
| `QQ_QZONE_P` | `0.35` | 过了点之后今天写不写的概率。**`0` = 永远不会发**；`1` = 每天都发 |
| `QQ_QZONE_RIGHT` | `4` | 可见范围：`1` 所有人 / `4` 好友 / `16` 部分好友 / `64` 仅自己 / `128` 部分好友不可见 |
| `QQ_QZONE_GAP` | `20` | 两条之间至少隔几小时 |
| `QQ_QZONE_REPLY` | `0` | 回**自己说说下**别人留的评论（不是好友动态）。**评论发出去不能撤回**（删评论接口在真机 500） |
| `QQ_QZONE_REPLY_HOURLY` / `_GAP` / `_AGE` | `6` / `5` / `72` | 滚动一小时最多几条 / 两条之间至少几分钟 / 只回几小时以内的 |
| `QQ_QZONE_WEB` | `1` | 读说说/评论走 QZone Web 接口（用 NapCat 的 Cookie）。设 `0` 等于关掉回评论 |
| `QQ_QZONE_REPLY_POSTS` | `3` | 看最近几条说说下面的评论 |
| `QQ_QZONE_REPLY_IGNORE` | 空 | 不回谁的评论（QQ 号，逗号分隔） |

验有没有接上：

```bash
curl -s 127.0.0.1:8100/api/qzone | python3 -m json.tool          # 自己的墙：昨天发没发、为什么没发
curl -s 127.0.0.1:8100/api/qzone/comments | python3 -m json.tool # 自己的墙：谁评论了、回了什么
curl -s -X POST 127.0.0.1:8100/api/qzone/poll                    # 现在看一次有没有新评论
curl -s -X POST 127.0.0.1:8100/api/qzone -d '{"publish": false}'  # 只写不发，预演一条说说
```

---

### 改人设 / 改规则（不用重启）

`characters/` 通过 bind mount 挂在容器里的 `/app/characters`（compose 里那一行
`- ./characters:/app/characters`），所以在宿主机上直接编辑 `/opt/scbot-docker/characters/...`
就是改容器里的那份。人设、语域、人名册、作品知识都算，下一轮对话就生效（文件要安静 0.35 秒才被采用；空文件会被忽略，
保留上一版）。想立刻确认，跑：

```bash
curl -s -X POST http://127.0.0.1:8100/api/qq/reload
```

人群里的语气档位也在同一个文件里（文件末尾的 `【阶段 name｜第几次开始】`）：改门槛或改文案都是改数据，
下一轮生效。想看自己现在在第几档，群里发 `/阶段`；想从头体验「初次见面」那一档，
把 `.env` 的 `QQ_OWNER_STAGE` 设为空、重启，再 `/阶段 重置`。

返回 `{"dropped": 2, "sheet": true, "generation": 7}` —— `dropped` 是丢掉的提示词缓存数，
`generation` 是重读次数。`/api/qq/status` 里也能看到它。

注意这两个文件在卷上，改完**不要** `docker compose down -v`，那会把卷一起删掉。

## 4. 起容器

```bash
cd /opt/scbot-docker
docker compose up -d --build
docker compose logs -f scbot
```

正常时日志里会出现两行：

```
QQ 通道已启动：群 123456789 / 角色 fuyuko / ws://napcat:3001
onebot: connected
```

`docker compose ps` 里 `scbot` 应该是 `healthy`（镜像自带 healthcheck，探 `/api/roster`）。

---

## 5. 登录 QQ 小号（只做一次）

WebUI 只绑在宿主机回环上，所以在**你自己的电脑**上开隧道：

```bash
ssh -L 6099:127.0.0.1:6099 root@your-host
```

浏览器打开 `http://127.0.0.1:6099/webui?token=你设的口令`，用**小号**扫码登录。

然后在 WebUI 里确认两件事（`MODE=ws` 已经起了正向 WebSocket）：

1. 网络配置 → **WebSocket 服务端** 开启，端口 `3001`，监听 `0.0.0.0`
2. 如果设了 token，把同一个值写进 `.env` 的 `QQ_ONEBOT_TOKEN`，然后
   `docker compose up -d scbot` 让它重连

> 小号第一次登录可能需要过验证（滑块、设备锁）。这一步只能手动。登录态存在
> `napcat-qq` 卷里，重启容器不用重扫。

---

## 6. 把她拉进群

用主号把小号拉进那个群（50 人那个）。不需要给管理员权限。

进群之后在群里发一条：

```
@小号 ふゆ、こんばんは
```

第一条回复会带 @ 和引用，后面几段是连发的。如果没反应，看下一节。

---

## 7. 验证与排查

```bash
curl -s 127.0.0.1:8100/api/qq/status | python3 -m json.tool
```

```json
{
  "enabled": true, "connected": true, "groups": ["123456789"],
  "people": 1, "turns": 1, "sent": 2, "failed": 0,
  "events": 37, "last_error": "", "problem": ""
}
```

| 现象 | 看哪里 | 多半是 |
|---|---|---|
| 日志没有「QQ 通道已启动」 | `problem` 字段 | `.env` 少了 `QQ_SELF_ID` / `QQ_GROUP_IDS`，或 `QQ_ENABLED` 不是 1 |
| 有「已启动」但没有 `onebot: connected` | `last_error` | 容器网络不通，或 NapCat 的 WS 服务端没开 |
| 有 `connected`，`events` 一直是 0 | — | 群号填错（填成群名了），或机器人不在群里 |
| `events` 在涨，但它不回 | 日志里的 `qq:` 行 | 没 @ 它；`QQ_SELF_ID` 填成主号了；20 秒冷却；额度用光 |
| 回了，但语气像客服 | 她收到的是 `fuyuko-qq.md` | 这是群聊该有的「ふゆ」语域；私聊才是本性 |
| 群里叫它没反应，私聊有 | — | 设计如此：**私聊不响应** |
| 语音没声音 | — | 语音条还没做（`QQ_VOICE=0`） |

---

## 8. 日常运维

```bash
cd /opt/scbot-docker
docker compose logs -f scbot            # 她的日志
docker compose restart scbot            # 只重启内核，NapCat 登录态不动
docker compose down                     # 全停（卷保留）
docker compose up -d --build            # 更新代码后重建
```

**备份**（数据库 + 图片，都在卷里）：

```bash
mkdir -p /opt/scbot-docker/backups
docker run --rm -v scbot-docker_scbot-data:/data:ro \
  -v /opt/scbot-docker/backups:/backup alpine \
  tar czf /backup/scbot-data-$(date +%F).tgz -C /data .
```

**恢复**：先 `docker compose stop scbot`，把文件解回卷，再起。

**升级 NapCat**：`docker compose pull napcat && docker compose up -d napcat`。
NapCat 与 QQ 客户端版本必须匹配，升级后如果登不上，把镜像 tag 固定回旧版本。

---

## 9. 不想接 QQ，先看效果

不碰 NapCat，用假 NapCat + 假模型把整条链路跑一遍：

```bash
# 终端 A：一个会 @ 她的假 NapCat
python3 tools/fake_napcat.py --port 3001 --group 100 --self 999 --user 42

# 终端 B：内核 + 通道，假模型（不需要 key）
QQ_ENABLED=1 QQ_ONEBOT_WS=ws://127.0.0.1:3001 QQ_SELF_ID=999 \
QQ_GROUP_IDS=100 QQ_OWNER_IDS=42 QQ_MERGE_SECONDS=0.2 \
SCCORE_DB=/tmp/smoke.db SCCORE_MEDIA=/tmp/smoke-media \
python3 serve.py --fake --port 8100
```

终端 A 会打印她发回来的每一条和它的段落类型（`['reply','at','text','text']`），
终端 B 会打印 `onebot: connected`。这一步不需要 QQ、不需要模型、不花钱。
