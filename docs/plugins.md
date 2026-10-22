# 写一个插件

插件是一件事，放在 `plugins/` 的一个文件里。它不需要改内核、不需要改 `serve.py`，也不需要知道别的插件存在。

下面这个例子（没有放进仓库，你可以照着写）是「亲密度日记」：每轮记一笔，页面上能读。它用到了全部五个口子中的三个。

```python
"""她今天和你说了多少话。"""

from collections.abc import Sequence

from core.plugin import Plugin, Route, TurnContext
from core.prompt import Section, Slot
from core.store import Store


class Diary(Plugin):
    name = "diary"
    requires = ("memory",)      # 排在 memory 之后：它读 memory 写的表

    def __init__(self, store: Store) -> None:
        self._store = store

    def capabilities(self) -> dict:
        return {"panel": "diary"}

    def migrations(self) -> Sequence[str]:
        return (
            "CREATE TABLE IF NOT EXISTS diary ("
            " user_id TEXT NOT NULL, character_id TEXT NOT NULL,"
            " day TEXT NOT NULL, turns INTEGER NOT NULL DEFAULT 0,"
            " PRIMARY KEY (user_id, character_id, day))",
        )

    async def prompt_sections(self, turn: TurnContext) -> Sequence[Section]:
        rows = self._store.query(
            "SELECT turns FROM diary WHERE user_id = ? AND character_id = ?"
            " ORDER BY day DESC LIMIT 1",
            (turn.conversation.user_id, turn.conversation.character_id),
        )
        if not rows:
            return ()
        return (Section(Slot.TURN, f"## 昨天你们说了 {rows[0]['turns']} 句话。", order=30,
                        title="diary"),)

    async def after_turn(self, turn: TurnContext) -> None:
        if turn.reply is None:
            return          # 回复没落库，这一轮不算
        day = turn.now.date().isoformat()
        self._store.execute(
            "INSERT INTO diary (user_id, character_id, day, turns) VALUES (?, ?, ?, 1)"
            " ON CONFLICT (user_id, character_id, day) DO UPDATE SET turns = turns + 1",
            (turn.conversation.user_id, turn.conversation.character_id, day),
        )

    def routes(self) -> dict[tuple[str, str], Route]:
        async def read(query, body):        # query 是扁平 dict，body 是 JSON
            rows = self._store.query(
                "SELECT day, turns FROM diary WHERE user_id = ? ORDER BY day DESC",
                (str(query.get("u") or ""),),
            )
            return {"days": [dict(row) for row in rows]}

        return {("GET", "/api/diary"): read}
```

然后在 `app.py` 里把它放进列表：

```python
plugins = [self.characters, self.quota, ..., self.diary]
```

## 五条要记住的

**1. `before_turn` 只能看，不能改。**
它返回 `Veto(reason, retry_after)` 就是「这一轮不许开始」；`reason` 是给前端翻译的稳定 key（`cooling`、`quota.turns`、`photos.hourly`），`retry_after` 是多少秒以后可以再来。

**2. `prompt_sections` 只能写 `turn` 和 `profile`。**
写别处会抛 `ValueError`。`turn` 是这一轮才成立的东西（不缓存），`profile` 是这位制作人的长期设定（缓存）。想往人设里写，那是内核的事，不是插件的事。

**3. `after_turn` 只在回复落库之后跑。**
它会拿到 `turn.reply`；这一轮失败、被拒、被中断时它根本不会被调用。所以「失败的一轮不扣次数、不动好感度」是免费的，不用自己判断。

**4. 要模型替你看一眼，就提问题，别自己建一个客户端。**
`judge_questions()` 返回 `Question(key, ask, kind, least, most)`，答案会出现在 `turn.facts[key]`。几个插件的问题合成一次请求——自己调模型就是第 N 次请求、第 N 倍延迟。

```python
def judge_questions(self):
    return (Question("diary.warm", "这句话是不是在关心她？", "bool"),)

async def after_turn(self, turn):
    if turn.facts.get("diary.warm"):
        ...
```

判官挂了 `facts` 就是空的：**读不到就当作没有，不要猜**。

**5. 挂了不要紧，但不要挂掉别人。**
宿主会捕获钩子里的异常，把它记成一条 `failed` 事件，这一轮继续。所以插件里不要写 `raise` 来表达「我不想参与」——做不到就安静地不做。

## 什么时候该拆成插件

一条判据：**关掉它，对话还能不能继续？**

- 好感度、记忆、礼物、语音：关掉以后她照样说话 → 插件。
- 回复格式、历史裁剪、提示词装配：关掉就不是这个产品了 → 内核。

另一条：**它需要一张自己的表吗？** 需要，就说明它在记自己的事，那它应该是一个插件（内核只有三张表，多一张都不认）。

## 同一份数据，两种形状：`plugins/recall.py`

角色的资料（`characters/facts/<id>.md`）有两种用法，值得当成一个模式看：**整份**（数据版）与**按语义取几段**（向量版）。两份代码在同一个插件里，因为对内核而言它是同一件事——「她的资料」。

区别只在东西放进哪个槽：

- **整份**：对同一个角色永远不变，所以属于 `persona`，跟人设一起缓存。`persona` 槽只有内核能写，于是插件把文字交出去（`recall.persona(character_id)`），由装配处（`app.py` 的 `_base_sections`）放进 `base`。这是「插件提供内容、内核决定位置」的一个例外，写在这里是因为整份资料确实不是「这一轮的事」。
- **向量版**：每轮取哪几段都不同，所以是 `prompt_sections` 返回的 `turn` 段，和记忆、好感度同一层。

判断「该放哪」的一句话：**这一轮会不会变？** 会变就是 `turn`，不会变又想参与缓存，就交给装配处放进 `persona`／`profile`。

`recall.py` 另外演示了三件小事：表由插件自己建（`recall_vectors`）；嵌入这种阻塞调用走 `asyncio.to_thread`，失败就这一轮不带资料；判断模型的问题写成 `Question("recall.needed", …)`，和好感度、记忆的问题并进同一次请求。

## 前端怎么知道有这个插件

`capabilities()` 的内容会出现在 `GET /api/state` 的 `capabilities` 里，按插件名归档：

```json
{"diary": {"enabled": true, "panel": "diary"}}
```

被关掉的插件也在里面，带一个 `why`：

```json
{"voice": {"enabled": false, "why": "needs FISH_AUDIO_API_KEY"}}
```

前端的规矩是：**只认 `capabilities`，不猜后端版本**。设置页的分类、礼物页开不开、铃铛显不显示，都是读它决定的。
