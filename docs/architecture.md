# 架构

一句话：**内核只认识「一个回合」，其余都是插件。**

这句话不是口号，它是可检验的：把 `plugins/` 整个删掉，`core/` 仍然能跑一个回合（`demo.py` 就是），只是没有好感度、没有记忆、没有礼物。反过来，插件不能要求内核为它加一个钩子——它们能用的是 `before_turn` / `prompt_sections` / `judge_questions` / `after_turn` / `routes` 这五个口子。

## 一次请求走过哪里

```
浏览器
  │  POST /api/turns?c=mano   {"text": "……"}
  ▼
serve.py            标准库 ThreadingHTTPServer；只做三件事：怎么回浏览器、页面文件在哪、插件的路由
  ▼
app.py   App.turn   找出这位角色、这段对话；把图交出去；把这一轮交给 engine
  ▼
core/turn.py        回合的四个阶段
  ├─ before    字句检查（空/超长），然后问各插件门禁；任何插件说不，就是一轮拒绝
  ├─ build     判官一次请求答完所有插件的问题 → 各插件写提示词片段 → 四槽装配
  ├─ stream    模型吐字 → 协议解析 → 段落事件
  └─ after     落库（两条消息，一起成功）→ 各插件旁观（改好感度、写记忆）
  ▼
SSE    data: {"type":"delta",…} … {"type":"done","id":…}
```

### 提示词的四个槽

`core/prompt.py` 把提示词分成四块，**槽位就是缓存决定**：

| 槽 | 内容 | 缓存 |
|---|---|---|
| `persona` | 她的人设 + 回复格式说明 | 缓存（每位角色一份，所有人共用） |
| `profile` | 制作人是谁 | 缓存（每位制作者一份） |
| `history` | 存下来的那些回合 | 缓存（每轮只追加） |
| `turn` | 这一轮才成立的东西（她此刻的态度、找到的记忆、图片说明） | **不缓存** |

插件只能往 `turn` 和 `profile` 写，往别处写会直接抛错。这样一条插件就没法悄悄把每轮的提示词前缀改掉、让缓存全部失效、把账单翻三倍。

### 判官

好感度要问「这句话伤到她了吗」，记忆要问「这值得记住吗」。如果一个插件一次请求，三个插件就是三次；所以内核自己拿一个判官（`core/judge.py`），把所有人提的问题拼进**一次**请求，答案按 key 分回去。插件拿到 `turn.facts["affinity.level"]`，拿不到就当作没有——判官挂了不能拖垮这一轮，也不能让插件瞎猜。

判官只看制作人说的话，不看她的回复：**回复本身不是证据**。

## 插件

```python
class MyThing(Plugin):
    name = "mything"
    requires = ("affinity",)          # 排在谁后面；缺了就一起关掉

    def capabilities(self): ...       # 前端能显示什么
    def migrations(self): ...         # CREATE TABLE IF NOT EXISTS / ALTER TABLE
    def routes(self): ...             # 自己的 HTTP 接口
    def judge_questions(self): ...    # 要判官答的问题
    async def before_turn(self, turn): ...   # 返回 Veto 就是拒绝这一轮
    async def prompt_sections(self, turn): ...  # 往提示词里加东西
    async def after_turn(self, turn): ...    # 回复已落库之后
```

三条规矩：

1. **一个插件挂了，不是服务挂了。** 钩子抛异常只会把它自己记成 `failed` 事件，这一轮照常走完。角色因为好感度计数器坏了而不说话，比计数器不计数糟糕得多。
2. **顺序是依赖顺序，不是注册顺序。** `requires` 是「我读它的表/它写的事实」的承诺，宿主按它排序；成环是启动错误，不是第 40 轮才发现。
3. **插件只通过 `turn.facts` 说话。** 两个插件写同一个 key 是 bug，不是合并。

内置插件：

| 插件 | 门禁 | 提示词 | 旁观 | 路由 |
|---|---|---|---|---|
| `characters` | | | | 角色目录、头像 |
| `recall` | | 角色的资料（整份进 `persona`，或按语义取几段进 `turn`） | 建索引、记嵌入成败 | 段数与嵌入数 |
| `quota` | 每小时轮数 | | 成功才结账 | |
| `photos` | 每小时张数 | 图片说明 | 记账 | 上传 |
| `affinity` | 冷却中不接话 | 此刻的态度与感受 | 改数值、发变化事件 | 档位 |
| `memory` | | 她还记得的事 | 后台抽取新记忆 | 列表/删除/清空 |
| `gifts` | | | | 抽奖、背包、送礼、重置卡 |
| `profile` | | 档案进 `profile` 槽 | | 读/写 |
| `status` | | | 记模型成败 | 服务状态 |
| `voice` | | | | TTS（有 key 才开） |

## 存储

一份 SQLite，一个连接，一把锁。三张表是内核的（`users` / `conversations` / `messages`），其余的表属于插件，插件自己给出建表语句，启动时按依赖顺序执行。

没有迁移框架：建表用 `CREATE TABLE IF NOT EXISTS`，加列用 `ALTER TABLE ADD COLUMN`，**重复加列被当作正常**（老库没有就加上，新库已经有了就跳过）。这不是优雅，是不想让一次升级变成一次运维。

回复**存原样，读出来时才剥语气提示**（`core/store.py` 的 `_message()`）。这样语音合成还能听见「[softly]」，而页面上永远不显示它。

## 前端

`web/` 是一份手写的单页，没有框架也没有构建步骤：

- 样式直接用原版编译出来的 Tailwind 产物（`web/assets/css/app.css`），class 名照抄原版，所以视觉天然一致。
- 图标是从上游用的图标库抓下来的 SVG 路径，逐字节一致。
- 路由是 `history.pushState` + 自己画：`/`、`/conversations`、`/conversation/<id>`、`/settings/<类>`、`/gifts`。
- 一个坑：Tailwind v4 的产物**只含源码里用过的类**，自己新写的类不会存在，写进 class 也不报错、只是没样式。所以仓库里有 `tools/check_classes.py`，`tests/test_frontend.py` 会跑它。

## 为什么只用标准库

上游是 FastAPI + Next.js + Docker Compose。这一版想回答的问题是「这套玩法最少需要什么」，答案是一台 1 核 1G 的小机器加上 `python3 serve.py`：没有 `pip install`，没有构建，升级就是换一个目录、重启一次。

代价写在 [parity.md](parity.md) 里：WebRTC 语音通话、剧情库与重排、多用户账号这些要么需要依赖，要么需要后台，这一版没有移植。**向量检索是唯一的例外**：角色的资料可以选择向量版（`SCCORE_RECALL=vector`），走的是 OpenAI 兼容的 `/embeddings`，一行 `urllib` 就够，不用装包；不选它、或者那家服务不通，整份资料直接进提示词，对话照样进行。
