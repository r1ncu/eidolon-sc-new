# scbot-new

一个角色扮演对话内核：网页里聊，QQ 群里也聊。角色是数据，玩法是插件。

**这个仓库里没有任何角色。** 它是为一个具体角色写的，后来把「角色是谁」整个拆了出去：
人设、头像、剧情资料、组合表都在 `characters/`，由部署者自己准备——格式和示例见
[characters/README.md](characters/README.md)。不提供、也不附带任何他人的角色设定、
立绘或剧情文本。

技术上是 [Eidolon-SC](https://github.com/Sallyn0225/eidolon-sc)（AGPL-3.0）的**内核重构版**：把原来一体的后端拆成「一个只认识回合的最小内核 + 一组各自干一件事的插件」，把 Next.js 前端换成一份手写的单页，用标准库跑起来。目标不是做一个更小的复刻，而是把「什么属于内核、什么只是一个玩法」这条线画出来。因为是衍生作品，本仓库同样以 AGPL-3.0 发布（见 `LICENSE`）。

- **零第三方依赖**：只用 Python 标准库，`python3 serve.py` 就能跑。
- **角色是数据**：人设、头像、颜色都在 `characters/` 里，改配置不用改代码——加一个角色就是加一行。
- **资料可整份、可向量**：`characters/facts/<id>.md` 可以整份贴进人设（数据版，不用任何服务），也可以切段嵌入后按语义取几段（向量版，一个 OpenAI 兼容的 `/embeddings` 就够），一个环境变量切换。
- **玩法是插件**：好感度、记忆、礼物、限额、语音各自一个文件，关掉任何一个，对话照旧。
- **单用户、本地、自己的 key**：一份 SQLite，一个目录，没有账号体系也没有管理后台。

## 关于文中的示例

下文的路径和命令里会出现 `fuyuko` 这样的角色 id——那是**示例**，换成你自己的
`QQ_CHARACTER` 就行。仓库里没有这个角色，也没有她的任何资料。

## 跑起来

```bash
cp .env.example .env          # 填三个 CHAT_LLM_*（任何 OpenAI 兼容服务）
python3 serve.py              # → http://127.0.0.1:8100
```

没有 key 也能把界面看一遍，回复是固定台词，不发出任何请求：

```bash
python3 serve.py --fake
```

要求 Python 3.11+（Debian 12 自带的就是 3.11），不需要装任何包。

## 住在一个 QQ 群里（1.2.0）

同一个内核还能当一个群里的冬优子：她只待在你指定的那一个群，只回应**被 @** 的那一句，不私聊。
距离的分配在数据文件里，不写死：`characters/sheets/fuyuko.md` 只留公开场合那一层（有外人在、镜头前、群里），
独处时按场次走 `characters/world/fuyuko-dm.md`。人设排在场景之后，所以**独处的许可只能写在场景文件里**，写进人设会被它后面的硬性规则压回去——这是「私聊里她还是抗拒」的真正原因。

**好感度按场合分开记，一共三条。** 网页一条（`affinity` 表），QQ 群一条、私聊一条（`affinity_scenes` 表，`surface` = `group` / `dm`）。同一个角色、同一个人，三个数字互不影响：群里那五十个人怎么闹，不会压低你私聊里的关系，反过来也一样。
老库升级时，网页那条旧值会被复制成**私聊**的起点——那是这个数字一直在描述的关系；群聊从「信任」重新算。`QQ_OWNER_AFFINITY`、恋爱的两道门和恋爱后的下限，都只作用在私聊那一条上。

**恋爱是一个状态，不是一个档位。** 好感度 ≥ `QQ_OWNER_LOVE`（95）**且**私聊说过 `QQ_OWNER_LOVE_TURNS`（20）次话，私聊里就多一段【恋爱】——写在 `fuyuko-dm.md` 里，只对私聊生效，群里永远不会看到。
进去之后，下限从 `QQ_OWNER_AFFINITY`（100）降到 `QQ_OWNER_LOVE_FLOOR`（90）：小的波动是「她今天有点不高兴」，真掉下去（特别伤到她）会保留 6 小时再抬回来。而她会不定时、不定次地先发消息过来（`QQ_OWNER_LOVE_PING`）。

**她的长期记忆是一份 .md，不是聊天记录。** `characters/memory/dm-<QQ号>.md` 每 3 天重写一次，
`group-<群号>.md` 每周一次。每次重写都把上一版整份交给模型，让它决定留什么、改什么、删什么，
然后**覆盖同一个文件**——所以那是记忆，不是日记：它会变短，会忘掉不重要的事，也会被新的事实改写。
它写在 `characters/` 底下，是为了你能直接打开看、直接编辑（改完下一轮就生效，不用重启），
而它同时也进提示词（`QQ_DIGEST_PROMPT`），所以她真的会想起来。

**群里她只发中文字幕**（日语原文只喂给语音），所以语域必须在中文里也看得出来：日语靠「です・ます」，
中文靠句子的形状（「……可以吗」「……不太喜欢」），四档各配了中文样本。
**群里的人都是制作人**——玩这个企划、看 LIVE 的人，没有路人——所以她照每个人都叫「プロデューサーさん」，
但群里有别人在，她永远是「ふゆ」（です・ます、♡）；只有你自己那一号多一分挖苦和括号里的真心话。
他们比你更懂企划，所以她不用解释设定；卡池、曲子、LIVE 接得上就接，不知道就说不知道。
她记得「是谁在叫她」——每个群友各自的身份、好感、记忆——但不记群聊流水，不知道就直接说不知道。

```bash
cp .env.example .env          # 填 CHAT_LLM_*、QQ_SELF_ID、QQ_GROUP_IDS、QQ_OWNER_IDS
docker compose up -d --build  # 内核 + NapCat，两个容器
```

不限 QQ 先看效果（不需要 QQ、不需要 key）：

```bash
python3 tools/fake_napcat.py --port 3001 &          # 假的 NapCat
QQ_ENABLED=1 QQ_ONEBOT_WS=ws://127.0.0.1:3001 \
QQ_SELF_ID=999 QQ_GROUP_IDS=100 python3 serve.py --fake
```

部署、扫码、排查、备份都在 [DEPLOY.md](DEPLOY.md)。

## 它长什么样

左边是对话列表（她的头像、最后一句、相对时间、搜索、筛选、置顶），点进去是她的对话页：她的壁纸、白气泡、深蓝墨水气泡、右上角的好感度档位、输入栏旁边的图片上传。设置分九类：档案、记忆、统计、通话、外观、自带密钥、登录与数据、数据包、关于；礼物页是抽奖台 + 我的礼物 + 送礼面板。

界面的 DOM 与 class 是从原版逐页复刻的（连 Tailwind 的产物 CSS 一起），所以颜色、字号、圆角、间距和原版一致；脚本是自己写的一份，没有 React。

## 目录

```
app.py              装配：一个 store、一个 host、每位角色一个 engine
serve.py            标准库 HTTP + SSE；插件路由在这里并进服务
demo.py             不启服务、只在终端里跑一轮

core/               内核：只认识「一个回合」
  protocol/         回复格式（<ja>/<zh>）、语气提示的剥与留
  llm.py            OpenAI 兼容客户端 + 假模型
  prompt.py         四个槽位，槽位即缓存边界
  store.py          users / conversations / messages
  turn.py           回合：门禁 → 判断 → 拼提示词 → 流式 → 落库 → 旁观
  host.py           插件宿主：依赖排序、失败 fail-open、路由合并
  judge.py          判断总线：N 个问题合成一次请求
  settings.py       环境变量

plugins/            每个文件一件事
  characters.py     角色目录、头像、组合
  recall.py         角色的资料：整份贴入（数据版）/ 切段向量检索（向量版）
  quota.py          每小时轮数（滑动窗口）
  photos.py         发图与每小时张数
  affinity.py       好感度：判官打分、档位、冷却、礼物加成
  memory.py         长期记忆：跨角色共享、转述、被取代
  gifts.py          抽奖、背包、送礼、重置卡
  profile.py        制作人档案
  status.py         服务状态（模型这一轮成没成）
  voice.py          Fish Audio 语音合成

web/                单页前端（index.html + assets/）
characters/         角色配置与人设；facts/ 是角色资料（可选）；gifts.toml 是礼物目录
  world/            QQ 群用的知识层：office.md（28 人名册）、idols/（按需小传）、
                    meta.md（作品知识）、fuyuko-qq.md（群聊语域）、
                    fuyuko-dm.md（私聊）、fuyuko-stage.toml / fuyuko-refusals.toml（措辞）
docs/               架构、插件写法、与原版的对照、部署
deploy/             systemd 单元
tests/              425 条单元测试，不打网络、不需要 key

channels/           QQ 群通道（可选插件：这个目录在就在，删掉就是一个网页端）
  ws.py             标准库写的 WebSocket 客户端（握手 + 帧）
  onebot.py         OneBot 11：事件、动作、echo 配对、断线重连
  qq.py             一条 @ → 一轮对话 → 若干条消息
  identities.py     QQ 号 ↔ 内核 user；去重、说过几次、恋爱标志
  presence.py       每轮的语域段（对面是谁、能接多少）；措辞来自 `-stage.toml`
  world.py          三层知识的装配与检索（按名字，不按语义）
  outbox.py         分段、切句、限速、重启续发
  texts.py          内部原因 → 她的台词（来自 `-refusals.toml`）
  stickers.py       表情包：收录、命名、挑选
  digest.py         把聊天重写成记忆文件（整份重写）
  stage.py          读 `characters/world/<id>-stage.toml` 的小工具
  interests.py      没人叫她时，判断有没有她想接的话、接哪一句（依据 `-interests.md`）
  config.py         所有 `QQ_*` 环境变量
  plugin_qq.py      通道的生命周期（线程 + 事件循环 + /api/qq/*）
```

`channels/` 是这一层里**唯一可以整个拿走**的部分：它不 import 内核的任何私有东西，
内核也不知道它存在（`app.py` 里是一次可选导入，目录不在就跳过）。想把它装到另一个
内核上、或者换一个角色重新写一遍人设，看 `docs/qq-channel/`（六篇教学 + 模板 + 自检脚本）。

## 改规则不用重启

人设和规则都是磁盘上的文本，它们在每轮对话开始时被检查一次：改了文件，下一轮就照新的说。

| 文件 | 生效时机 |
|---|---|
| `characters/world/fuyuko-qq.md` / `fuyuko-dm.md` | 下一轮（群聊／私聊各一份） |
| `characters/world/fuyuko-stage.toml`（称呼与兜底措辞）／`fuyuko-refusals.toml`（拒绝台词） | 下一轮 |
| `characters/world/office.md`（人名册）、`meta.md`（作品知识） | 下一轮 |
| `characters/world/idols/<id>.md` | 下一次提到这个人时 |
| `characters/sheets/<id>.md`、`characters/facts/<id>.md`、`characters.toml` | 下一轮（会重建提示词缓存） |
| `.env`、`docker-compose.yml`、代码 | 仍然要重启容器 |

三个细节值得知道：

- **要等文件安静 0.35 秒。** 编辑器边打字边写盘，读到一半会出半截规则；所以改动在落地后下一次检查才生效。
- **读到空文件不生效。** 保留上一版——`>` 重定向或编辑器「清空再写」的瞬间不会让她忽然没有人设。
- **改了但想立刻看到效果**：`/重载`（只有制作人）或 `curl -X POST http://127.0.0.1:8100/api/qq/reload`。它丢掉所有缓存的提示词，下一轮按磁盘重建。

`/api/qq/status` 里的 `generation` 会随每次重读 +1，可以用它确认改动真的被吃进去了。

## 关系是有档位的

她对一个人说话的语气，不是「熟 / 不熟」两档，而是按**她和这个人说过多少次话**往上走的一把梯子。
次数就是 `qq_identities.turns`：她每回答一个人一次，那个人的数就 +1（一次回答分成几条气泡也只算一次）。

档位写在 `characters/world/fuyuko-qq.md` 末尾，每一档一行头、下面一段文案，`｜` 后面是「从第几次开始」：

```
【阶段 new｜0】      刚认识——营业最浓的一档，句尾软，♡ 和「ふゆ♡」在这里用
【阶段 familiar｜3】  聊过几次——敬语还在，甜味收了
【阶段 close｜10】    熟人——句子短、直，开始挖苦，不再写「ふゆ♡」
【阶段 inner｜30】    自己人——平时的节奏漏出来，偶尔一句私下的口气
```

改门槛或改文案都算改数据，保存后下一轮就生效（不用重启）；`/api/qq/status` 的 `ladder` 会显示
当前真正在用的四档和字数。

几条要知道的：

- **档位文案走人设槽，不是提示词末尾。** 实测过：只放在提示词末尾时，四档说出来的话几乎一模一样；
  放到人设最前面、排在 sheet 之前，语气才真的分层。每个（场景 × 档位）各有一份缓存，互不污染。
- **制作人有下限**（`QQ_OWNER_STAGE`，默认 `close`）：他不是群里认识的陌生人，不该从「初次见面」开始。
  想在自己的号上看「初次」那一档，把它设成空、`/阶段 重置`，然后说句话。
- **群是群，档位是档位。** 就算到 `inner`，那也是「对一个熟人的放松」，不是把私下的她整个搬进群里——
  旁边还有几十个人在看，这条由【场景】段压着。
- **加了新好友不会拉低别人。** 每个人各算各的；插话（没人叫她时自己接一句）不计入，
  因为那不是「和他说话」。

`/阶段` 看自己现在几回，`/阶段 重置` 归零，`/阶段 全员重置` 把除制作人以外全群归零（只有制作人）。

## 指令只有制作人能用（`/表情` 除外）

`/帮助`、`/好感`、`/阶段`、`/重载`、`/静音`、`/解除`、`/重开` —— 这些是控制面板，不是群里的公共菜单。
默认（`QQ_COMMANDS=owner`）只有 `QQ_OWNER_IDS` 里的人发才有反应；**其他人发了什么都不回**，
连「你没有权限」都不回——回那句话等于告诉她这里有个指令系统，下一个人就会接着试。
（想放开成谁都能用，把 `QQ_COMMANDS` 设成 `all`。）

`/表情` 是唯一的例外：它没有副作用，只是从表情库里取一张——库里是群友发过的表情，加上这个号自己收藏的（启动后自动拉一次、之后每天一次，`/表情 同步` 可以手动再来一遍），锁着只会让全群改求制作人代打字。
群友用这条有 15 秒冷却，`QQ_STICKERS_PUBLIC=0` 可以关。

`/静音` 与 `/解除` 是一对：静音判定在指令之前让路，所以静音之后仍然发得出 `/解除`。

完整的一张表（每条指令的回复原文、权限、已知缺口）在 [`COMMANDS.md`](COMMANDS.md)。

## 配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `CHAT_LLM_BASE_URL` / `CHAT_LLM_API_KEY` / `CHAT_LLM_MODEL` | 必填 | 对话模型，任何 OpenAI 兼容的 `/chat/completions` |
| `SCCORE_HOST` / `SCCORE_PORT` | `0.0.0.0` / `8100` | 监听地址 |
| `SCCORE_DB` | `scbot.db` | SQLite 库 |
| `SCCORE_MEDIA` | `media` | 图片目录 |
| `SCCORE_CHARACTERS` / `SCCORE_CHARACTERS_CONFIG` | `characters` / `characters.toml` | 角色目录与配置文件 |
| `SCCORE_TURNS_PER_HOUR` | `60` | 每小时轮数，`0` 不限 |
| `SCCORE_PHOTOS_PER_HOUR` | `8` | 每小时发图张数 |
| `SCCORE_HISTORY_TURNS` | `20` | 带多少轮历史，`0` 全带 |
| `SCCORE_JUDGE_MODEL` | 空 | 判官模型；留空用对话模型 |
| `SCCORE_JUDGE_BASE_URL` / `SCCORE_JUDGE_API_KEY` | 空 | 判官另用一家服务时填（如 OpenRouter 上的 JEV）；留空用对话模型那家 |
| `SCCORE_RECALL` | `off` | 角色资料的用法：`off` 只有人设、`whole` 整份贴入（数据版）、`vector` 切段向量检索（向量版） |
| `SCCORE_RECALL_TOP` / `SCCORE_RECALL_MIN` | `4` / `0.35` | 向量版：每轮带几段、余弦下限 |
| `SCCORE_RECALL_GATE` | `off` | `judge`：先让判断模型决定这一轮要不要查资料（上游用 JEV） |
| `SCCORE_EMBEDDING_BASE_URL` / `_API_KEY` / `_MODEL` | 空 / 空 / `Qwen/Qwen3-Embedding-0.6B` | 向量版用的嵌入服务（OpenAI 兼容的 `/embeddings`） |
| `SCCORE_TIMEZONE` | `Asia/Shanghai` | 记忆里的日期用哪个时区 |
| `FISH_AUDIO_API_KEY` | 空 | 语音；留空语音插件自动关 |
| `SCCORE_FAKE` | 空 | `1` 等于 `--fake` |
| `QQ_ENABLED` | `0` | 群通道总开关；开之前必须有下面四项 |
| `QQ_ONEBOT_WS` | `ws://127.0.0.1:3001` | NapCat 的正向 WebSocket |
| `QQ_ONEBOT_TOKEN` | 空 | 与 NapCat 里设的 OneBot 令牌一致 |
| `QQ_SELF_ID` | 空 | 机器人自己的号（过滤自发消息、判断 @） |
| `QQ_GROUP_IDS` | 空 | 只待这一个群；空则不响应任何群 |
| `QQ_OWNER_IDS` | 空 | 谁是「制作人」，决定称呼与分寸 |
| `QQ_PRIVATE` | `0` | 私聊开关。开了以后名单里的人可以一对一找她，那里没有外人，她用平时的语气；两个人是彼此喜欢的成年人，亲近到吻为止，性的事她仍然拒绝。进了恋爱她会先发消息，语气也换成「在一起」的那种 |
| `QQ_PRIVATE_IDS` | 空 | 谁能私聊；留空沿用 `QQ_OWNER_IDS` |
| `QQ_CHARACTER` | `fuyuko` | 群里是谁 |
| `QQ_MERGE_SECONDS` / `QQ_MEMBER_COOLDOWN` | `1.2` / `20` | 连发合并窗口 / 同一人冷却（秒） |
| `QQ_GROUP_CONCURRENCY` | `2` | 同一个群里同时最多跑几轮 |
| `QQ_AMBIENT` / `QQ_AMBIENT_P` | `0` / `0.05` | 没人 @ 时插不插话 / 每条消息掷一次硬币的概率 |
| `QQ_AMBIENT_GAP` / `QQ_AMBIENT_HEARD` | `300` / `8` | 两次插话的最小间隔（秒）/ 给她看的最近几行 |
| `QQ_AMBIENT_STICKER_P` | `0.3` | 插话时只用一张表情、不说话的比例 |
| `QQ_AMBIENT_OWN_P` | `0.35` | 插话里有多少比例**不是在接话**，而是说一句自己的事（没人叫她、也不回答谁） |
| `QQ_AMBIENT_IDLE` | `0` | 群里安静多少分钟后她可能自己开口（分钟，`0` 关）；只在 9:00–23:00，且三重复核 |
| `QQ_STICKERS` / `QQ_MONDAY_HOUR` | `1` / `9` | 表情库总开关（群友发的 + 号里收藏的）/ 周一几点发「上班」那张（-1 关） |
| `QQ_STICKERS_PUBLIC` | `1` | 让群友也能发 `/表情`（15 秒一次）；`0` 收回成只有制作人 |
| `QQ_DIGEST` | `1` | 把说过的话写成记忆文件（`characters/memory/*.md`），每次重写整份，旧的被新的盖掉 |
| `QQ_DIGEST_DM_DAYS` / `QQ_DIGEST_GROUP_DAYS` | `3` / `7` | 私聊每几天重写一次 / 一个群每几天重写一次 |
| `QQ_DIGEST_PROMPT` | `1` | 写好的记忆进不进提示词。`0` 就只存档给你看，她不会想起来 |
| `QQ_OWNER_STAGE` | `close` | 制作人说话档位的**下限**（档位名见 `fuyuko-qq.md`）；设空则和普通人一样从第一次算，设 `inner` 就是一直「自己人」 |
| `QQ_OWNER_AFFINITY` | `0` | 制作人**好感度**的下限（0–100，0 不管）。判官读到「他连着几次提同一个要求」会扣分，掉到「疏远」她就用对失望的人的语气说话；设 `100` 就是一直满好感，只抬制作人 |
| `QQ_OWNER_LOVE` / `QQ_OWNER_LOVE_TURNS` | `0` / `20` | 私聊进入**恋爱**的两道门：好感度 ≥ 95（比「亲密」高）**且**说过 20 次话。进了就记住，之后掉分也不退出；`0` 关闭 |
| `QQ_OWNER_LOVE_FLOOR` | `90` | 恋爱后的好感度下限。恋爱前是 `QQ_OWNER_AFFINITY`；恋爱后降到 90，允许「今天有点不高兴」但不掉出恋爱；真掉下去（特别伤到她）保留 6 小时再抬回 |
| `QQ_OWNER_LOVE_PING` / `_MIN` / `_MAX` | `0` / `90` / `240` | 恋爱后她**先发消息**给她自己：间隔随机（分钟），只在 9:00–23:00，只在私聊 |
| `QQ_COMMANDS` | `owner` | 指令权限。`owner` 只有 `QQ_OWNER_IDS` 能用，别人发 `/帮助` 之类**完全没有反应**（`/表情` 除外）；`all` 谁都能用 |
| `QQ_WORLD` / `QQ_META` / `QQ_BRACKET` | `1` | 人名册 / 作品知识 / 对制作人的括号本性 |

## 接口

页面要用的都在这里；插件可以自己再挂路由，内核不知道它们是什么意思。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/`、`/conversations`、`/conversation/<id>`、`/settings`、`/gifts` | 页面（单页，二级路由由前端自己画） |
| GET | `/api/state?c=<角色>` | 角色、当前对话、能力清单、好感度、记忆、消息 |
| GET | `/api/roster` | 列表要的一行一位：最后一句、时间 |
| GET | `/api/conversations?c=` / POST 同路径 | 历史对话 / 新对话 |
| POST | `/api/turns?c=&conv=` | `{"text": "…", "photos": [...], "own_keys": {...}}`，SSE |
| GET | `/api/memory`、`/api/memory/all` | 记忆（后者给设置页，带「已被取代」） |
| GET | `/api/affinity?c=&u=` | 好感度档位（不给数字） |
| GET | `/api/gifts/state`、`/api/gifts/catalog` | 抽奖次数、背包、重置卡 / 礼物目录 |
| POST | `/api/gifts/draws`、`/api/gifts/give`、`/api/gifts/reset-cards` | 抽奖、送礼、用重置卡 |
| GET | `/api/stats?range=30d\|1y\|all` | 统计 |
| GET/POST | `/api/qq/reload` | 重新读取人设与规则文件，返回丢了几个缓存（不用重启） |
| GET | `/api/qq/status` | 群通道：连没连上、群里几个人、发了几条、收了几张表情、上一次错在哪（`QQ_ENABLED` 才挂） |
| GET | `/api/export`、POST `/api/import` | 数据包 |
| GET | `/api/service-status` | 模型最近的成败 |
| GET | `/api/recall?c=<角色>` | 资料切了几段、已嵌入几段、用的哪个嵌入模型 |

## 测试

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -q
```

425 条，全部走假模型、假判官与假嵌入服务：协议解析、提示词装配、回合、插件宿主、每个插件（含资料的两种版本）、HTTP 与前端 CSS 类，以及 QQ 通道（WebSocket 握手与帧、触发与去重、分段、群聊与私聊两套语域、知识检索、私聊出站与续发、随机插话与间隔、自己开口与安静时段、表情收录与收藏同步与命名（含「收藏的那张在群里发一次就能被标」）、指令权限、档位与好感度重置、恋爱门槛与下限、主动私聊、群聊与私聊的好感度隔离、记忆文件的整份重写与两种节奏、记忆只进各自场合的提示词、周一定时）。

## 与上游的关系

本仓库是 [Eidolon-SC](https://github.com/Sallyn0225/eidolon-sc) 的衍生作品，**同样以 AGPL-3.0 发布**（见 `LICENSE`）。数值、提示词与语义尽量照抄上游，包括那些反直觉的地方（存的是带语气提示的原文、行首空格会被吃掉、被取代的记忆删掉新的就恢复）。**哪些对上了、哪些故意不一样、哪些没移植**，逐条写在 [docs/parity.md](docs/parity.md)。

角色人设、头像与礼物插画来自上游仓库，版权属于原作者与相关权利方；本项目是粉丝作品，与万代南梦宫、Bandai Namco 无关。上游的**角色资料**（`characters/facts/`，由剧情与设定整理）没有可再分发的许可证，仓库里只留一份格式说明，要用请自己准备（见 [characters/facts/README.md](characters/facts/README.md)）。

## 文档

- [docs/architecture.md](docs/architecture.md) —— **架构设计与部署教学**：模块职责、每项功能的实现方式、
  数据流、运行环境、从零部署、配置全表、排错手册。要接手或重新部署，先读这份
- [docs/qq-channel/README.md](docs/qq-channel/README.md) —— 把 `channels/` 当独立插件包分发时的说明
  （安装、配置、NapCat、自定义角色、排错）
- [DEPLOY.md](DEPLOY.md) —— 最短路径的 QQ 单群部署（Docker、PVE、登录、备份）
- [COMMANDS.md](COMMANDS.md) —— 指令表与回复格式
