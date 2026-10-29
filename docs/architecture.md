# scbot-new 架构设计与部署教学

> 面向读者：要接手、改造或重新部署这个项目的人。
> 目标：读完能说清「一条消息从进来到发出去，中间经过了谁」，并能在空白机器上把它跑起来。
>
> 本文只描述**代码里现在是这样**的东西。设计上的取舍与来龙去脉在 `README.md`；
> 最短路径的 QQ 单群部署在 `DEPLOY.md`；通道作为独立插件包分发时的说明在 `docs/qq-channel/`。

---

## 目录

- [0 速览](#0-速览)
- [1 整体架构](#1-整体架构)
- [2 内核 core/](#2-内核-core)
- [3 插件 plugins/](#3-插件-plugins)
- [4 QQ 通道 channels/](#4-qq-通道-channels)
- [5 Web 端](#5-web-端)
- [6 数据流全景](#6-数据流全景)
- [7 运行环境与依赖](#7-运行环境与依赖)
- [8 部署步骤](#8-部署步骤)
- [9 配置参考](#9-配置参考)
- [10 常见问题](#10-常见问题)
- [11 注意事项](#11-注意事项)
- [附录 A 测试与工具](#附录-a-测试与工具)
- [附录 B 命令速查](#附录-b-命令速查)

---

## 0 速览

**它是什么**：一个用插件拼出来的「角色扮演对话服务」内核，跑在单个进程里。
两个入口共用它：浏览器里的网页对话，和 QQ 群 / 私聊里的机器人。
二者不是两套代码，是同一个回合循环的两个调用方。

| 项 | 数值 | 出处 |
|---|---|---|
| Python 代码 | 约 21,800 行（其中测试约 8,000 行） | `find . -name '*.py' \| xargs wc -l` |
| 第三方运行时依赖 | **0** | `pyproject.toml` 的 `dependencies = []` |
| 内核模块 | 12 个文件 + `protocol/` 3 个 | 见 §1.4 |
| 插件 | 11 个 + 1 个通道 | `plugins/`、`channels/` |
| 数据表 | 21 张（内核 3 + 插件 11 + 通道 7） | 见 §2.6 |
| 角色 | 28 位（1 位有完整私有资料） | `characters/characters.toml` |
| 单元测试 | 765 条 | `python -m unittest discover -s tests -t .` |
| 容器 | 2 个（内核 + NapCat） | `docker-compose.yml` |

三条贯穿全项目的设计纪律，先记住这三条，后面所有设计都是它们的推论：

1. **内核不认识功能。** `core/` 里没有任何一行 import 了「好感度」「QQ」「礼物」。
   功能的全部知识在 `plugins/` 和 `channels/`，内核只知道「这里有三种钩子」。
2. **插件坏了不能让人不说话。** 任何插件抛异常、缺依赖、配置没给，结果是**它自己下线**，
   回合继续。这条在 `Host` 里是硬编码的，不是约定。
3. **会重启的机器上，状态不能只在内存里。** 定时任务的「下一次该什么时候」、
   「今天发过没有」，全部落 `qq_flags` 表。宿主机半夜重启三次，行为不变。

---

## 1 整体架构

### 1.1 三层

```
┌──────────────────────────────────────────────────────────────┐
│  入口层（谁在跟它说话）                                        │
│                                                              │
│   serve.py ── HTTP/SSE ──► 浏览器（web/）                     │
│      └─ channels/plugin_qq.py ── WebSocket ──► NapCat ──► QQ  │
└───────────────────────────┬──────────────────────────────────┘
                            │ App.turn(...)  ← 唯一入口
┌───────────────────────────▼──────────────────────────────────┐
│  组装层  app.py                                               │
│    一个 Store、一个 Host、每位角色一个 Engine                    │
│    把「角色数据 + 插件列表 + 模型」装成可服务的东西              │
└───────────────────────────┬──────────────────────────────────┘
┌───────────────────────────▼──────────────────────────────────┐
│  内核层  core/                                                │
│    回合 turn.py │ 插件协议 plugin.py │ 调度 host.py            │
│    提示词 prompt.py │ 判分 judge.py │ 存储 store.py …          │
└───────────────────────────┬──────────────────────────────────┘
┌───────────────────────────▼──────────────────────────────────┐
│  插件层  plugins/ （11 个功能）                                │
│   characters recall stories quota photos affinity             │
│   memory gifts profile voice status                           │
└──────────────────────────────────────────────────────────────┘
```

**依赖方向是单向的**，这是整个项目最重要的一条约束：

```
serve.py / channels/ ──► app.py ──► core/   （core 不知道上面两层存在）
plugins/ ─────────────► core/
```

`core/` 只能 import `core/`。它不是「约定」而是可以检查的事实：

```bash
grep -rn "^from channels\|^import channels\|^from app " core/     # 应该没有输出
```

反过来说，`channels/` 和 `serve.py` 都只通过 `App` 的公开方法说话（`app.turn`、
`app.state`、`app.call_ready` …），不直接摸 `core` 的内部。

### 1.2 运行时拓扑

```
                    ┌──────────────── 宿主机 ────────────────┐
  你的电脑           │                                        │
  ┌────────┐  SSH   │  ┌──────────────┐   compose 网络         │
  │ 浏览器  │───────┼─►│ 127.0.0.1:8100│  ┌────────┐          │
  └────────┘ 隧道    │  │   scbot      │  │ napcat │          │
                     │  │  ├ serve.py  │  │ :3001  │◄── ws ───┤
                     │  │  └ QQ 通道   │──┘        │          │
                     │  └──────┬───────┘  └────┬───┘          │
                     │         │ 卷 scbot-data │ 卷 napcat-qq   │
                     │         │ /data          │ 登录态         │
                     └─────────┴───────────────┴───────────────┘
```

要点：

- **只有两个端口发布到宿主机，且都绑 `127.0.0.1`**：`8100`（网页 + API）、`6099`（NapCat WebUI）。
  OneBot 的 `3001` 从不发布，只在 compose 网络里，靠服务名 `napcat` 访问。
- 网页通过 SSH 隧道看；NapCat WebUI 也通过隧道扫码。没有一条路把 QQ 登录入口交给局域网。
- 数据两个卷：`scbot-data`（`scbot.db` + 图片 + 表情包）、`napcat-qq`（QQ 登录态，重启不重扫）。
- **`characters/` 是 bind mount**，不是镜像内容。改人设不用重建镜像，详见 §11。

### 1.3 一个进程里发生了什么

`serve.py` 是单进程、多线程：

```
主线程     ThreadingHTTPServer.serve_forever()   ← 每个 HTTP 请求一个线程
           └ 处理函数里用 asyncio.run(...) 跑一轮（本进程内新建事件循环）
QQ 线程    threading.Thread(name="qq", daemon=True)
           └ 一个长期事件循环：WebSocket 收事件、定时任务、发送
定时任务   都在 QQ 线程的那个循环里，用 asyncio.create_task 起，`stop()` 时统一取消
```

这个结构是刻意的、也是唯一的妥协：HTTP 层用的是标准库的线程服务器（零依赖），
而 QQ 需要长期连接和流式等待（必须 async）。两者用**一个独立线程 + 独立事件循环**隔开。
好处是 QQ 那侧崩了不影响网页；代价是两边共享一个 SQLite，所以 `Store` 里有一把全局锁（§2.6）。

### 1.4 目录地图

```
scbot-new/
├── serve.py                  HTTP 服务入口：路由、SSE、静态文件
├── app.py                    组装：Store + Host + 每角色 Engine；网页要的聚合视图
├── demo.py                   不要 key、不启服务，跑两轮看内核怎么工作（教学用）
├── Dockerfile / docker-compose.yml / .env.example
│
├── core/                     内核（不认识任何具体功能）
│   ├── turn.py               ★ 一次回合的四个阶段
│   ├── plugin.py             ★ 插件协议：Veto / Question / TurnContext / Plugin
│   ├── host.py               ★ 插件调度：排序、隔离、路由合并、能力上报
│   ├── prompt.py             提示词四槽 + 缓存断点 + 历史裁剪
│   ├── judge.py              判分总线：一次请求回答多个问题
│   ├── store.py              SQLite：三张表 + 插件迁移入口
│   ├── settings.py           环境变量 → Settings（含 .env 读取）
│   ├── live.py               可热加载的文件（人设/规则改完下一轮生效）
│   ├── llm.py                OpenAI 兼容客户端 + 测试用的 ScriptedLLM
│   ├── events.py             回合事件词汇表
│   ├── messages.py           Conversation / Message 数据结构
│   └── protocol/             回复格式：解析 <ja>/<zh>、语气提示、照片标记
│
├── plugins/                  11 个可开关的功能
│   ├── characters.py         角色名册与头像
│   ├── recall.py             她的资料（off / whole / vector）
│   ├── stories.py            剧情原文按话题检索
│   ├── quota.py              每小时回合数
│   ├── photos.py             图片与每小时额度
│   ├── affinity.py         ★ 好感度 + 生闷气
│   ├── memory.py             关于制作人的长期记忆
│   ├── gifts.py              抽签、背包、重置卡
│   ├── profile.py            制作人档案（称呼/性别/生日/时区）
│   ├── voice.py              语音合成（Fish Audio）
│   └── status.py             服务健康（调用成功率）
│
├── channels/                 QQ 通道（可以整个拿走，内核照常跑网页版）
│   ├── plugin_qq.py        ★ 通道即插件：生命周期、路由、定时循环
│   ├── qq.py               ★ 消息入口、判定、回合、分段发送
│   ├── config.py             QQ_* 配置
│   ├── onebot.py             OneBot 11 客户端（重连 + echo 关联）
│   ├── ws.py                 零依赖 WebSocket 客户端
│   ├── outbox.py             发送队列：分段、节流、失败补发
│   ├── presence.py           语域：场景 / 熟悉度档位 / 好感档位
│   ├── world.py              三层知识：人名册 / 同事档案 / 作品常识
│   ├── interests.py          插话判断（含「她认识的同行」）
│   ├── digest.py             记忆文件：定期整份重写
│   ├── stickers.py           表情包：收录、打标、挑选
│   ├── qzone.py             ★ QQ 空间说说（自己的墙）
│   ├── qzone_feed.py        ★ 别人的说说：拉动态、回评论
│   ├── identities.py         QQ 号 ↔ 内核用户 + 次数/静音/恋爱标记
│   ├── stage.py / texts.py   角色自己的措辞与拒绝台词
│   └── (world/<id>-*.md|toml 在 characters/ 下)
│
├── characters/               角色数据（bind mount，不进镜像更新流程）
│   ├── characters.toml       28 位角色的名册
│   ├── sheets/<id>.md        人设（长篇，进 persona 槽）
│   ├── facts/<id>.md         设定资料（按 SCCORE_RECALL 决定怎么进）
│   ├── facts/<id>-stories/   剧情原文语料（按话题检索）
│   ├── facts/<id>-live/      现实演出数据（CSV + README）
│   ├── world/<id>-qq.md      群聊语域
│   ├── world/<id>-dm.md      私聊语域
│   ├── world/<id>-stage.toml 熟悉度档位、称呼、自称
│   ├── world/<id>-interests.md  插话清单
│   ├── world/<id>-idols.toml    她认识的同行（三档亲疏）
│   ├── world/<id>-stories.toml  剧情话题表（53 个话题）
│   ├── world/<id>-qzone.md      说说写作口径
│   ├── world/<id>-refusals.toml 拒绝台词
│   ├── world/office.md / meta.md / idols/  全角色共用知识
│   └── memory/               自动生成的记忆文件（digest 写入）
│
├── web/                      SPA：index.html + app.js(200KB) + CSS
├── tests/                    765 条测试，全离线
├── tools/                    自检工具（见附录 A）
└── docs/                     本文与通道包文档
```

### 1.5 一张表看清「谁负责什么」

| 你可能想改的东西 | 该动哪里 | 要重启吗 |
|---|---|---|
| 她的性格、口癖 | `characters/sheets/<id>.md` 或 `world/<id>-qq.md` | 不用，下一轮生效 |
| 群里对陌生人的语气 | `world/<id>-qq.md` 的档位段 | 不用 |
| 什么情况下她接话 | `world/<id>-interests.md` | 不用 |
| 她记得的事 | 自动写进 `characters/memory/*.md` | 不用 |
| 开关某个功能 | `.env`（`QQ_AMBIENT` 等） | 要，`up -d` |
| 好感度加减规则 | `plugins/affinity.py` 的数量常量 | 要，重建镜像 |
| 回合流程本身 | `core/turn.py` | 要，重建镜像 |
| 网页长什么样 | `web/assets/app.js` + CSS | 不用，强刷即可 |

---

## 2 内核 core/

内核可以用一句话概括：**给定一段话和一个人，走完一个固定的四阶段流程，把过程以事件流的形式吐出去。**
它不知道角色是谁，也不知道消息从哪来。

那四段是 `before` / `build` / `stream` / `after`（见 `core/turn.py` 的模块注释）。
下面按实现顺序拆成七步，因为「判分」「落库」各自错一步，现象的差别很大。

### 2.1 一次回合的生命周期

入口：`core/turn.py::Engine.turn()`，它是个 async 生成器，产出事件。

```
            ┌─ 0 入口校验 ──────────────────────────────────┐
            │ 空文本？（有图片也算有内容）→ TurnRejected("empty") │
            │ 超过 max_chars(2000)      → TurnRejected("too_long")│
            │ 会话不存在                → TurnRejected("no_conversation")│
            └───────────────────────────────────────────────┘
                              ↓
                      yield UserSaid(text)          ← 事件流的第一个
                              ↓
   ┌─ 1 before：闸门 ─────────────────────────────────────────┐
   │  Host.gate()：依次问每个插件 before_turn()                 │
   │  第一个返回 Veto 的赢，后面的不再问                          │
   │  Veto → TurnRejected(reason, retry_after)，整轮结束         │
   └──────────────────────────────────────────────────────────┘
                              ↓
   ┌─ 2 judge：判分 ──────────────────────────────────────────┐
   │  Host.questions() 收集所有插件的 Question（去重前的一次请求）│
   │  Judge.ask(...) → 结果写进 turn.facts["<key>"]             │
   │  失败 → facts 空，插件自己想办法（都不会因此报错）           │
   └──────────────────────────────────────────────────────────┘
                              ↓
   ┌─ 3 build：组装提示词 ────────────────────────────────────┐
   │  Host.enrich()：每个插件 prompt_sections() 交出自己的段落   │
   │  PromptBuilder.build()：四槽拼装（§2.4）                    │
   └──────────────────────────────────────────────────────────┘
                              ↓
   ┌─ 4 stream：流式生成 ─────────────────────────────────────┐
   │  llm.stream(prompt) 的 chunk 喂给 ReplyParser              │
   │  解析出 SegmentDelta / SegmentComplete / PhotoTagged       │
   │  异常 → TurnFailed(reason)；一个正常段都没有 → TurnFailed  │
   └──────────────────────────────────────────────────────────┘
                              ↓
   ┌─ 5 store：落库 ─────────────────────────────────────────┐
   │  Store.add_turn()：用户那句 + 她的回复，**同一个事务**      │
   │  两半都有或都没有 → 失败时可以原样重发                      │
   └──────────────────────────────────────────────────────────┘
                              ↓
   ┌─ 6 after：观察者 ───────────────────────────────────────┐
   │  Host.observe()：每个插件 after_turn(turn)，此时 turn.reply │
   │  插件可通过 turn.events 追加 PluginEvent                   │
   └──────────────────────────────────────────────────────────┘
                              ↓
                 yield TurnDone(message_id)     ← 结束
```

四条值得记住的细节：

1. **闸门先于判分。** 额度用完的回合不花钱。`quota.before_turn` 只看不写，
   计数在 `after_turn` 里（回复真的存下来了才算一次）。
2. **判分是一次请求回答所有问题。** 五个插件各问一个，仍是一次模型调用（§2.5）。
3. **失败不落库。** 模型超时、被拒、说空话，都不会写入 `messages`，所以用户可以原样重发。
4. **观察者在落库之后。** 所以 `after_turn` 可以读 `turn.reply`（回复的 id、段落、照片标记）。

### 2.2 插件协议：三种钩子

`core/plugin.py` 一共 139 行，是整个扩展机制的**全部**。没有「插件类型」字段，
一个插件是什么，取决于它实现了哪些方法：

| 形态 | 钩子 | 约束 | 典型 |
|---|---|---|---|
| **gate** | `before_turn() -> Veto \| None` | 不许碰提示词；只读判定 | 额度、图片额度、冷淡期 |
| **enrich** | `prompt_sections()`、`judge_questions()` | 应是纯的：同输入同输出 | 好感度、资料、剧情、记忆 |
| **observe** | `after_turn()` | 必须 async、必须自己吞异常 | 好感度记分、记忆抽取、计数 |

```python
class Plugin:
    name: str = ""
    requires: tuple[str, ...] = ()      # 我读谁的 facts / 表

    def capabilities(self) -> dict:      # 前端据此画界面
    def migrations(self) -> Sequence[str]:   # 我自己的 CREATE TABLE
    def routes(self) -> Mapping[tuple[str, str], Route]:  # 我自己的 HTTP 接口
    def judge_questions(self) -> Sequence[Question]:      # 我要判分问什么
    async def before_turn(self, turn) -> Veto | None:
    async def prompt_sections(self, turn) -> Sequence[Section]:
    async def after_turn(self, turn) -> None:
    def start(self) -> None:             # 有生命周期的才有（开线程/起定时器）
    def stop(self) -> None:              # 必须快、必须不抛
```

**插件之间唯一的通信方式是 `TurnContext.facts`。** 键带命名空间（`affinity.level`、
`photos.ids`），谁写谁负责，两个插件写同一个键是 bug，不是合并。

`Section` 有三个字段，第三个决定了插件能把提示词搞坏到什么程度：

```python
Section(slot=Slot.TURN, text="…", order=10, title="affinity")
```

`slot` 只能是 `TURN` 或 `PROFILE`（`_only()` 会检查），也就是说**插件无法往缓存区里塞易变内容**。
这是故意的：破坏前缀缓存会让账单翻倍，而这个错误在代码里看不出来。

### 2.3 Host：排序、隔离、路由

`core/host.py` 做四件事，每件都对应一种「否则会出的错」：

**① 依赖排序（`_ordered`）**
按 `requires` 做拓扑排序，不是注册顺序。`affinity` 要求 `characters`（要角色名）和
`quota`（其实不要求），`gifts` 要求 `affinity`（送礼要加分）。**成环直接抛异常**——
启动时报错，而不是第 40 轮对话时才发现。

**② 依赖传递性下线**
甲的依赖不在或已下线 → 甲也下线，理由写进 `_off`。
所以拔掉 `affinity` 会自动带走 `gifts`，而不是让礼物页 500。

**③ 钩子隔离**
每个钩子外面一个 `try/except`，异常变成一条 `PluginEvent(name, "failed", {"hook": …})`，
回合继续。`start()` 抛异常则整个插件下线，**并且只影响它的依赖者**。

**④ 路由合并**
所有插件的 `routes()` 合成一张表；**同一个 path 被两个插件声明 = 启动报错**。
静默让后一个覆盖前一个，是两个功能抢一个 URL 的开始。

`capabilities()` 会把下线的插件也报上去（`{"enabled": false, "why": "start failed: …"}`），
所以设置页能解释「为什么这个功能是灰的」。

### 2.4 提示词：四槽 + 缓存

`core/prompt.py` 的注释写得很直接：**槽位就是缓存决策**。

```
┌─ system  persona ──────────────────────── cache_breakpoint ✅
│  角色 sheet + 人设资料（whole 模式）+ 回复格式指令
├─ system  profile ──────────────────────── 跟着 persona 一起被缓存
│  「制作人是谁」：称呼、性别、生日、时区
├─ user / assistant × N  history ────────── 最后一个消息上打 cache_breakpoint ✅
│  裁剪过的历史（§下面的 1.5 倍规则）
├─ system  turn ─────────────────────────── **不缓存**
│  这一轮才成立的东西：时段、距离、好感档、判过的情绪、记忆、剧情
└─ user    本轮输入 ─────────────────────── 永远最后
```

- `persona` 只允许放「对所有人和所有轮都不变」的东西；
- `profile` 放「对这个人不变」；
- `turn` 放「这一轮才成立」；
- 插件只能往 `TURN` / `PROFILE` 里写（`_only()` 强制），所以不可能悄悄打碎前缀缓存。

**历史裁剪（`trim_history`）**：默认保留 20 轮，但**不是每轮丢一条**，而是涨到 30 轮才一次性
砍回 20 轮。原因是缓存：每轮都变的前缀等于没有缓存。这条是照搬上游 `engine.chat_history`。

**顺序由 `order` 决定，相同 order 保持添加顺序。** 实测的 turn 槽顺序：

| order | 谁写的 | 内容 |
|---|---|---|
| 10 | `affinity` | 你现在对这个人的态度（含生闷气） |
| 15 | `recall` | 向量检索到的资料段落 |
| 20 | `qq` 通道 | 「你有收藏表情」的说明 |
| 25 | `stories` | 命中的剧情原文 |
| 30 | `qq` 通道 | 记忆文件（背景信息，所以放最后） |

一条设计经验，写在 `presence.py` 的注释里，值得单独记：**人设会赢。**
把「对陌生人的语气」写在 turn 槽，人设里 700 行「ふゆ」的营业声线会把它压过去；
同样的句子放在 persona 槽**最前面**，才真的改变输出。这是量过四档、四档输出几乎一样之后才改的。

### 2.5 判分总线

判分模型（judge）读「制作人刚说了什么」，回答几个小问题：这句话伤到她了吗？
她要不要发一张照片？这句在问她自己的设定吗？

**关键设计：内核拥有这次调用，插件只写问题。**

```python
Question("affinity.level", "这句话让她多高兴（0 伤到她 … 4 非常开心）", "int", least=0, most=4)
Question("photos.wants", "他想看的是一张照片吗？", "bool")
Question("recall.needed", "这句需要她的设定资料才答得上吗？", "bool")
```

三个插件问三个问题 → **一次请求，一个 JSON 对象**。理由两条，都很硬：
延迟（串行三次就是三倍等待）和成本（插件各自持有一个 LLM 时，账单按插件数增长）。

读取规则（`judge.py::_read`）**非常保守**：

- 模型返回散文？正则只抓第一个 `{...}`，抓不到就当没答；
- 某个键缺失 → 那个键干脆不出现，插件读到 `None` 必须自己决定怎么办；
- 类型不对 → 丢弃，不做「聪明」的猜测；
- 越界 → 夹到 `[least, most]`。

系统提示里还写死一句：**「拿不准时按最保守的值回答：bool 给 false，int 给 0。」**

判分失败（网络、超时、格式全错）时 `facts` 为空。每个插件的写法都不一样、但都是安全的：

| 插件 | 判分缺失时 |
|---|---|
| `affinity` | 不动数字（不变更比误判便宜） |
| `photos` | 不发照片 |
| `recall` | **照常检索**（fail-open：多说一点比少说好） |
| `stories` | 照常注入 |
| `qq` 的「无语」表情 | **不发**（fail-closed：发出去收不回来） |

注意 `recall` 和 `stories` 是 fail-open，`affinity` 和表情是 fail-closed——这不是随意的，
判据是「错了的代价在哪边」。

### 2.6 存储与数据表

`core/store.py`：**一个连接、一把全局锁、WAL 模式**。

```python
self._db = sqlite3.connect(str(path), check_same_thread=False)
PRAGMA journal_mode=WAL
```

内核只管三张表：`users`、`conversations`、`messages`。
其余所有表都是插件自己的，通过 `migrations()` 交上来，`Store.migrate()` 在一个事务里执行。

`migrate()` 有一个必须知道的容错：**`duplicate column name` 被吞掉**。
因为加列的标准写法是「表定义里带上新列 + 一条 `ALTER TABLE ADD COLUMN`」，
后者在新建的库上必然失败，那是正常的。

**21 张表全表**（内核 3 + 插件 11 + 通道 7）：

| 层 | 表 | 谁写 | 存什么 |
|---|---|---|---|
| 内核 | `users` | Store | 一个人（网页的制作人 / 群里的某位群友） |
| 内核 | `conversations` | Store | 一对 user×character |
| 内核 | `messages` | Store | 每轮两句；助手那句存 `segments` JSON |
| 插件 | `memories` | memory | 关于制作人的事实（谁告诉她的） |
| 插件 | `producer_profile` | profile | 称呼 / 性别 / 生日 / 时区 |
| 插件 | `photo_uses` | photos | 每小时图片额度的流水 |
| 插件 | `quota_uses` | quota | 每小时回合额度的流水 |
| 插件 | `affinity` | affinity | 网页表面的好感度 |
| 插件 | `affinity_scenes` | affinity | 分表面好感度（dm / group），含 sulk 标记 |
| 插件 | `recall_vectors` | recall | 向量检索缓存 |
| 插件 | `gift_balances` / `gift_backpacks` / `gift_records` | gifts | 抽签次数 / 背包 / 送礼记录 |
| 插件 | `service_calls` | status | 每次模型调用的成败（窗口统计） |
| 通道 | `qq_identities` | identities | QQ 号 ↔ 用户 id、次数、静音、恋爱标记 |
| 通道 | `qq_seen` | identities | 处理过的 message_id（防重放） |
| 通道 | `qq_outbox` | outbox | 未送出的消息（重连补发） |
| 通道 | `qq_stickers` | stickers | 表情包（sha1、来源房间、标签） |
| 通道 | `qq_flags` | stickers | 键值：定时任务的「下一次」「今天发过」 |
| 通道 | `qq_digest` | digest | 记忆文件的水位（写到第几条消息） |
| 通道 | `qzone_posts` | qzone | 说说记录（tid、正文、是否成功） |
| 通道 | `qzone_feeds` | qzone_feed | 别人的动态与评论（tid、作者、正文、回没回） |

一条实用提醒：**`messages.text` 对助手那句话是空的**，内容在 `segments` JSON 里。
查历史别查错列：

```sql
SELECT role, substr(segments,1,60) FROM messages ORDER BY seq DESC LIMIT 5;
```

### 2.7 热加载：改文件，下一轮生效

`core/live.py` 做一件小而关键的事：文件变了就重读，但带三条保护。

| 保护 | 为什么 |
|---|---|
| **settle 0.35s** | 编辑器一边打字一边写盘，读到的是半个文件 |
| **保留上一版** | 读到空内容不采用（`>file` 重定向正好落在两次读之间） |
| **stat 便宜** | 每轮每文件一次 `stat`，相对一次模型调用（秒级）可以忽略 |

所有角色文件（`world/*.md`、`stage.toml`、`interests.md`、`qzone.md`）都走这条路。
但**有两处例外必须重启**：

- `characters/characters.toml` 的增删角色 → 其实也热加载（`App.refresh()`），只有新增图片文件不行；
- **`facts/<id>.md` 在 persona 槽里，被 Engine 缓存**——改完要 `/重载` 或重启，
  因为 `Engine` 是按角色缓存的，不是每轮重读（`app.generation` 会随 `refresh()` 递增，
  通道的 `_engine()` 用它做 key，所以 `/重载` 之后立刻生效）。

> 一个具体的坑：`Characters.stale()` 的签名只看 `characters.toml` 和 `sheets/*.md`，
> **不含 `facts/`**。所以改 facts 时主循环的自动刷新不会触发，必须 `/重载`。
> 这条在 `SCCORE_RECALL=whole` 时最容易被撞上（facts 整天在 persona 槽里）。

### 2.8 事件词汇表

`core/events.py` 一共 76 行，是通道和前端渲染的**唯一契约**。

| 事件 | 何时 | 谁关心 |
|---|---|---|
| `UserSaid(text)` | 回合开始 | 前端（回显） |
| `SegmentDelta(segment, lang, text)` | 流式增量 | 前端（打字机） |
| `SegmentComplete(segment)` | 一个段落写完 | 通道（一段话发一条 QQ 消息） |
| `SpeechDelta(…)` | 语音增量 | 电话（本版未接） |
| `PhotoTagged(tag)` | 回复里带照片标记 | 前端 |
| `TurnDone(message_id)` | 落库成功 | 前端、通道（取回复 id） |
| `TurnRejected(reason, retry_after)` | 闸门拒绝，没花钱 | 通道（翻成人话） |
| `TurnFailed(reason)` | 开始了但没成功 | 通道（道歉一句） |
| `PluginEvent(ns, name, payload)` | 插件要说的话 | 知道自己的前端；**不认识的必须忽略** |

最后一条是兼容性纪律：**前端遇到不认识的 namespace 要忽略，不能报错。**
这样一个插件上线不会让旧前端白屏。

---

## 3 插件 plugins/

读法：每个插件给出「它是什么 / 用什么钩子 / 自己有什么表 / 关键逻辑 / 怎么关」。
`app.py` 里的注册顺序（也是钩子调用顺序，除非 `requires` 另有规定）：

```python
plugins = [
    self.characters, self.recall, self.stories, self.quota, self.photos,
    self.affinity, self.memory, self.profile, self.voice, self.status,
]
if self.gifts is not None:
    plugins.append(self.gifts)
if self.qq is not None:          # 通道最后（它 requires characters, affinity）
    plugins.append(self.qq)
```

### 3.1 characters — 角色名册

- **钩子**：无（纯 surface）。`capabilities()` 报角色数与默认角色。
- **接口**：`GET /api/characters`、头像 `GET /media/avatar/<id>`。
- **数据**：`characters/characters.toml`（28 位）+ `sheets/<id>.md`。
- **关键点**：`sheet` 的**全文进 persona 槽**，所以人设长度直接等于每轮固定成本。
  现网 `fuyuko.md` 是 24KB，属于偏大的（一般角色 8~15KB 合适）。
- **关掉会怎样**：内核仍能服务「一位角色」，从 `SCCORE_CHARACTERS` 下第一个 `*.md` 读。

### 3.2 recall — 她的资料，三种形态

`SCCORE_RECALL` 三选一，这是**部署时的成本/质量取舍**，不是功能开关：

| 模式 | 做法 | 成本 | 适用 |
|---|---|---|---|
| `off` | 只有 sheet | 最低 | 没有 `facts/<id>.md` |
| `whole` | 整份 facts 进 persona 槽 | 一次提示词长度，之后被缓存 | **现网用这个**（facts 24KB） |
| `vector` | 切段 + 嵌入 + 按句检索 top-k | 每轮一次 embedding 调用 | facts 很大（>50KB）时才有意义 |

- **钩子**：`prompt_sections()`（vector 模式）、`judge_questions()`（`recall.needed`，只 vector 开）。
- **降级**：`vector` 但没配 `SCCORE_EMBEDDING_BASE_URL` → 启动时打印一行，自动退回 `whole`。
  理由写在 `app.py`：「话照说」比「说了但少了资料」重要。
- **关掉会怎样**：`mode=off`，她只知道自己的人设，不知道设定资料。

### 3.3 stories — 剧情原文按话题检索

语料 47 个文件、约 1.1MB，**不进提示词**。命中话题才取。

- **三个关键词来源**：手写话题表（`world/<id>-stories.toml`，53 个话题）→ 卡名 → 话名。
- **钩子**：`prompt_sections()`（order=25）、`judge_questions()`（`stories.needed`）。
- **为什么用确定性匹配而不是向量**：误命中的代价不对称。漏了 = 她少说一句细节；
  错命中 = 她讲一段谁也没提的剧情，那在群里立刻穿帮。
- **注入上限**：≤3 段、≤1500 字。
- **接口**：`GET /api/stories?c=<角色>&q=<话>`，用来验证一个话题能不能命中。
- **关掉**：`SCCORE_STORIES=off`。

### 3.4 quota — 每小时回合数

刻意做成最小的插件（115 行），它是「插件协议是否够用」的检验样本。

- **钩子**：`before_turn`（只读，超了返回 `Veto("quota.turns", retry_after)`）、
  `after_turn`（落一条流水）。
- **表**：`quota_uses(user_id, at)`。
- **为什么滑动小时**：固定的小时桶会让用户在一小时的最后一分钟用光，下一分钟再用光一整小时。
- **为什么 on the way out**：回复没存下来就不算用过（失败的回合不扣额度）。
- **免计**：`turn.facts["quota.exempt"]` 为真时不计数——带自己 key 的那一轮（`own_keys`）。
- **默认**：`SCCORE_TURNS_PER_HOUR=60`（网页端一个人用，基本碰不到）。

### 3.5 photos — 图片与额度

只做「制作人 → 她」这个方向（她能发图靠另一个机制，见 §4.10）。

- **钩子**：`before_turn`（`Veto("photos.hourly")`）、`prompt_sections`（无）、`after_turn`（计费）。
- **表**：`photo_uses`。
- **流程**：data URL → 解码 → 校验（真图片类型、≤6MB）→ 写 `SCCORE_MEDIA/<id>.<ext>` →
  本轮以 `parts` 形式进 user 消息（模型真的看图）。
- **失败提示**：有图片但解码失败时，通道会插一句「对方发了图但你没看清，不要猜内容」。
- **默认**：`SCCORE_PHOTOS_PER_HOUR=8`。

### 3.6 affinity — 好感度与生闷气 ★

这是最复杂的一个插件（801 行），也是「三种钩子同在一个类里」的样板：
判分问题、闸门、提示词、观察者全有。

**数值规则**

```
0 ──── 冷淡 ──20── 疏远 ──40── 信任 ──65── 亲近 ──85── 亲密 ── 100
         ↑ 冷却从 0 开始 ↑ 冷却在 10 结束
```

| 常量 | 值 | 含义 |
|---|---|---|
| `NORMAL` | 50 | 网页/私聊的起点，也是静置回升的上限 |
| `SCCORE_AFFINITY_START` | 20 | **群聊**起点（落在「疏远」，先听到营业声线） |
| `LEVEL_STEPS` | `-5, -2, 0, +0.5, +1` | 判分 0~4 分别怎么动数字 |
| `GAINS_PER_DAY` | 6 | 一天最多涨多少（**群聊除外**） |
| `RECOVERY_PER_DAY` | 10 | 没人说话时每天回到起点值 |
| `THAW` | 10 | 冷却结束的位置 |

**分表面（这是关键）**

好感度按 `surface` 分三份：`page`（网页）、`dm`（私聊）、`group`（群聊）。

- 结构上：`page` 用 `affinity` 表，`dm`/`group` 用 `affinity_scenes` 表；
  第一次写 `scenes` 时会从旧表搬一份（`INSERT ... SELECT`）。
- 语义上：**五十个人对她的影响和一个人对她的影响是两段关系**，一个数字表达不了。
- 后果要记住：`GROUP_SURFACE` 是一个桶，**两个群共用一份群聊好感度**。
  想按群分开，得把 key 改成 `group:<room>`（没做）。

**判分 → 数字**

```python
# plugins/affinity.py::after_turn（简化）
judged = self.judged(turn)          # 读 facts["affinity.level"] / ["affinity.out_of_line"]
if judged.step == 0: return
before, change, after = self.change(user, character, judged.step, surface=turn.surface)
```

两条不可越过的边界：

1. **回复的文本永远不改数字。** 只有判分能改。
   模型在角色里说「你伤到我了」不构成证据；一个喜欢被夸的模型会自己往上飘。
2. **失败的回合不动数字。** `after_turn` 只在存库之后跑。

**生闷气（sulk）**

只在**同时满足三件事**时可能发生：

```
私聊（surface == "dm"）  且  恋爱中（facts["affinity.loving"]）  且  数字 < SULK_LINE(70)
                        且  这一句被判定为「惹到她」（level 0/1 或越界）
```

- 一旦发生，`sulk` 标记落库（`affinity_scenes.sulk`），并在提示词里插入 `SULK_HEAD`；
  语气**按数字分三档**（≥65 / ≥50 / <50），越低越冷。
- 哄她：正面判分额外 **+0.5**（`SULK_SOOTHE`）。
- **回到 70 自动解除**，不用手动清。
- 为什么门槛是 70 而不是「亲近」的 65：见 `SULK_LINE` 的注释——
  「我还在生气」和「我们没那么熟」是两个问题，不能被同一个数字回答。

**「冬優 / 冬優子」这类人格层的东西不在这里。** 见 §4.6 的三套尺子。

**接口**：`GET /api/affinity?c=&u=&s=dm|group`。返回 `tier` / `tier_label` / `sulk` / `value`。

**关掉会怎样**：`gifts` 跟着下线（`requires`），QQ 通道跟着下线（也 requires affinity）。
所有读它的地方都有 `try/except`，不会抛。

### 3.7 memory — 关于制作人的长期记忆

两条完全不同的记忆路径，别混：

| | `memory` 插件 | `channels/digest.py` |
|---|---|---|
| 单位 | **一条事实**（一句话） | **整份文件**（一段关系的形状） |
| 触发 | 每回合（后台抽取） | 私聊每 3 天 / 群聊每 7 天 |
| 存哪 | `memories` 表 | `characters/memory/*.md` |
| 进提示词 | TURN 槽（只带相关的几条） | TURN 槽（整份，order=30） |
| 成本 | 每回合一次小调用 | 每几天一次大调用 |

`memory` 插件的四条纪律（都来自上游，改动会立刻被用户察觉）：

1. **是一句事实，不是摘要。** 摘要等于第二份历史，而提示词里已经有一份。
2. **所有角色的记忆共享**（同一个制作人）。一条记忆记着「谁告诉她的」，
   别的角色看到要转述成「听 X 说……」，不能当成自己经历的。
3. **每条都带来源角色。**
4. **抽取是后台的**，失败就算了。

### 3.8 gifts — 抽签、背包、重置卡

数字全是上游的，且互相制衡：

| 项 | 值 |
|---|---|
| 抽签次数 | 一天 1 次（北京时间零点算新的一天），最多存 7 次 |
| 抽到什么 | 重置卡 1% / 普通 69% / 稀有度较高 25% / 稀有 5% |
| 送礼加成 | 按稀有度 +3 / +5 / +7；她喜欢再加 3 |
| 重置卡 | 所有「信任」以下（含冷却中）回到 50 |

- **钩子**：纯 surface（`routes()`、`capabilities()`）。不参与回合。
- **表**：`gift_balances` / `gift_backpacks` / `gift_records`。
- **数据**：`characters/gifts.toml`（礼物目录）；角色喜欢什么写在 `characters.toml` 的 `liked_gifts`。
- **目录坏了不致命**：`GiftCatalogError` 被 `app.py` 抓住，`gifts = None`，页面显示「礼物暂时没有开放」。
- **关掉**：`gifts.toml` 缺失或解析失败。群聊里没有礼物——通道没接这个插件。

### 3.9 profile — 制作人档案

- **钩子**：`prompt_sections()` → `Slot.PROFILE`，order=10。
- **内容**：称呼、性别、生日、时区。**空字段什么都不写**，不是写默认值——
  「每个角色按自己的习惯称呼」是合法的空，不是缺失。
- **表**：`producer_profile`。

### 3.10 voice — 语音

- **钩子**：纯 surface（`POST /api/voice` → 一段 mp3 的 base64；页面上按播放键才调）。
- **开关**：`FISH_AUDIO_API_KEY` 存在才 `enabled`，否则报 `{"enabled": false, "why": …}`。
- **影响面**：不只一个按钮。`SCCORE_CALL=auto` 时，**电话键画不画、她提不提打电话，
  都读 `App.call_ready()`**，而这个方法读 `voice.ready`。
  所以配了语音会同时改变三处行为（页面、提示词、通话能力上报）。

### 3.11 status — 服务健康

- **钩子**：`after_turn` 之外，主要被 `serve.py` 在每轮结束时直接调用 `record("chat_llm", ok=…)`。
- **表**：`service_calls(kind, at, ok)`。
- **判定**：窗口内失败占比 ≥5% → `unsteady`；≥50% → `down`；窗口内没调用 → `idle`（不是故障）。
- **接口**：`GET /api/service-status`（对话列表页那个铃铛）。

---

## 4 QQ 通道 channels/

通道是「可以整个拿走的一层」：删掉 `channels/` 目录，`app.py::_qq_plugin()` 的 import 失败、
返回 `None`，网页版照常运行，`serve.py` 一行都不用改。

### 4.1 组成与生命周期

```
channels/plugin_qq.py::QQService   ← 一个 Plugin，也是唯一有生命周期的那个
  ├ start()：new_event_loop + Thread(name="qq") + 启动定时循环 + client.run()
  ├ stop() ：取消所有 task → 关 socket → join(5s)
  ├ routes()：/api/qq/*、/api/qzone*
  └ migrations()：5 个模块的表一次交上来
        │
        ├── QQChannel（channels/qq.py，1782 行）  消息判定与回合
        │     ├ Presence     语域（三套尺子）
        │     ├ World        三层知识
        │     ├ Interest     插话判断
        │     ├ Digest       记忆文件
        │     ├ Identities   QQ 号 ↔ 用户
        │     └ Outbox       发送队列
        ├── OneBot + ws      OneBot 11 客户端（零依赖 WebSocket）
        ├── Pack             表情包
        └── Qzone            说说
```

**为什么它不是普通插件？** 因为普通插件的钩子只在回合里被调用，而通道要开线程、连 socket、
跑定时器——它有自己的时间。所以 `serve.py` 直接 `app.host.start()` 把它拉起来，
`Host.start()` 是唯一知道「谁有生命周期」的地方。

### 4.2 一条群消息的完整旅程

以「群里有人发了一句普通的话」为例，途经的每一道闸都在这里：

```
NapCat ── WS ──► OneBot.run 收帧（echo 的是回包，post_type 的是事件）
                    │ asyncio.create_task(_deliver)
                    ▼
            QQChannel.on_event
                    │ message_type == "group" ?
                    ▼
   ┌── _on_group ─────────────────────────────────────────────────┐
   │ ① 群在 QQ_GROUP_IDS 里？           否 → 丢                    │
   │ ② 不是自己发的、不在忽略名单？      否 → 丢                    │
   │ ③ 记「最后谁说话 / 房间最后活动时间」（给安静自说自话用）        │
   │ ④ 收表情（群友发的图 → qq_stickers，sha1 去重）                │
   │ ⑤ 算 called：@ 了？或叫了她的名字？                            │
   │      都没叫 → _remember（进环形缓冲）+ _interject → 结束       │
   └───────────────────────────────────────────────────────────────┘
                    │ 被叫到了
                    ▼
   ┌── _accept：花钱前的总闸 ──────────────────────────────────────┐
   │ ① 空消息（无文字无图）→ 丢                                     │
   │ ② message_id 见过？（qq_seen，防重放）                          │
   │ ③ 以 "/" 开头 → 走指令分支（在静音闸之前，否则静音后无法解除）    │
   │ ④ 这个人被静音了？→ 丢                                         │
   │ ⑤ 进缓冲，重置 1.2s 合并定时器                                 │
   └───────────────────────────────────────────────────────────────┘
                    │ 1.2 秒后（这 1.2 秒内同一人的后续消息会并进来）
                    ▼
   ┌── _after_window ──────────────────────────────────────────────┐
   │ ① 群友且距上次回复 < 20s → 跳过（主人不受这条）                 │
   │ ② called 有值 → _called；否则 → _turn                          │
   └───────────────────────────────────────────────────────────────┘
                    ▼
   ┌── _turn：组装这一轮的上下文 ──────────────────────────────────┐
   │ role（owner/stranger）→ user_id（identities.resolve）          │
   │ turns（说过几次）→ stage（熟悉度档位）                          │
   │ love（仅私聊，且两道门都过）                                    │
   │ tier（仅群聊，读 group 表面的好感度）                           │
   │ 会话（按 scene 分：qq:dm / qq:group:<room>）                   │
   │ 图片 → ids                                                     │
   │ sections：presence 语域 + world 知识 + digest 记忆 +            │
   │           最近几行转写 + 表情说明 + 收藏表情说明                 │
   │ async with _gate(信号量 2) → async with _lock(会话) → _stream   │
   └───────────────────────────────────────────────────────────────┘
                    ▼
   ┌── _stream：跑一轮内核 ────────────────────────────────────────┐
   │ app.turn(..., surface="group", extra_facts={"affinity.loving"})│
   │ asyncio.timeout(180s) 包住整轮                                 │
   │ SegmentComplete → _emit（一段一条消息）                        │
   │ TurnRejected/TurnFailed → 用她的口吻说一句（texts.py）          │
   │ finally：记「刚回过这个人」（冷却起点）                        │
   │ 对方要表情 → 整轮结束后附一张图                                 │
   └───────────────────────────────────────────────────────────────┘
                    ▼
   ┌── _emit → Outbox.send ────────────────────────────────────────┐
   │ 剥掉 [贴纸] 记号 → 超过 900 字按句号切 → 0.8~1.5s 间隔发        │
   │ 第一条带 @ 和引用；插话不带（没人问她）                         │
   │ 失败 → 留 pending 行，重连后补发                                │
   └───────────────────────────────────────────────────────────────┘
```

### 4.3 三道「不要重复劳动」的闸

| 闸 | 在哪 | 防的是什么 |
|---|---|---|
| `qq_seen`（message_id） | `_accept` | 重连时 NapCat 会重放事件，回两次是最显眼的 bug |
| 合并窗口（1.2s） | `_accept` → `_after_window` | 「ふゆ在吗」+「帮我看看这个」是两帧，应该问一次「要不要回」 |
| 成员冷却（20s） | `_after_window` | 五十人的群里，一个人不能占住场子 |

**冷却不作用于主人**，这是修过的 bug：「@ 了两次，第二次没回」看起来像坏了，而第二句其实是个正经问题。

### 4.4 被叫到：@ 与叫名字

两种「叫」走同一条路，但判断强度不同：

```python
if self.config.called and (mentioned or self._named(text)):
    called = "at" if mentioned else "name"
```

| 方式 | 判不判断 | 理由 |
|---|---|---|
| **@ 她** | **不判断，直接回** | @ 是点名的地址，漏掉不可能；群有权期待被 @ 的机器人回答。顺带省一次模型请求 |
| **说了她的名字** | 判断（`interest.called()`），可以「不回」 | 「在说她」不等于「在对她说」。「不」是合法答案，理由写进日志 |
| 都没叫 | 走插话（§4.5） | — |

`QQ_CALLED=0` 回到旧行为：@ 直接回（只看见那一句），叫名字不算叫。

「叫了她的名字」由 `world/<id>-stage.toml` 的 `names.mentions` 决定，
**只收那些不会被日常对话带出来的形式**（写「小糸」而不是「糸」）。

> 踩过的坑：`mentions` 按 `[、,，/\s]+` 切分，所以写「福丸 小糸」会被拆成两个词
> （等于多认了一个姓）。姓名之间要写全角空格以外的形式，或者只写不带空格的全名。

### 4.5 插话：不掷骰子决定「说什么」

`channels/interests.py` 的注释把设计动机写清楚了：
**硬币能决定「说不说」，但决定不了「说什么」**——以前输入永远是最新那条消息，
于是插话永远是「对最后一句的反应」，看起来像个解析器，不像一个人。

现在的闸门顺序（**便宜的先过**，这一步顺序就是成本控制）：

```
1. QQ_AMBIENT=1 ？（总开关）
2. 这个人被静音了？
3. 距上次插话 ≥ QQ_AMBIENT_GAP（默认 240s）
4. 今天的次数还有？（QQ_AMBIENT_DAILY，所有群合计）
5. 距上次「看这个群」≥ QQ_AMBIENT_CHECK（默认 300s）
6. 这个群已有一个判断在飞？→ 跳过（五个人同时说话不等于五次请求）
7. 抛「自言自语」的硬币（QQ_AMBIENT_OWN_P，默认 0.35）
8. ── 到这里才可能花钱 ──
   a. 「无语」表情判断（§4.10.3）：先掷 QQ_STICKER_FACE_P，中了再问模型
   b. 提到她认识的同行？→ 抬高「看一眼」的概率（见下）
   c. 掷 QQ_AMBIENT_P
   d. 问模型一次：「有没有她想接的？接第几行？」→ (speak, which, why)
9. 说了 → 扣一次额度，记日志（第几次、接谁、为什么）
```

**同行加权（偶像加成）**：`world/<id>-idols.toml` 里 88 个别名、三档亲疏
（3 = 自己人，2 = 同事务所，1 = 事务所的人），最长匹配优先
（「芹沢あさひ」不会被「あさひ」抢掉）。

```python
chance = QQ_AMBIENT_P
if mention:                                  # 命中了某个名字
    chance += (1 - chance) * (closeness / 3) * IDOL_LEAN   # IDOL_LEAN = 0.75
```

所以当 `QQ_AMBIENT_P=0.55` 时：平时 55%，同行 78%，自己人 89%。
**是概率不是开关**，`ambient_gap` 和每日次数照旧管着，整体仍然低频。
判断提示里还写死一句「不想说话就不接，别变成听见关键词就开口的人」。

**「不插话」也要写日志**：

```
qq: 看了一眼群里，没接（没什么可接的）
```

这是被问得最多的问题（「她怎么不理我们」），所以理由必须能从日志里读出来。

### 4.6 语气：三套尺子并存

这是最容易搞混的地方。三套机制叠在一起，各管一件事：

| 尺子 | 依据 | 取值 | 作用位置 | 管什么 |
|---|---|---|---|---|
| **scene** | 有外人在吗 | `group` / `private` | persona 最前 | 营业声线 vs 本性 |
| **stage** | 和她说过几次话 | `new/familiar/close/inner` | persona 最前（`【对面】…`） | 熟悉度（紧张/拘谨/放松） |
| **tier** | 群聊好感度数值 | `cold…intimate` | turn 槽（`affinity` 插件） | 距离感、甜话、带刺 |

细节：

- `stage` 由 `QQ_IDENTITIES.turns` 的正整数决定，档位和门槛写在
  `world/<id>-stage.toml` 的 `【阶段 name｜第 N 次开始】` 标题里；主人的下限是 `QQ_OWNER_STAGE`。
- `tier` **只在群聊算**（`if not private: tier = self._tier(...)`）。
  私聊完全不读这个值——私聊有自己的语域文件，那里是本性。
- **主人也不例外**：群聊里他就是群里的一员，数值怎么走就怎么说。
  以前写死 `owner ⇒ intimate`，让他那个数字等于摆设（已改）。
- 三套尺子会互相拉扯：熟客但好感低时，`stage` 说「放松」，`tier` 说「有距离」。
  实测提示词尾部那条（turn 槽的 affinity）赢，因为它离回复最近。
  **要彻底统一，得把 `stage_for` 改成按 tier 选**——这一步没做，属于已知的接缝。

> 一个不对称，设计时要知道：**会话是按群分的，好感度不是。**
> 会话的键是 `conversations.title = "qq:group:<群号>"`，所以同一个人在 A 群和 B 群
> 各有各的历史；但群聊好感度只按 `surface="group"` 存一份，两个群共用。
> 结果是：同一个人换群说话，历史接不上，距离感却是同一份。

**关于「冬優 / 冬優子」**（写在这里是因为反复被搞反）：

- **「冬優（ふゆ）」= 面具/营业那一面**：笑眯眯、句尾软、挂♡、有距离。
- **「冬優子」= 本体**：短句、带刺、「ハ？」「バーカ」。
- 群聊语域是一条斜线：**越陌生越往「冬優」去**，越熟越能出本体的短句。
- 这条规则写在 `world/<id>-qq.md`，**不要写进 `affinity.py` 的常量和提示词文本里**——
  换一位角色，那些名字全是错的。

### 4.7 发送：分段、节流、落队列

`channels/outbox.py` 解决了三件事：

| 问题 | 做法 |
|---|---|
| 一段回复太长 | 单条 ≤900 字，**只断在句末符号**（`。！？…\n`），从不切在句子中间 |
| 像机器人刷屏 | 每条之间 0.8~1.5s 随机间隔（`_pace`） |
| 发失败丢内容 | 先写 `qq_outbox`（pending），发成功才删；重连后 `resend_pending()` 补发 |

几个具体数字和设计：

- `_push` 内部重试 2 次（间隔 0.4s、0.8s），之后才抛出；
- 补发的行要求 `attempts < 5`，每条之间等 3 秒——一口气补二十条会被风控；
- **补发时机在「连上」那一刻**（`on_connect`），不是启动时。
  踩过这个坑：启动时 socket 还没连上，每一行都失败还各花掉一次重试次数，
  于是在重启循环的机器上，队列被悄悄吃光而一条都没发出去。
- 私聊目标在 `group_id` 列里用 `dm:` 前缀区分——加一列意味着对线上库做迁移，
  加一个前缀只需要一次 `startswith`。

### 4.8 定时任务族

全部在 QQ 线程的事件循环里，`stop()` 时统一取消。**每个任务的时间点都落库**，
这是第一纪律：这台宿主一夜能重启三次，内存里的定时器每次从头摇。

| 任务 | 周期 | 状态存哪 | 干什么 |
|---|---|---|---|
| `_alive` | 300s | 无 | `get_login_info` 比对 `QQ_SELF_ID`，掉登录时打日志 |
| `_faces` | 20s 首次 → 24h（失败 300s 后重试） | 无 | 拉这个号收藏的表情进表情包 |
| `_floor` | 300s | `affinity` 表 | 把主人的好感度抬回下限（§下面） |
| `_love` | 300s 看一眼 | `qq_flags: love:next` / `love:last` | 恋爱中主动私聊（随机间隔 60~150 分钟） |
| `_idle` | 60s | 无 | 群里安静够久时她自己开口（默认关） |
| `_digest` | 90s 首次 → 3600s | `qq_digest` 水位 | 到期就重写记忆文件 |
| `_qzone` | 60s | `qq_flags: qzone:*` | 到点且掷中才发说说（没连上不算一次发布） |
| `_qzone_feed` | 300s | `qzone_feeds` 表 + `qq_flags: qzone:feed:*` | 看好友动态，回一句（有额度、间隔、时效） |
| `_monday` | 60s | `qq_flags: monday:<ISO 周>` | 周一发「上班」表情 |

**为什么 `_love` 的间隔要存库**：随机间隔放在内存里，重启一次就重画一次。
在一台半小时重启一次的机器上，60~150 分钟的间隔永远走不到尽头——
她一次都不会主动发。存了之后，重启只是「等同一个时刻」，
而掉线期间到点的那些会在回来之后不久发出，这恰好就是「过一会儿她想起来了」。

**为什么 `_monday` 记 ISO 周而不是「今天发过」**：一周一张，得防住一周内重启多次。

**为什么额度用日期做 key**：`ambient:2026-10-27` 这种形式，
过了本地零点自然换一个键，不需要清理任务，也不会提前重置。

### 4.9 指令系统

```
/帮助 /help /?        菜单（末行说明谁能用哪些）
/重开 /reset          换一个新话题（新会话）
/好感 /affinity       她现在的心情（档位，不给数字）
/阶段 /stage          说过几次、什么档位；/阶段 重置 归零；/阶段 全员重置 except 你
/重载 /reload         改完人设/规则文件后立刻生效（丢缓存 + 重读）
/表情 [标签]          发一张（不带标签 = 自己收藏的）
/表情 同步            立刻拉一次收藏表情
/表情标 标签          把最新收进包里的那张命名（标签空 = 取消）
/静音 /mute           让她先别说话
/解除 /unmute         恢复
```

三道权限门，缺一不可：

1. **谁能发**：`QQ_COMMANDS=owner` 时只有 `QQ_OWNER_IDS` 里的人有效果；
2. **群里谁都能用的白名单**：`QQ_SHARED_COMMANDS`（默认 `表情,好感`）——
   这两个都是「只读、只影响自己」的指令，所以可以放开；
3. **节流**：`_quiet_repeat` 同一个指令 15 秒内同一个人只回一次（免得有人连发刷屏）。

指令检查**在静音闸之前**，否则一个人静音自己之后就再也解不开了。

### 4.10 表情包

#### 4.10.1 两个来源

| 来源 | 怎么进来 | 特征 |
|---|---|---|
| 群友发的 | 每条群消息里的图片 URL，`_harvest` 抓下来 | `group_id = <房间>` |
| 这个号收藏的 | `fetch_custom_face` 拉一次（启动 20s 后 + 每天） | `group_id = ''` ← **就是「收藏」** |

都按 **sha1 内容哈希** 去重，文件落在 `SCCORE_MEDIA/stickers/`。
上限：群友的每次最多 2 张；收藏最多 200 张。

#### 4.10.2 「收藏的表情叫不出来」是怎么修的

真实原因不是同步（14 张都在库里、文件完好），而是**挑的时候从全包随机**：
包里 186 张，收藏占 7.5%，看起来就是「从来不用」。

修法分两层：

```python
def _sticker_row(self):
    return self.pack.pick(source="fav") or self.pack.pick()   # 先收藏，空了才退回全包
```

- `Pack.pick(tag, source="fav"|"group")` 增加了来源维度；`Pack.favourites()` 单独计数；
- `/表情 收藏`（别名 我的 / 我的收藏 / fav）可以显式指定；
- 主动发一张（`/api/qq/sticker`）也走 `_sticker_row()`。

#### 4.10.3 什么时候主动发

| 触发 | 判定 | 节流 |
|---|---|---|
| **对方要** | `wants_sticker(text)`：`来/发/给/整/弄/丢/甩` + `表情/贴纸/表情包`，或 `表情` + `呢/吗/看看/来一张`；`别发/不用了/算了` 一票否决 | 每房间 600s |
| **一句让人无语的话** | `_maybe_face()`：标签有货 → 硬币 `QQ_STICKER_FACE_P` → 10 分钟节流 → 问模型「有没有难绷的」 | 每房间 600s（与上共用 `_marked`） |
| **插话时** | `QQ_AMBIENT_STICKER_P`（默认 0.3） | 同上 |
| **周一** | `_monday()`，标签 `上班` | 每周一次 |

**「要表情」为什么是通道判、不是模型判**——这是一条重要教训：

最初做的是让模型在话尾写 `[贴纸]` 标记，通道换成图。**模型从来不写。**
查了 `core/protocol/reply.py` 的回复格式：「除了这些标签和标签里的内容，不要输出任何其他文字」
「只用方括号加英文……不要用别的标签」。改成「这是唯一例外」也没用——
24k 的提示词里，那条格式禁令更早、更硬。

**结论：不要指望模型输出它自己的格式规则禁止的记号。能力必须落在通道侧。**
现在 `[贴纸]` 只留作保险（万一流出来，`split_mark()` 会剥掉，不让它出现在气泡里）。

#### 4.10.4 「无语」表情的判断

标签是人给的（`/表情标 无语` 或 `POST /api/qq/sticker/tag`），插件不认识像素。
同一个标签下可以标多张（挑的时候随机选一张），标 0 张等于关掉这个功能。
判断链是「便宜的先过」：

```
标签池里有货？→ 没有 = 这个功能不存在（且启动日志会说）
   ↓
硬币 QQ_STICKER_FACE_P（默认 0.2）
   ↓
这个房间 10 分钟内没发过？
   ↓
问模型一次：「刚才这几句有没有吹牛、油腻、自以为很帅、尬、莫名其妙炫耀的？」
   只收 {"face": true/false, "why": "…"}，**答不上来 = 不发**（fail-closed）
```

它接在 `ambient_p` 骰子**之前**——插话的骰子管「她想不想说话」，这张管「她说不出来」，
是两件事。发完会顶掉 `_last_checked`，五分钟内不会再插话。

### 4.11 QQ 空间

`channels/qzone.py`：她自己的独白，**不是回复任何人**，所以不需要回合那套机械，
需要的是**日历**和**克制**。

「独白」说的是内容形态（没有人问、没有上文），**不是语气**：空间是公开的，好友、同事、
半熟的人都刷得到，所以口径按 `characters/world/<id>-qzone.md` 走——写的是公共场合的她
（ふゆ），不是私下的那个人。私聊的语气（「あんた」「〜わよ」那一侧）在空间里一句都不能出现。

**接口事实**（NapCat 扩展，不在 OneBot 11 里）：

```
send_qzone_msg {content, images[], ugc_right, target_uins[]} → {tid}
delete_qzone_msg {tid}
```

`ugc_right`：1 所有人 / 4 好友（默认） / 16 部分好友 / 64 仅自己 / 128 部分好友不可见。
**没有只读接口**——不能「先问能不能发」，只能靠第一次失败来发现，
所以 `unsupported` 会被记 24 小时而不是每分钟重试。

**发不发的判定（`maybe()`）**：

```
QQ_QZONE=1 且 有人设文件 world/<id>-qzone.md（没有就拒发）
→ 过了 QQ_QZONE_HOUR（默认 21:00，按 SCCORE_TIMEZONE）
→ 今天没发过（qzone:day）
→ 距上一条 ≥ QQ_QZONE_GAP（20 小时）
→ 不在失败等待期（2 小时）
→ 掷一次骰子并落库（qzone:roll:<日期>，一天只掷一次）
```

**两个踩过的坑**（都是「配置是好的，但从来没发过」）：

1. **时区**。这条链上原来的钟是 `datetime.now().astimezone()`，也就是**容器的**时区；
   compose 只给 napcat 设了 `TZ`，内核容器是 UTC。于是 `QQ_QZONE_HOUR=21` 实际要等到
   北京时间早上五点才算到点。现在和别的定时任务一样读
   `datetime.now(_zone(settings.timezone))`——「几点之后」在整条通道里是同一个意思。
2. **启动那一下**。`_qzone` 是 `_serve` 里 spawn 的，第一次 tick 跑在 socket 连上之前，
   会拿到 `not connected`。原先那算一次**发布失败**：写一条失败记录、把今天压掉两小时，
   于是「过了点的晚上重启一次」就等于「今天不发」。现在 `network` 不算发布——不写历史、
   不压等待，下一个 tick 再试；循环本身也先看 `client.connected`。

**内容闸（`_check`，拒绝而不是提醒）**：

| 规则 | 值 |
|---|---|
| 长度 | 16 ~ 240 字 |
| 5 位以上连续数字 | 拒（QQ 号 / 群号 / 电话） |
| 链接、`@` | 拒 |
| 破功词（AI / 机器人 / 模型 / 提示词 …） | 拒 |
| 与最近 8 条**真的发出去过**的重复 | bigram Jaccard ≥ 0.6 → 重写一次，还像就放弃 |

最后一条有个已修的细节：**判重只和成功的比**。失败那条墙上没东西，
把它算进去会让同样的句子在一周的重试里都用不了。

**失败分类与后果**（`_kind`）：

| 类型 | 判据 | 后果 |
|---|---|---|
| `network` | `not connected` | 等 2 小时 |
| `login` | 登录 / skey / 失效 | 等 2 小时，日志点名「登录态没了」 |
| `limited` | 频繁 / 风控 / 验证 | 等 2 小时，不硬撞 |
| `unsupported` | 不支持 / 未知 API | 记 24 小时（升级 NapCat 后自愈） |
| `rejected` | 其它 | 落库，等 2 小时 |
| `unclear` | 回包成功但没有 `tid` | **花掉今天**——可能已经发出去了 |

**接口**：`GET /api/qzone`、`POST /api/qzone {text?, publish?}`、`POST /api/qzone/delete {tid}`。
手动发不过日历，但**过内容闸**；开关关着时它也拒绝（否则「功能关着」只是「定时器关着」）。

`GET /api/qzone` 里的 `last_why` / `last_at` 是「为什么今天没发」的答案（「还没到点」
「今天抽到的签是不写」「还没连上」…），`now` 是它当时用的钟——一个两小时前的理由看起来
和现在的理由一模一样，所以两个都带上。

### 4.11.1 回她自己墙上的评论

`channels/qzone_reply.py`：**只看她自己的说说下面有没有人评论**，有就回一句，没人评论就不管。
不是好友动态、不是别人的墙——那两样她不需要。

**先说清现实**：NapCat 的 QZone 官方接口只有 `send_qzone_msg` 和 `delete_qzone_msg`，
`get_qzone_msg_list` / `send_qzone_comment` 在 mlikiowa/napcat-docker 上一律回
`retcode 1404 不支持的API`。所以走 `channels/qzone_web.py`——用 NapCat 已经给出的 Cookie
直接问 QZone：

```
get_cookies {domain: user.qzone.qq.com}  → p_skey / skey / p_uin
g_tk = hash33(p_skey)                    → 评论的签名
读说说：emotion_cgi_msglist_v6            → tid / content / created_time / cmtnum
读评论：emotion_cgi_msgdetail_v6          → 评论的 tid、uin、name、content，以及 list_3（楼中楼）
回评论：emotion_cgi_re_feeds（h5 域名）    → paramstr=2 + commentId/commentUin + 正文前缀标记
```

分工是刻意的：`qzone_web.py` 只管协议（Cookie、签名、解析、失败分类），`qzone_reply.py`
只管「要不要回、回什么」。`QQ_QZONE_WEB=0` 时它不工作——这条功能没有 action 版本，
因为官方接口里根本没有「读评论」这件事。

**一次 tick（`poll()`，5 分钟）**：

```
没开 / 没人设文件 / 还在暂停期 → 说不回，以及为什么
拉最近 N 条说说（QQ_QZONE_REPLY_POSTS，默认 3）
  → cmtnum = 0 的跳过（没人评论，不值得再发一个请求）
  → cmtnum 没变、且距上次看不过 30 分钟，也跳过（见下面「cmtnum 只数一级评论」）
  → 否则拉一次评论列表，逐条过闸，没见过的写进 qzone_comments
第一次拉取（qzone:reply:begun 为空）→ 只记录，不回
否则：按时间从旧到新，回得起就回
```

**回不回的四道闸**：

| 闸 | 规则 |
|---|---|
| 是谁 | 她自己发的评论（`uin` 是自己）不回、`QQ_QZONE_REPLY_IGNORE` / `QQ_IGNORE_IDS` 不回 |
| 说给谁 | **楼中楼里别人之间的对话不看**。只回「对她说」的那句：一级评论算对她说的；楼里的回复要看 `@{uin:…}` 标记指向谁，指向别人就跳过 |
| 多旧 | 只回 `QQ_QZONE_REPLY_AGE`（72 小时）以内的——评论是留言，窗口比说说宽，但回上周的也不算聊天 |
| 一小时几条 | `QQ_QZONE_REPLY_HOURLY`（6），**滚动窗口**（`replied_at >= now-1h`），不是整点重置。按天算会把第七条推到第二天早上，留言过夜就凉了 |
| 两条之间 | `QQ_QZONE_REPLY_GAP`（5 分钟）。**三十是从「回别人动态」那版继承下来的**：那里一串评论像机器在刷feed；这里是她墙上的留言，20:20 写的等到 20:50 才回，不叫克制，叫不理人。五分钟只够防「两条挤在一起」 |

**说不说话，是模型的事**，但那条线是「辱骂」，不是「不客气」：低俗/性相关、骂人、
纯属复读报错的，只输出 `SKIP`；**找茬、抬杠、拿她跟别人比的要回**——损回去正是她的本事，
「要扣分哦」比沉默更像她。另外 `qzone_reply.COARSE` 那一小串词在**进模型之前**就挡掉了——不是
内容审查，是「这种话她不回」，而一个被客客气气问的模型有时会硬回。

**内容闸**（`check`）：4～60 字，5 位以上连续数字 / 链接 / `@` / 破功词一律拒。

**状态**：`qzone_comments.state` 三态——`0` 待回、`1` 回过（`reply` 是原话）、`2` 不回
（第一次拉取时就有的、她自己发的、被忽略的人、或者模型说 SKIP）。写进库而不是内存：
重启不能让同一条被回两次，也不能让当天的额度归零。

**接口**：`GET /api/qzone/comments`（看到什么、回了谁、暂停状态）、
`POST /api/qzone/poll`（现在拉一次）、
`POST /api/qzone/reply {comment_tid, text?}`（手动回一条；不给 text 就让她自己写）。

**一条回复要落进楼里，四样都得对**（`Web.reply`，真机验过）：

```
POST https://h5.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds?g_tk=…
topicId   = <说说作者uin>_<说说tid>__1
paramstr  = 2                       ← 1 是新评论，2 是回复
commentId = <一级评论的 tid>         ← 锚点必须是楼根，指向回复会被忽略
commentUin= <一级评论的作者 uin>
content   = @{uin:<被回复的人>,nick:<昵称>,auto:1} 正文
```

`@{uin:…}` 那个前缀不是装饰：QZone 靠它画「回复 某某」，缺了它，回复就变成一句普通评论。

**六个坑**，都试出来过：

- `topicId` 传裸 tid 会被当成 `t1_uin=0 / t1_tid=0`，回 `-10004 参数错误`；必须是
  `<作者uin>_<tid>__1`。**少了 `__1` 也能过**，但写出来的只是普通评论。
- **写接口的 `g_tk` 必须在 query 里**（`?g_tk=…`）。只放在表单里，`emotion_cgi_re_feeds`
  会回一个**包体为空的 HTTP 500**——读接口两种都认，所以这个差别只在「写」上露头，
  而且长得像限流。查法：同一分钟里手写请求成功、`Web.reply` 失败，差异就只剩 URL。
- **失败模式是「成功」**：`t2_*` 那套参数（`t2_uin`/`t2_tid`）在真机上回 `code:0`，
  却在说说下面多出一条普通评论——日志里看起来一切正常。所以「回出去了」不等于
  「回进楼了」，唯一的验证是把评论列表读回来，看那条评论的 `list_3`。
  这条弯路走过一次，别再走：`t1_*`、`t2_*`、`addreply_ugc` 都不是楼中楼。
- **`cmtnum` 只数一级评论**（实测 `cmtnum=1` 时楼下已有两条回复），所以「评论数没变就不看」
  会漏掉「有人回复了她的回复」。`RECHECK=30` 分钟是补这个洞的：数没变也每半小时读一次。
- **评论发出去收不回来**：`emotion_cgi_delcomment` 在真机上一律 HTTP 500（试过九种参数
  组合），所以通道不提供「撤回」。上面那些闸因此不是小心，是唯一一道防线。
  （有意思的是 500 之后评论确实被删掉了——它不可靠，不写成功能。）
- 「第一次拉取」记的是 `qzone:reply:begun` 这个 flag，不是「表是空的」——好友那天没评论的
  部署，用后者会把每一次都当成第一次，于是永远不回。

**楼中楼在库里是 `root_tid` + `comment_tid`**：QZone 给一级评论和回复各自编号，`1` 两边
都有，所以回复存成 `<root>.<tid>`，锚点另存一列。发的时候只用到锚点，`tid` 只是身份。

### 4.12 其余子模块

| 模块 | 一句话 | 关键点 |
|---|---|---|
| `ws.py` | 零依赖 WebSocket 客户端 | 只实现用到的部分；不实现扩展、子协议、分片发送 |
| `onebot.py` | OneBot 11 客户端 | echo 关联；断线 1s→60s 退避；`on_connect` 用 `create_task`（**不能 await**：回包要靠同一个读循环） |
| `world.py` | 三层知识 | `office.md`（每人一行，每轮进缓存）→ `idols/<id>.md`（提到才进）→ `meta.md`（问到才进） |
| `digest.py` | 记忆文件 | 整份重写不是追加；水位记 `last_seq`；<4 条新消息不动；写到卷上而非镜像里 |
| `identities.py` | QQ 号 ↔ 内核用户 | `resolve()` 建用户；`turns` 计数；`loving` 标记只增不减 |
| `texts.py` | 拒绝台词 | 键是稳定的（`quota.turns`），话是角色的（`world/<id>-refusals.toml`） |
| `stage.py` | 角色自己的措辞 | 扁平键值；缺键=默认，空值=默认，读不了=默认 |

**`digest.py` 的一个权限坑**：容器里跑的是 uid 10001，宿主机上 `characters/memory/` 必须是它可写的：

```bash
mkdir -p characters/memory
chown 10001:999 characters/memory
chmod 2777 characters/memory      # setgid：新建文件继承组
```

否则日志里会出现 `qq: dm 的记忆写不进文件（PermissionError …）`。

---

## 5 Web 端

`web/` 是**手写的 SPA**，不是打包产物：

```
web/
├── index.html      空壳（1843 字节），JS 挂载
└── assets/
    ├── app.js      200KB，全部界面逻辑（直接改，`node --check` 验语法）
    ├── api.js      请求封装
    ├── icons.js    图标
    ├── color.js    印象色计算
    ├── css/app.css 186KB（Tailwind v4 产物）
    ├── css/ja.css、zh.css  字体子集（按语言拆，为了首屏）
    └── gifts/*.webp  礼物图
```

`serve.py` 提供的能力：

| 路径 | 用途 |
|---|---|
| `GET /`、`/settings/*`、`/conversation/*` | 同一个 shell（刷新不 404） |
| `POST /api/turns` | **SSE 流**：一轮对话，边生成边给 |
| `GET /api/state` | 首屏大包：角色、能力、好感、记忆、消息 |
| `GET/POST /api/conversations*` | 历史对话列表 / 新建 / 删除 |
| `GET /api/roster`、`/api/stats`、`/api/export`、`POST /api/import` | 列表页、统计、数据导出导入 |
| `GET /media/*`、`/media/avatar/*` | 图片与头像（路径做 `parents` 校验，防穿越） |
| 插件路由（`host.routes()`） | `/api/qq/*`、`/api/qzone*`、`/api/affinity` 等 |

两个前端相关的机制值得知道：

1. **资源指纹**：`serve.py::_page_html()` 把 `web/assets` 下最新的 mtime 当版本号拼到链接上，
   所以改了 JS 不用手动清缓存。
2. **能力驱动**：界面按 `GET /api/state` 的 `capabilities` 决定画不画某个按钮。
   例如 `capabilities.call.enabled` 为 false 时不画电话键。
   **加一个前端功能，先想清楚它在能力里怎么表达**，而不是在页面里写死。

`tools/check_classes.py` 解决一个很隐蔽的问题：Tailwind v4 的产物只包含源码里出现过的类，
所以从别处搬 CSS 时，写进 `class` 字符串里的类可能根本没有样式——静默失效。
部署前跑一下它（退出码非 0 表示有类缺失）。

---

## 6 数据流全景

三条链，各自的读写完全分开。把三张图记住，排查问题时能立刻定位到层。

### 6.1 网页一轮对话

```
浏览器 POST /api/turns {text, photos, own_keys}
   │
   ▼ serve.py::_stream（SSE 头先发出去）
App.turn(character, text, photos, conv, own_keys)
   │ refresh()（人设变了就重建 Engine）
   │ conversation()（没有就建）
   │ 自己带了 key？→ replace(engine, llm=own)，facts["quota.exempt"]=True
   ▼ Engine.turn(...)
     gate → judge → enrich → build → stream → store → observe
   │
   ▼ 事件 → wire() → `data: {...}` 一行一条
浏览器按 delta 打字，done 之后拉 /api/history 对齐

写：messages（1 user + 1 assistant）、memories、affinity(+scenes)、
    quota_uses、photo_uses、service_calls
读：characters/、affinity、memories、producer_profile、facts、stories
```

### 6.2 群消息

```
QQ → NapCat → ws → OneBot → QQChannel
   ├ 被叫到 → _turn → App.turn(surface="group", extra_facts={affinity.loving})
   │            └ 回复分段 → Outbox → send_group_msg → QQ
   ├ 没叫到 → _interject（可能：无语表情 / 插一句 / 什么都不做）
   └ 定时：周一表情 / 空间 / 记忆重写 / 收藏同步 / 好感地板 / 主动私聊
```

**surface 是这一层最重要的一个参数**，它决定：

| 读 surface 的地方 | 影响 |
|---|---|
| `Affinity.change/read` | 数字写进哪一行（group 还是 dm） |
| `Affinity.prompt_sections` | 用哪份好感度生成语气 |
| `Engine`（经 `TurnContext`） | 传给插件的上下文 |

### 6.3 定时任务

```
QQService._serve() 启动时 spawn
   ├ 每个循环自带 try/except（一个任务坏了不能带走整条通道）
   ├ 决策落库（qq_flags / qq_digest），内存只留「已加载的值」
   └ 发送走同一个 Outbox（节流共享，不会和回复抢）
```

### 6.4 关键表的读写矩阵

| 表 | 写 | 读 |
|---|---|---|
| `messages` | `Store.add_turn` | 提示词历史、`/api/history`、统计、记忆重写 |
| `qq_identities` | `_stream` 结束后 `bump`、`refresh_love`、`/静音` | 每次群消息（turns/role/muted/loving） |
| `qq_flags` | 各定时任务 | 各定时任务（重启后恢复） |
| `qq_outbox` | `Outbox.send` | 重连后 `resend_pending` |
| `affinity` / `affinity_scenes` | `after_turn`、送礼、`set_value`（地板） | 提示词、`/好感`、`/api/affinity` |
| `qq_stickers` | `_harvest`、`import_faces`、`/表情标` | `_sticker_row`、`_maybe_face` |
| `memories` | `memory.after_turn` | `prompt_sections` |
| `qq_digest` | `digest.run` | `due()` 判断、`section()` |

---

## 7 运行环境与依赖

### 7.1 硬性要求

| 项 | 要求 | 说明 |
|---|---|---|
| Python | **≥3.11** | 用了 `asyncio.timeout`（3.11）、`tomllib`（3.11） |
| 第三方包 | **无** | `dependencies = []`；WebSocket、HTTP 客户端、SQLite 都是标准库 |
| 系统包 | `tzdata` | `ZoneInfo` 需要时区数据库，否则她会在半夜说「おはよう」 |
| 磁盘 | 约 1GB+ | 镜像 ~150MB、`characters/` 约 2MB、图片与表情按使用增长 |
| 内存 | 512MB 起 | 单进程，实际常驻约 150~250MB |
| 网络出站 | 模型 API、NapCat 本地 | 空间功能还需要能连 QQ |
| Docker | 有 compose v2 | 单机部署推荐 |

### 7.2 外部服务（按需）

| 服务 | 何时必需 | 缺了会怎样 |
|---|---|---|
| OpenAI 兼容的对话模型 | **总是** | 起不来（`Settings.from_env` 抛 `RuntimeError`） |
| 判分模型 | 否 | 默认用对话模型，`temperature=0` |
| OpenAI 兼容的 `/embeddings` | 仅 `SCCORE_RECALL=vector` | 自动退回 `whole` |
| Fish Audio | 仅语音 / 电话 | 语音插件下线、电话键不画、她也不提电话 |
| NapCat | 仅 QQ 通道 | 通道不启动，网页照常 |

### 7.3 不用 Docker 直接跑

```bash
cd scbot-new
cp .env.example .env && vi .env          # 至少填 CHAT_LLM_*
python3 -m venv .venv && . .venv/bin/activate
pip install -e .                          # 可选：装成包，主要为了拿到 console 脚本
python serve.py                           # 读 ./.env，再读环境变量
```

想不花钱先看界面：`python serve.py --fake` —— 固定台词，不需要 key。

---

## 8 部署步骤

以「一台能跑 Docker 的 Linux（Debian/Ubuntu/PVE 宿主）+ 一个 QQ 小号」为目标。
每步都给出**验证方法**，出了问题能立刻知道是哪一步。

### 第 0 步：准备清单

- [ ] 一台 Linux，能装 Docker
- [ ] 一个 OpenAI 兼容服务的 `base_url` / `api_key` / `model`
- [ ] 一个 **QQ 小号**（机器人本体），能和主号加好友
- [ ] 一个群（先拉小号进群，不需要给管理员）

### 第 1 步：装 Docker

```bash
apt-get update && apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list
apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
```

验证：`docker run --rm hello-world`（或 `docker compose version`）。

> PVE 宿主装 Docker 会往 iptables 加链。只要端口都绑 `127.0.0.1` 就不会和 pve-firewall 打架。
> **不要**把 6099 映射成 `0.0.0.0`——那是把 QQ 登录入口挂到局域网上。

### 第 2 步：放代码

```bash
# 开发机
tar czf scbot.tgz --exclude='__pycache__' --exclude='.env' --exclude='*.db*' \
    --exclude='media' --exclude='sccore' -C /path/to/localbot-sc scbot-new
scp scbot.tgz root@<host>:/opt/

# 目标机
mkdir -p /opt/scbot-docker
tar xzf /opt/scbot.tgz -C /opt/scbot-docker --strip-components=1
cd /opt/scbot-docker && ls     # 应该看到 Dockerfile docker-compose.yml serve.py channels/ ...
```

### 第 3 步：写 `.env`

```bash
cd /opt/scbot-docker
cp .env.example .env
chmod 600 .env
vi .env
```

**最少要填的六项**：

| 变量 | 填什么 | 填错的症状 |
|---|---|---|
| `CHAT_LLM_BASE_URL` | 例 `https://api.deepseek.com/v1` | 启动即报缺环境变量 |
| `CHAT_LLM_API_KEY` | 你的 key | 同上 |
| `CHAT_LLM_MODEL` | 例 `deepseek-flash` | 调用 400 |
| `QQ_SELF_ID` | **小号**的号 | 它把自己的话当别人说的，或者不认为自己被 @ |
| `QQ_GROUP_IDS` | 群号（不是群名），逗号分隔 | 收不到任何消息 |
| `NAPCAT_WEBUI_TOKEN` | 自己设一个强口令 | NapCat 起不来（compose 里是必填） |

推荐：

```
QQ_ENABLED=1
QQ_CHARACTER=fuyuko          # characters.toml 里的 id，决定用哪份人格
QQ_OWNER_IDS=<你的主号>
QQ_PRIVATE=0                 # 先关私聊，群里跑通再说
SCCORE_RECALL=whole          # 有 facts 文件就用整份
```

### 第 4 步：起容器

```bash
cd /opt/scbot-docker
docker compose up -d --build
docker compose logs -f scbot
```

**成功的日志长这样**（顺序有意义）：

```
scbot-new  ←  http://0.0.0.0:8100 （28 位角色 / characters, recall, stories, quota, photos, affinity, memory, profile, voice, status, gifts, qq / <model>）
QQ 通道已启动：群 123456789 / 角色 fuyuko / ws://napcat:3001
qq: 周一 9:00 之后发一张「上班」表情到 123456789（表情包里标了「上班」的有 1 张）
onebot: connected
```

- 第一行末尾的插件列表就是**实际启动成功**的插件；被关掉的不会出现在这里。
- `QQ 通道没启动：<原因>` 会明确说缺哪个变量（看 `QQConfig.reason_off()` 的五个条件）。
- 容器状态：`docker compose ps` → `scbot` 应为 `healthy`（healthcheck 探 `/api/roster`）。

### 第 5 步：登录 QQ 小号

WebUI 只绑在宿主机回环上，所以从你自己的电脑开隧道：

```bash
ssh -L 6099:127.0.0.1:6099 root@<host>
# 浏览器打开 http://127.0.0.1:6099/webui?token=<你设的口令>
```

用**小号**扫码。然后在 WebUI 里确认网络配置：

1. **WebSocket 服务端** 已开启，端口 `3001`，监听 `0.0.0.0`（compose 已经设 `MODE=ws`）；
2. 如果给 OneBot 设了 token，把同一个值写进 `.env` 的 `QQ_ONEBOT_TOKEN`，
   然后 `docker compose up -d scbot` 让它重连。

> 第一次登录可能遇到滑块 / 设备锁，只能手动过。登录态存在 `napcat-qq` 卷里，重启容器不用重扫。

**掉登录了怎么办**：NapCat 会退回扫码页，`3001` 不再监听，scbot 一直 `ConnectionRefused`。
三条路：

1. 密码登录走 WebUI API（实测一次成功、不要验证码）：
   `POST /api/auth/login {hash: sha256(token + ".napcat")}` 拿 `data.Credential`
   → `POST /api/QQLogin/PasswordLogin {uin, passwordMd5}`；
2. 二维码卡住不刷新时 `docker compose restart napcat` 重出；
3. 扫码用的是 NapCat 的 WebUI 页面，手机 QQ 扫。

### 第 6 步：把它拉进群，验证

用小号进群后，在群里发：

```
@小号 こんばんは
```

**第一条回复会带 @ 和引用**，后面的段是连发的（每段之间 0.8~1.5 秒）。
如果没有反应，看第 7 步。

再验一次「她记得」：

```
@小号 我叫什么来着
```

### 第 7 步：验证接口

```bash
curl -s 127.0.0.1:8100/api/qq/status | python3 -m json.tool
```

```json
{
  "enabled": true, "connected": true, "running": true,
  "groups": ["123456789"], "people": 3, "turns": 12,
  "sent": 26, "failed": 0, "events": 340, "last_error": "",
  "problem": ""
}
```

| 字段 | 含义 |
|---|---|
| `problem` | 空 = 配置齐了；非空 = 缺什么（`reason_off()` 的原话） |
| `connected` | socket 连着；`events` 在涨说明真的在收消息 |
| `people` | 认过多少人（`qq_identities` 行数） |
| `sent` / `failed` | 发送成功 / 失败的条数（失败会留 pending 补发） |
| `last_error` | 最近一次 socket 或处理异常 |

### 第 8 步：常用运维

```bash
cd /opt/scbot-docker
docker compose logs -f scbot                 # 她的日志
docker compose restart scbot                 # 只重启内核，NapCat 登录态不动
docker compose up -d --build                 # 改了代码
docker compose down                          # 全停（卷保留）
docker compose pull napcat && docker compose up -d napcat   # 升级 NapCat
```

**备份**（数据库 + 图片都在卷里）：

```bash
mkdir -p /opt/scbot-docker/backups
docker run --rm -v scbot-docker_scbot-data:/data:ro \
  -v /opt/scbot-docker/backups:/backup alpine \
  tar czf /backup/scbot-data-$(date +%F).tgz -C /data .
```

恢复：先 `docker compose stop scbot`，解回卷，再起。

> **`docker compose down -v` 会删卷**，包括数据库、图片、表情包、记忆文件。
> 改人设不需要它，升级镜像也不需要它。

### 第 9 步（可选）：不接 QQ 先跑通全链路

不需要 QQ、不需要模型、不花钱：

```bash
# 终端 A：假 NapCat（会自动 @ 她）
python3 tools/fake_napcat.py --port 3001 --group 100 --self 999 --user 42

# 终端 B：内核 + 通道 + 假模型
QQ_ENABLED=1 QQ_ONEBOT_WS=ws://127.0.0.1:3001 QQ_SELF_ID=999 \
QQ_GROUP_IDS=100 QQ_OWNER_IDS=42 QQ_MERGE_SECONDS=0.2 \
SCCORE_DB=/tmp/smoke.db SCCORE_MEDIA=/tmp/smoke-media \
python3 serve.py --fake --port 8100
```

终端 A 会打印她发回来的每条消息和它的段类型（`['reply','at','text','text']`），
终端 B 会打印 `onebot: connected`。**这是改通道代码后的第一道回归测试。**

---

## 9 配置参考

分五组。**默认值就是「安全的那一侧」**（关、低频、只认名单里的人）——
聊天机器人的失败模式是「不小心被打开」。

### 9.1 模型与内核

| 变量 | 默认 | 说明 |
|---|---|---|
| `CHAT_LLM_BASE_URL` / `_API_KEY` / `_MODEL` | — | **必填**，任何 OpenAI 兼容服务 |
| `SCCORE_JUDGE_MODEL` / `_BASE_URL` / `_API_KEY` | 空 | 判分用别的模型；空 = 用对话模型（`temperature=0`） |
| `SCCORE_HOST` / `SCCORE_PORT` | `0.0.0.0` / `8100` | 监听地址 |
| `SCCORE_DB` | `scbot.db` | SQLite 路径（容器里 `/data/scbot.db`） |
| `SCCORE_MEDIA` | `media` | 图片与表情包目录 |
| `SCCORE_CHARACTERS` | `characters` | 角色数据目录 |
| `SCCORE_CHARACTERS_CONFIG` | `characters.toml` | 名册文件名 |
| `SCCORE_TURNS_PER_HOUR` | 60 | 每人每小时的回合上限 |
| `SCCORE_PHOTOS_PER_HOUR` | 8 | 每人每小时能发几张图 |
| `SCCORE_HISTORY_TURNS` | 20 | 提示词里保留几轮（0 = 全部） |
| `SCCORE_TIMEZONE` | `Asia/Tokyo` | 日界所在时区（记忆、额度、空间日历、作息）——**她的钟是日本时间**：她是日本人，「深夜」「该睡了」都按这个算 |
| `SCCORE_AFFINITY_START` | 20 | **群聊**陌生人起步好感度（网页/私聊是 50） |
| `SCCORE_RECALL` | `off` | `off` / `whole` / `vector` |
| `SCCORE_RECALL_TOP` / `_MIN` / `_GATE` | 4 / 0.35 / `off` | 只 vector 模式有用 |
| `SCCORE_STORIES` | `off` | `on` 才做剧情检索 |
| `SCCORE_CALL` | `auto` | `auto`（跟语音走）/`off`/`on`；写错启动即报错 |
| `SCCORE_EMBEDDING_*` | 空 | vector 模式必需，否则自动退回 whole |
| `FISH_AUDIO_API_KEY` / `_BASE_URL` | 空 | 语音与电话的开关 |
| `SCCORE_VOICED_BY` | 空 | 语音模型名，影响回复格式指令 |

### 9.2 QQ 通道（必填项）

| 变量 | 默认 | 说明 |
|---|---|---|
| `QQ_ENABLED` | 0 | 总开关 |
| `QQ_CHARACTER` | 空 | 用哪份人格（`characters.toml` 的 id）。**空 = 不启动** |
| `QQ_SELF_ID` | 空 | 小号的号。用来过滤自己、判断被 @ |
| `QQ_GROUP_IDS` | 空 | 要待在哪些群（**群号**，逗号分隔） |
| `QQ_OWNER_IDS` | 空 | 谁是「制作人」 |
| `QQ_PRIVATE` | 0 | 要不要接私聊 |
| `QQ_PRIVATE_IDS` | 空 | 谁能私聊（空 = 用 `QQ_OWNER_IDS`） |
| `QQ_ONEBOT_WS` | `ws://127.0.0.1:3001` | 容器里是 `ws://napcat:3001` |
| `QQ_ONEBOT_TOKEN` | 空 | 与 NapCat 里设的一致 |
| `QQ_IGNORE_IDS` | 空 | 完全不理的号：消息不进 `heard`，也不回 |
| `QQ_BOTS` | 空 | 别的角色的机器人（`号码=角色id`）。说话时提示词里多一行「这个号不是人，是〇〇的机器人」，语气按认识的人走——不标的话，一段ルカ的台词会被当成陌生群友的寒暄来接 |
| `NAPCAT_WEBUI_TOKEN` | — | **必填**（compose 里没有它会拒绝启动） |

### 9.3 群聊行为

| 变量 | 默认 | 说明 |
|---|---|---|
| `QQ_MERGE_SECONDS` | 1.2 | 连发合并窗口（秒） |
| `QQ_MEMBER_COOLDOWN` | 20 | 同一个人两次回复的最小间隔（主人不受限） |
| `QQ_GROUP_CONCURRENCY` | 2 | 同时进行几轮对话（超出的排队） |
| `QQ_TURN_TIMEOUT` | 180 | 一轮最多等多久（秒），防止卡住占死并发位 |
| `QQ_AMBIENT` | 0 | 没人叫她时会不会自己说话 |
| `QQ_AMBIENT_DAILY` | 10 | 一天最多插话几次（**所有群合计**，0 = 不限） |
| `QQ_AMBIENT_GAP` | 240 | 两次插话之间至少隔几秒 |
| `QQ_AMBIENT_CHECK` | 300 | 两次「要不要插话」的判断之间至少隔几秒（成本闸） |
| `QQ_AMBIENT_HEARD` | 8 | 给她看最近几行 |
| `QQ_AMBIENT_P` | 1.0 | 每条群消息被「认真看一眼」的比例 |
| `QQ_AMBIENT_OWN_P` | 0.35 | 插话时「说自己的事」而不是接话的比例 |
| `QQ_AMBIENT_STICKER_P` | 0.3 | 插话时只发一张表情的比例 |
| `QQ_AMBIENT_IDLE` | 0 | 安静多少分钟后她自己开口（分钟，0 = 关） |
| `QQ_CALLED` | 1 | 叫名字也算叫她（0 = 旧的「只认 @」） |
| `QQ_OWNER_STAGE` | close | 主人在群里的熟悉度下限（空 = 和别人一样从零爬） |

### 9.4 表情、空间、记忆

| 变量 | 默认 | 说明 |
|---|---|---|
| `QQ_STICKERS` | 1 | 收录表情、能发 |
| `QQ_STICKERS_PUBLIC` | 1 | 群友能不能用 `/表情` |
| `QQ_STICKER_DIR` | 空 | 表情包目录（空 = `<media>/stickers`） |
| `QQ_STICKER_FACE` | 无语 | 「说不出话」用哪个标签（空 = 关掉这个反应） |
| `QQ_STICKER_FACE_P` | 0.2 | 看到难绷的话之后真发的概率 |
| `QQ_SHARED_COMMANDS` | `表情,好感` | 群里谁都能用的指令白名单 |
| `QQ_MONDAY_HOUR` | 9 | 周一几点发「上班」表情（-1 = 关） |
| `QQ_MONDAY_GROUPS` | 空 | 只发到哪几个群（空 = 全部） |
| `QQ_QZONE` | 0 | 要不要自己发说说 |
| `QQ_QZONE_HOUR` | 21 | 几点之后可能发 |
| `QQ_QZONE_P` | 0.35 | 到点后今天写不写的概率（一天只掷一次） |
| `QQ_QZONE_RIGHT` | 4 | 1 所有人 / 4 好友 / 16 部分 / 64 自己 / 128 部分不可见 |
| `QQ_QZONE_GAP` | 20 | 两条之间至少隔几小时 |
| `QQ_DIGEST` | 1 | 要不要写记忆文件 |
| `QQ_DIGEST_DM_DAYS` / `_GROUP_DAYS` | 3 / 7 | 多久重写一次 |
| `QQ_DIGEST_PROMPT` | 1 | 记忆要不要进提示词（0 = 只存档） |

### 9.5 好感度与恋爱（都是「产品决策」，不是技术参数）

| 变量 | 默认 | 说明 |
|---|---|---|
| `QQ_OWNER_AFFINITY` | 0 | 主人**恋爱前**的好感度地板（0 = 不管）。防止判分把满好感的人判成「疏远」 |
| `QQ_OWNER_LOVE` | 0 | 进入恋爱的门槛（建议 95，比「亲密」的 85 高）。0 = 不启用 |
| `QQ_OWNER_LOVE_TURNS` | 20 | 还要说过这么多次话（两道门缺一不可） |
| `QQ_OWNER_LOVE_FLOOR` | 0 | 恋爱中的地板。**0 = 没有地板**（这样才能真的不高兴、才可能生闷气） |
| `QQ_OWNER_LOVE_PING` | 0 | 恋爱后会不会先发消息（只在私聊） |
| `QQ_OWNER_LOVE_PING_MIN` / `_MAX` | 60 / 150 | 两条之间的随机间隔（分钟） |
| `QQ_COMMANDS` | owner | `owner` = 只有主人能用管理指令；`all` = 谁都能用 |
| `QQ_WORLD` / `QQ_META` / `QQ_BRACKET` | 1 | 三层知识里各开哪一层 |
| `QQ_VOICE` | 0 | 语音条（尚未实现，保持 0） |

**一条容易误会的规则**：`SCCORE_CALL` 不是人设里的死规矩，而是每轮现问
`App.call_ready()`。理由：写在 `fuyuko-dm.md` 里的「电话打不通」，
在配上语音模型之后仍然是错的——规则必须随部署变。

---

## 10 常见问题

按「现象」查，不要按「模块」查——排查的人先看到的是现象。

### 10.1 起了但完全没反应

| 检查 | 命令 | 结论 |
|---|---|---|
| 通道启没启 | 日志有没有 `QQ 通道已启动：…` | 没有 → `curl /api/qq/status` 看 `problem` |
| 缺哪个变量 | `problem` 字段的原话 | 五个条件之一（§9.2） |
| socket 通没通 | `connected` / `last_error` | false → NapCat 的 WS 服务端没开，或服务名不对 |
| 有事件但没人回 | `events` 在涨吗 | 不涨 → 群号填错（填了群名），或机器人不在群里 |

```bash
curl -s 127.0.0.1:8100/api/qq/status | python3 -m json.tool
```

### 10.2 有时候不回，过一阵又好了

按三层查，从外往里：

1. **宿主崩过？** `last -x reboot`、`uptime`。崩了重启，队列会在重连后补发。
2. **QQ 掉登录？** NapCat 退回扫码页、`3001` 不监听、scbot 一直 `ConnectionRefused`。
   日志里会有 `qq: 现在登录的是 X，配置里是 Y —— 这个号被挤下线过`（`_check_login` 每 5 分钟查一次）。
3. **她自己卡住了？** 日志里 `qq: 一轮对话超过 180s 没说完，放弃了（<房间>）`。
   以前没有这个上限时，两个卡住的请求就占死了两个并发位——整条通道不说话。

### 10.3 @ 了没反应

按顺序排：

| 可能 | 怎么确认 |
|---|---|
| `QQ_SELF_ID` 填成主号了 | 日志里 `qq: 现在登录的是 …`，配置里对不上 |
| 被 20 秒冷却挡了（群友） | 日志 `qq: <号> again within 20s, skipped`；主人不受这条 |
| 额度用光 | 日志会带 `quota.turns` 的拒绝；`/好感` 能看到，或直接查 `quota_uses` |
| 被静音了 | `SELECT muted FROM qq_identities WHERE qq_id=…` |
| 她认为不该回（叫名字而非 @） | 日志 `qq: 有人提到了她，没回（<理由>）`。@ 不判断、必回 |

### 10.4 她「从来不用收藏的表情」

两个原因，都验证过：

1. **池子比例**：`SELECT count(*) FROM qq_stickers WHERE group_id=''` 应该是 14 左右，
   总包 180+。老代码从全包随机 → 7%，看起来就是不用。现在 `_sticker_row()` 先收藏。
2. **没有触发路径**：只有 `/表情` 命令、别人要、插话、无语判断四条。**她不会自己挂图。**

```bash
# 手动发一张验证链路
curl -s -X POST 127.0.0.1:8100/api/qq/sticker -d '{}' -H 'Content-Type: application/json'
# 指定标签
curl -s -X POST 127.0.0.1:8100/api/qq/sticker -d '{"tag":"无语"}' -H 'Content-Type: application/json'
# 看标签分布
docker exec scbot python -c "import sqlite3;d=sqlite3.connect('/data/scbot.db');[print(r) for r in d.execute(\"select tag,count(*) from qq_stickers group by tag order by 2 desc\")]"
```

### 10.5 周一的「上班」表情没发

`_monday()` 只认**标签**，不猜像素。日志在启动时就会说清楚：

```
qq: 周一 9:00 之后发一张「上班」表情到 123（表情包里标了「上班」的有 0 张）
```

补法：群里发一次那张表情（会被 `_harvest` 收进包），然后 `/表情标 上班`。
或者用 API：`POST /api/qq/sticker/tag {"sha1":"…","tag":"上班"}`。

### 10.6 空间发不出去

```bash
curl -s 127.0.0.1:8100/api/qzone | python3 -m json.tool
docker logs scbot --tail 50 | grep 空间
```

| 日志 | 含义 |
|---|---|
| `写不出来（…）` | 模型没给出合格内容；或者缺 `world/<id>-qzone.md` |
| `这一条不通过（…）` | 内容闸拦了（太长/短、数字、链接、@、破功词、重复） |
| `空间这一轮出错（…）` | 异常；`unsupported` 会记 24 小时不再试 |
| 接口返回 `"why": "空间功能没开（QQ_QZONE=0）"` | 开关没开；**手动路径也受开关管** |

**没发出的试探**：拿非法 `ugc_right` 打接口，NapCat 会在发布**之前**报参数错误，
既能证明接口在、账号在线，又不产出任何说说：

```python
# 期望：1200 ugc_right 参数不合法, 允许的值为: 1, 4, 16, 64, 128
```

### 10.7 记忆文件没写 / 写不进去

```bash
curl -s 127.0.0.1:8100/api/qq/status | grep -o '"digest[^,]*'   # 不一定有，看日志更准
docker logs scbot | grep 记忆
ls -la characters/memory/
```

三个原因：

1. **不到期**：私聊 3 天、群聊 7 天，且要求新消息 ≥4 条（`MIN_ROWS`）；
2. **权限**：`PermissionError` → 按 §4.12 修目录属主（uid 10001）；
3. **模型给了空回答**：日志 `qq: dm 的记忆这次没写成（模型给了空回答）`，水位不动，下一轮重试。

### 10.8 改了人设没生效

| 改的是哪 | 生效方式 |
|---|---|
| `world/*.md`、`-interests.md`、`-stage.toml` | 下一轮自动生效（`live.py`，文件要安静 0.35 秒） |
| `facts/<id>.md` | 它在 persona 槽、被 Engine 缓存 → `/重载` 或重启容器 |
| `sheets/<id>.md` | 同上 |
| `characters.toml` 增删角色 | `App.refresh()` 会重建 Engine；新头像文件要重启 |
| 代码 | 重建镜像：`docker compose up -d --build` |

`/重载` 返回 `{"dropped": …, "sheet": true, "generation": …}`，`dropped` 是被丢掉的提示词缓存数。

### 10.9 日志读不出来 / 是空白的

`docker logs` 报 `invalid character` 或完全空白，先想这个：
**宿主硬崩时 json 日志文件被写进了 NUL 字节**（scbot、napcat 都中过）。
`docker compose up -d` 重建容器即恢复。

### 10.10 群里语气不对（太甜 / 太冷 / 没有傲娇）

按这个顺序看，不要直接改人设：

1. **她现在在哪一档**：群里 `/阶段`（熟悉度）、`/好感`（距离）。
2. **私聊和群聊是两套语域**：私聊用 `-dm.md`（本性），群聊用 `-qq.md`（营业 + 斜线）。
   在群里觉得「像客服」，这通常是**设计如此**。
3. **高好感不该变温顺**：`affinity.py` 的 `KEEP_THE_EDGE` 紧跟在甜话后面追加
   （只对 close/intimate 生效）。觉得变软了，看这里有没有被谁改掉。
4. **stage 和 tier 在拉扯**：熟客好感低时两者相反，实测 turn 槽那条赢。
   要彻底解决得让 `stage_for` 按 tier 选（未做）。

### 10.11 报 `PermissionError: socketpair` 或每个请求都 500

某些宿主机（PVE + AppArmor 默认 docker profile）会拒绝 asyncio 启动时建 socketpair。
**这不是权限问题，是启动条件**：干净的 `python:3.11-slim` 同样起不来。
compose 里已经有 `security_opt: apparmor=unconfined`，`napcat` 还有 `seccomp:unconfined`
和 `shm_size: 256m`（Chromium 默认 64M 会让扫码页白屏）。

---

## 11 注意事项

这一节是「会真的咬人」的东西，按主题排。

### 11.1 数据与卷

- **`characters/` 是 bind mount，不是镜像内容。** 改人设不用重建镜像；重建镜像也只会把
  镜像里那份旧文本再盖回来一次（挂载点仍然覆盖）。这是两件事，不要混。
- **`docker compose down -v` 删卷**：数据库、图片、表情包、记忆全没。改配置不需要它。
- 备份命令见 §8 第 8 步。备份前 `stop scbot` 更稳（WAL 会自己 checkpoint，但别赌）。
- **SQLite 是 WAL 模式。** 只复制主库文件会读到旧数据——**要连 `-wal` / `-shm` 一起拷**。
  （踩过：打标之后做副本库预演，看到的是打标前的数据。）

### 11.2 时间与时区

- 数据库里所有时间戳是 **UTC**（`+00:00`）。「今天」「周几」「零点」的判断
  全部按 `SCCORE_TIMEZONE`（默认 `Asia/Tokyo`，日本时间）算。
- 容器里必须有 `tzdata`，否则 `ZoneInfo("Asia/Shanghai")` 失败，退回 UTC 偏移，
  于是「早上九点的表情」会在错误的时间发。
- 定时任务的判断都是「过了这个点之后」而不是「正好这个点」，所以一次 tick 的延迟
  （60~300 秒）不影响结果。

### 11.3 状态与重启

- **任何「下次什么时候」「今天发过没有」都要落库**（`qq_flags`），不能只放内存。
  这台机器一夜能重启三次；内存定时器每次从头摇，随机间隔永远走不到尽头。
- 「一天几次」的额度用**日期做 key**（`ambient:2026-10-27`），过零点自然换键，
  不需要清理任务。
- `stop()` 里必须取消所有定时 task，否则重启时日志里会有一堆
  「Task was destroyed but it is pending」——看起来像 bug，其实只是没收拾。

### 11.4 密钥

- `.env` 是唯一放密钥的地方，`chmod 600`，**不要提交、不要贴群里**。
- `.dockerignore` / `tar` 打包时要排除 `.env`、`*.db*`、`media`。
- 空间/表情的调试脚本里不要写明文账号密码（本项目已经清理过一轮）。
- 对外发布的分发包（`tools/pack_channel.py`）会检查禁用词（主机名、内网 IP、`sk-` 等），
  见该脚本的 `FORBIDDEN`。

### 11.5 并发与单实例

- **只支持单实例。** SQLite 一个连接 + 一把锁，`Outbox`、`Ambient` 计数都在进程内。
  开两个副本会双发、双计数。
- HTTP 层是「每请求一个线程 + `asyncio.run`」，所以**不要在请求处理里做长连接**（电话就没做）。
- QQ 侧并发是信号量 2（`QQ_GROUP_CONCURRENCY`），每个会话还有一把锁。
  调大之前先想清楚模型 API 的限流。

### 11.6 平台的坑

- **NapCat 的日志文件可能带 NUL 字节**，表现为 `docker logs` 报 `invalid character`。
  `up -d` 重建容器即恢复。
- **`send_qzone_msg` 是 NapCat 扩展，不是 OneBot 标准**。换别的 OneBot 实现，
  空间功能整个不可用（`unsupported` 会记 24 小时）。
- **没有「有人发说说」的事件**，也没有官方的好友动态接口。回别人的动态只能轮询，
  而且依赖装在 NapCat 上的 QZone 扩展（`get_qzone_msg_list` / `send_qzone_comment` 等）——
  名字随扩展变，所以代码是探测式的：第一个答得上的就记住。
- **`fetch_custom_face` 回包是数组**，不是对象。所以 `OneBot.call_raw()` 存在——
  `call()` 会把非 dict 缩成 `{}`，让唯一的列表接口看起来像空的。
- **QQ 消息的图片 `file` 字段是 NapCat 容器内的路径**，从这里读不到；只有 `url` 有用。

### 11.7 排查手法

- **不要用中文关键字 grep 远端日志**：SSH 传中文参数会编码错，`grep 发送` 什么都匹配不到。
  直接 `docker logs --tail 50` 看，或者在远端写脚本再执行。
- **查助手说过的话要搜 `messages.segments`**，`text` 列是空的（§2.6）。
- 排查「她为什么不说」优先看**日志里的 `qq:` 行**——每个「不回」的分支都写了一句理由，
  这是刻意加的（`_interject`、`_called`、`_maybe_face` 都有）。
- 改人设/改判断逻辑之前，**先用副本库预演**，不要拿真群试：
  复制 `/data/scbot.db`（含 `-wal`）→ 用 `Settings`/`App` 指过去 →
  `QQChannel(app, client=<假的>, config=..., outbox=Recorder())` →
  `await channel._turn(...)`，Recorder 只收集不发送。

### 11.8 已知的接缝（诚实清单）

| 接缝 | 现象 | 想彻底解决 |
|---|---|---|
| `stage`（按次数）与 `tier`（按好感）两套尺子 | 熟客好感低时提示词自相矛盾 | 让 `stage_for` 按 tier 选 |
| `GROUP_SURFACE` 是一个桶 | 两个群共用一份群聊好感度 | 把 key 改成 `group:<room>` |
| 电话没有后端 | `capabilities.call` 只控制提示词与按钮 | 需要麦克风→识别→模型→合成→播放的实时链路 |
| 空间没有只读接口 | 「能不能发」只能靠第一次失败发现 | 换实现 / 等 NapCat 加接口 |
| `voice` 只做「一段 mp3」 | 没有流式语音条（`QQ_VOICE=0`） | 接 `SpeechDelta` |
| 表情不会自己挂 | 只有「被要」「无语」「插话」三条路 | 需要一个新的触发条件，且要防刷屏 |

---

## 附录 A 测试与工具

### A.1 跑测试

```bash
cd scbot-new
PYTHONPATH=. python -m unittest discover -s tests -t .          # 全部
PYTHONPATH=. python -m unittest tests.test_channels -v          # 单个模块
```

**765 条，全离线**：模型是脚本化的（`ScriptedLLM`）、OneBot 是假的、时钟是注入的、
文件写在临时目录。所以它可以在任何机器上跑，不花钱、不联网、不发消息。

| 文件 | 条数 | 覆盖 |
|---|---|---|
| `test_channels.py` | 最多 | 通道：判定、合并、冷却、插话、表情、指令、档位、空间按钮 |
| `test_plugins.py` | 次多 | 各插件：好感度、生闷气、礼物、记忆、资料、剧情 |
| `test_qzone.py` | 39 | 空间：日历、内容闸、失败分类（含「没连上不算发布」）、手动入口 |
| `test_qzone_feed.py` | 25 | 别人空间：探测降级、只回一次、额度与间隔、第一次只记录 |
| `test_turn.py` / `test_host.py` / `test_prompt.py` / `test_protocol.py` | — | 内核四阶段、插件调度、提示词槽位、回复解析 |
| `test_store.py` / `test_quota.py` / `test_recall.py` / `test_stories.py` | — | 存储、滑动窗口、检索、话题匹配 |
| `test_live.py` / `test_judge.py` / `test_server.py` / `test_frontend.py` / `test_dropin.py` | — | 热加载、判分解析、HTTP、前端资源、插件可拆卸性 |

**测试风格值得学的一件事**：断言写的是**行为与理由**，不是实现细节。例如

```python
def test_an_empty_collection_still_falls_back_to_the_pack(self):
    ...
    self.assertIsNotNone(row, "收藏空了不等于没得发")
```

### A.2 工具

| 工具 | 用途 |
|---|---|
| `tools/fake_napcat.py` | 假 NapCat：自动 @ 她，打印回包段类型。**改通道后的第一道回归** |
| `tools/pack_channel.py` | 把 `channels/` 打成一个可独立交付的插件包（含禁用词检查） |
| `tools/check_classes.py` | 查前端用到的 Tailwind 类在 CSS 产物里真的存在（防静默失效） |
| `demo.py` | 命令行里跑一轮，不启服务 |

### A.3 改代码后最小验证流程

```bash
# 1. 语法 / 导入
python -c "import app, channels.plugin_qq"

# 2. 全量测试（约 35 秒）
PYTHONPATH=. python -m unittest discover -s tests -t .

# 3. 前端（改过 web/ 才需要）
node --check web/assets/app.js
python tools/check_classes.py

# 4. 端到端（不花钱）
python tools/fake_napcat.py --port 3001 --group 100 --self 999 --user 42 &
QQ_ENABLED=1 QQ_ONEBOT_WS=ws://127.0.0.1:3001 QQ_SELF_ID=999 QQ_GROUP_IDS=100 \
QQ_OWNER_IDS=42 SCCORE_DB=/tmp/smoke.db SCCORE_MEDIA=/tmp/smoke-media \
python serve.py --fake --port 8100
```

---

## 附录 B 命令速查

### B.1 网页 / 内核

```bash
curl -s 127.0.0.1:8100/api/roster | python3 -m json.tool     # 角色列表（healthcheck 也用它）
curl -s 127.0.0.1:8100/api/state?c=fuyuko | python3 -m json.tool
curl -s "127.0.0.1:8100/api/affinity?c=fuyuko&u=<user>&s=dm"
curl -s "127.0.0.1:8100/api/stories?c=fuyuko&q=单独公演"       # 话题能不能命中
curl -s -X POST 127.0.0.1:8100/api/turns -d '{"text":"在吗"}' -H 'Content-Type: application/json'
```

### B.2 QQ 通道

```bash
curl -s 127.0.0.1:8100/api/qq/status | python3 -m json.tool
curl -s -X POST 127.0.0.1:8100/api/qq/reload                  # 改完人设立刻生效
curl -s -X POST 127.0.0.1:8100/api/qq/ping -d '{"mode":"dm"}'      # 让她先开口一次（测试）
curl -s -X POST 127.0.0.1:8100/api/qq/ping -d '{"mode":"group"}'
curl -s -X POST 127.0.0.1:8100/api/qq/sticker -d '{}'         # 发一张表情（收藏优先）
curl -s -X POST 127.0.0.1:8100/api/qq/sticker -d '{"tag":"无语"}'
curl -s -X POST 127.0.0.1:8100/api/qq/sticker/tag \
     -d '{"sha1":"<sha1>","tag":"无语"}' -H 'Content-Type: application/json'
```

### B.3 空间

```bash
curl -s 127.0.0.1:8100/api/qzone                              # 状态
curl -s -X POST 127.0.0.1:8100/api/qzone -d '{"publish": false}'   # 只写不发（预演）
curl -s -X POST 127.0.0.1:8100/api/qzone -d '{"text":"…"}'         # 指定内容发一条
curl -s -X POST 127.0.0.1:8100/api/qzone/delete -d '{"tid":"…"}'   # 撤回
```

### B.4 数据库（容器内）

```bash
docker exec scbot python -c "
import sqlite3
d = sqlite3.connect('/data/scbot.db')
for r in d.execute('select key, value from qq_flags order by key'): print(r)
"

docker exec scbot python -c "
import sqlite3
d = sqlite3.connect('/data/scbot.db')
for r in d.execute('select tag, count(*) from qq_stickers group by tag order by 2 desc'): print(r)
"

docker exec scbot python -c "
import sqlite3
d = sqlite3.connect('/data/scbot.db')
for r in d.execute('select qq_id, turns, muted, loving from qq_identities order by turns desc limit 10'): print(r)
"
```

### B.5 群里能用的指令

```
/帮助  /重开  /好感  /阶段  /阶段 重置  /阶段 全员重置  /重载  /静音  /解除
/表情 [标签]  /表情 同步  /表情标 标签
```

---

*本文与代码同版本维护。改了 §1.4 的模块、§2.6 的表、或任何一个 `.env` 变量，
记得回来改这张文档——否则下一个接手的人会相信一份过期的地图。*

