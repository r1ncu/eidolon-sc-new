# eidolon-sc-new


这是 [Eidolon-SC](https://github.com/Sallyn0225/eidolon-sc)（AGPL-3.0）的**内核重构版**：把原来一体的后端拆成「一个只认识回合的最小内核 + 一组各自干一件事的插件」，把 Next.js 前端换成一份手写的单页，用标准库跑起来。目标不是做一个更小的复刻，而是把「什么属于内核、什么只是一个玩法」这条线画出来。本地部署后如果自己有不同的想法即可以插件的形式添加进来，不用再去动内核。

**纯Vibe coding产物，故如有各种代码编程上的问题，我无法解决。除了BUG以外，本项目不会再维护**

在原仓库功能的基础上新增了一个识图功能，需要自行添加API（可以和LLM共用同一个API KEY）

_偷偷提一嘴，因为角色和角色数据也进行了插件化，你甚至可以制作本家、柏油、灰灰、赛德曼、初星中专等等的版本，甚至是大杂烩……_



- **零第三方依赖**：只用 Python 标准库，`python3 serve.py` 就能跑。
- **角色是数据**：28 位角色的人设、头像、颜色都在 `characters/` 里，改配置不用改代码。
- **资料可整份、可向量**：`characters/facts/<id>.md` 可以整份贴进人设（数据版，不用任何服务），也可以切段嵌入后按语义取几段（向量版，一个 OpenAI 兼容的 `/embeddings` 就够），一个环境变量切换。
- **玩法是插件**：好感度、记忆、礼物、限额、语音各自一个文件，关掉任何一个，对话照旧。
- **单用户、本地、自己的 key**：一份 SQLite，一个目录，没有账号体系也没有管理后台。

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
docs/               架构、插件写法、与原版的对照、部署
deploy/             systemd 单元
tests/              199 条单元测试，不打网络、不需要 key
```

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
| GET | `/api/export`、POST `/api/import` | 数据包 |
| GET | `/api/service-status` | 模型最近的成败 |
| GET | `/api/recall?c=<角色>` | 资料切了几段、已嵌入几段、用的哪个嵌入模型 |

## 测试

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -q
```

199 条，全部走假模型、假判官与假嵌入服务：协议解析、提示词装配、回合、插件宿主、每个插件（含资料的两种版本）、HTTP 与前端 CSS 类都在里面。

## 与上游的关系

本仓库是 [Eidolon-SC](https://github.com/Sallyn0225/eidolon-sc) 的衍生作品，**同样以 AGPL-3.0 发布**（见 `LICENSE`）。数值、提示词与语义尽量照抄上游，包括那些反直觉的地方（存的是带语气提示的原文、行首空格会被吃掉、被取代的记忆删掉新的就恢复）。**哪些对上了、哪些故意不一样、哪些没移植**，逐条写在 [docs/parity.md](docs/parity.md)。

角色人设、头像与礼物插画来自上游仓库，版权属于原作者与相关权利方；本项目是粉丝作品，与万代南梦宫、Bandai Namco 无关。上游的**角色资料**（`characters/facts/`，由剧情与设定整理）没有可再分发的许可证，仓库里只留一份格式说明，要用请自己准备（见 [characters/facts/README.md](characters/facts/README.md)）。

## 文档

- [docs/architecture.md](docs/architecture.md) —— 一次请求走过哪几层，插件能做什么、不能做什么
- [docs/plugins.md](docs/plugins.md) —— 自己写一个插件
- [docs/parity.md](docs/parity.md) —— 与原版逐项对照
- [docs/deploy.md](docs/deploy.md) —— 部署、备份、换模型
