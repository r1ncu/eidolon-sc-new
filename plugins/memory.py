"""What she remembers about you after the conversation scrolls away.

The history window is twenty turns; a producer who says "我下周要考试" on Monday should
not have to repeat it on Friday. So after each turn a background model reads the exchange
and writes down, in Chinese, whatever he revealed about himself that is worth keeping.

Four decisions are upstream's, and each one changes behaviour visibly, so they are kept:

- **A fact, not a summary.** The question asks for one sentence about the producer. A
  memory that reads like a recap of the chat is a second history, and the prompt already
  has one of those.
- **All of a producer's characters share his memories** (upstream ADR-0002). A memory names
  the character it was told to — its *source character* — and a character who finds one
  told to someone else is asked to pass it on as hearsay, 「听 X 说……」, never as something
  the producer told her himself.
- **What he says may update what he said before** (upstream ADR-0014). Writing it down, the
  model is shown the memories nearest the turn and says which of them the new memory
  supersedes. A superseded memory is neither recalled nor shown again, but stays listed
  until the producer deletes it; deleting what superseded it brings it back.
- **A cleared memory never comes back.** Writing runs after the turn, so a clear that
  happened while it was being written discards it.

Where this deliberately differs from upstream: no embeddings and no reranker. Upstream
finds memories by vector search; here the turn is shown the latest few, and the extraction
prompt is shown the latest few as well. Without an embedding service on this deployment
that is the honest version of the same idea, and it keeps the plugin loadable with no
network beyond the one model call it already makes.
"""

import json
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.llm import ChatMessage, LLM
from core.plugin import Plugin, Route, TurnContext
from core.prompt import Section, Slot
from core.store import Store

RECALL = 8
"""How many memories the turn's prompt carries: enough to hold a person's circumstances,
few enough that the list is not most of the prompt."""

SHOWN_MEMORIES = 20
"""How many memories the background model is shown, numbered, so it neither writes them
down again nor misses what the turn supersedes."""

EXCHANGE_HISTORY = 4
"""How many messages before the producer's latest the background model reads with it."""

MAX_NEW_MEMORIES = 5
"""The most memories one turn writes down."""

MAX_MEMORY_LENGTH = 200
"""How much of a memory, in characters, is kept; longer ones are cut short."""

SETTLE_TIMEOUT = 5.0
"""How long a turn, listing or deleting waits for the producer's previous turn to finish
being written down before going on without it."""

ANOTHER_IDOL = "事务所的另一位偶像"
"""Who a memory was told to, when that character is not on this deployment."""

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS memories ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " user_id TEXT NOT NULL,"
    " character_id TEXT NOT NULL,"
    " text TEXT NOT NULL,"
    " at TEXT NOT NULL,"
    " superseded_by INTEGER)",
    "ALTER TABLE memories ADD COLUMN superseded_by INTEGER",
    "CREATE INDEX IF NOT EXISTS memories_by_pair ON memories(user_id, character_id, id)",
)


@dataclass(frozen=True)
class Remembered:
    """Something about a producer worth keeping across conversations, written in Chinese,
    with the character it was told to (its source character)."""

    id: int
    user_id: str
    source_character_id: str
    text: str
    at: str
    superseded_by: int | None = None

    @property
    def holding(self) -> bool:
        return self.superseded_by is None


@dataclass(frozen=True)
class NewMemory:
    """A memory the background model wrote down: its text, and the numbers it was shown the
    memories this one supersedes under, counting from 1."""

    text: str
    supersedes: tuple[int, ...] = ()


def parse_memories(answer: str) -> list[NewMemory] | None:
    """The memories in the model's answer; None if it is not the JSON asked for. A memory
    written as text alone supersedes nothing; what it supersedes is read as whole numbers
    only."""
    start, end = answer.find("{"), answer.rfind("}")
    try:
        memories = json.loads(answer[start : end + 1])["memories"] if start >= 0 else None
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(memories, list):
        return None
    written: dict[str, NewMemory] = {}
    for memory in memories:
        if isinstance(memory, dict):
            text, supersedes = memory.get("text"), memory.get("supersedes")
        else:
            text, supersedes = memory, None
        if not isinstance(text, str):
            continue
        text = text.strip()[:MAX_MEMORY_LENGTH]
        if not text:
            continue
        numbers = supersedes if isinstance(supersedes, list) else []
        written.setdefault(text, NewMemory(text, tuple(dict.fromkeys(n for n in numbers if type(n) is int))))
    return list(written.values())[:MAX_NEW_MEMORIES]


class Memory(Plugin):
    name = "memory"

    def __init__(
        self,
        store: Store,
        *,
        llm: LLM | None = None,
        names: Callable[[str], str | None] | None = None,
        recall: int = RECALL,
        clock: Callable[[], datetime] | None = None,
        timezone_name: str = "Asia/Shanghai",
    ) -> None:
        self._store = store
        self._llm = llm
        self._names = names or (lambda character_id: None)
        self._recall = recall
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._timezone = timezone_name
        self._lock = threading.Lock()
        self._pending: dict[str, list[threading.Thread]] = {}
        self._clearings: dict[str, int] = {}

    def capabilities(self) -> dict[str, object]:
        return {"panel": "memories", "recall": self._recall, "shared": True, "extracting": self._llm is not None}

    def migrations(self) -> Sequence[str]:
        return _SCHEMA

    # ---- the prompt

    async def prompt_sections(self, turn: TurnContext) -> Sequence[Section]:
        user = turn.conversation.user_id
        self.settle(user)
        found = self.recall_memories(user)[-self._recall :]
        if not found:
            return ()
        return (
            Section(
                Slot.TURN,
                "# 关于制作人的记忆\n" + self._memories_prompt(
                    found,
                    turn.conversation.character_id,
                    "下面是制作人以前说过的、和这句话可能有关的事，按相关程度排列，和话题无关的可以忽略。"
                    "像平时记得这些事一样自然地提起，不要逐条复述。",
                ),
                order=20,
                title="memory",
            ),
        )

    def _memories_prompt(self, memories: Sequence[Remembered], character_id: str, opening: str) -> str:
        lines = [opening]
        for memory in memories:
            when = _date(_parse(memory.at), self._timezone)
            if memory.source_character_id == character_id:
                lines.append(f"- {memory.text}（{when}，制作人对你说的）")
            else:
                name = self._names(memory.source_character_id) or ANOTHER_IDOL
                lines.append(
                    f"- {memory.text}（{when}，制作人对{name}说的，你是听{name}说的：提起时要用转述的口吻，"
                    f"例如「听{name}说……」，不要说成是制作人亲口告诉你的）"
                )
        return "\n".join(lines)

    # ---- writing down, after the turn

    async def after_turn(self, turn: TurnContext) -> None:
        if turn.reply is None or self._llm is None:
            return
        user = turn.conversation.user_id
        character_id = turn.conversation.character_id
        history = [m for m in self._store.messages(turn.conversation.id)][:-2]
        clearings = self._clearings.get(user, 0)
        shown = self.recall_memories(user)[-SHOWN_MEMORIES:]
        now = self._clock()
        thread = threading.Thread(
            target=self._write,
            args=(user, character_id, history, turn.text, turn.reply, shown, clearings, now),
            name="memory-write",
            daemon=True,
        )
        with self._lock:
            self._pending.setdefault(user, []).append(thread)
        thread.start()

    def _write(
        self,
        user: str,
        character_id: str,
        history: Sequence[Any],
        text: str,
        reply: Any,
        shown: Sequence[Remembered],
        clearings: int,
        now: datetime,
    ) -> None:
        try:
            prompt = self._extraction_prompt(character_id, history, text, reply, shown, now)
            answer = _run(self._llm.stream(prompt))
            written = parse_memories(answer)
            if written is None:
                return
            if self._clearings.get(user, 0) != clearings:
                return  # cleared since the turn began
            self.add(user, character_id, written, shown, now)
        except Exception:  # a background write must never take the turn down
            return
        finally:
            with self._lock:
                threads = self._pending.get(user, [])
                if threading.current_thread() in threads:
                    threads.remove(threading.current_thread())
                if not threads and user in self._pending:
                    del self._pending[user]

    def _extraction_prompt(
        self,
        character_id: str,
        history: Sequence[Any],
        text: str,
        reply: Any,
        shown: Sequence[Remembered],
        now: datetime,
    ) -> list[ChatMessage]:
        name = self._names(character_id) or character_id
        lines = [f"今天是 {_date(now, self._timezone)}（制作人所在时区）。", "", "# 已经记住的事"]
        lines += [f"{n}. {m.text}" for n, m in enumerate(shown, 1)] or ["（还没有）"]
        lines += ["", f"# 制作人和{name}的对话"]
        for message in list(history)[-EXCHANGE_HISTORY:]:
            if message.role == "user":
                lines.append(f"制作人：{message.text}")
            else:
                lines.append(f"{name}：{_said(message)}")
        lines += [f"制作人（最新的一句话）：{text}", f"{name}：{_said(reply)}"]
        return [ChatMessage("system", _INSTRUCTIONS), ChatMessage("user", "\n".join(lines))]

    def add(
        self,
        user: str,
        character_id: str,
        written: Sequence[NewMemory],
        shown: Sequence[Remembered],
        now: datetime,
    ) -> list[Remembered]:
        """Remember what the producer told the character just `now`, each new memory
        superseding those it names among the memories the model was `shown` that still
        hold; returns the memories that are new. One he already has is left out, or if
        superseded, holds again and supersedes what it names."""
        added: list[Remembered] = []
        at = now.isoformat(timespec="seconds")
        for memory in written:
            kept = self._store.query(
                "SELECT id, superseded_by FROM memories WHERE user_id = ? AND text = ?",
                (user, memory.text),
            )
            if not kept:
                rows = self._store.query(
                    "INSERT INTO memories (user_id, character_id, text, at) VALUES (?, ?, ?, ?)"
                    " RETURNING id",
                    (user, character_id, memory.text, at),
                )
                id = int(rows[0]["id"])
                added.append(Remembered(id, user, character_id, memory.text, at))
            else:
                id = int(kept[0]["id"])
                if kept[0]["superseded_by"] is None:
                    continue  # already remembered
                self._store.execute("UPDATE memories SET superseded_by = NULL WHERE id = ?", (id,))
            for number in memory.supersedes:
                if not (1 <= number <= len(shown)) or shown[number - 1].id == id:
                    continue
                self._store.execute(
                    "UPDATE memories SET superseded_by = ? WHERE id = ? AND user_id = ?"
                    " AND superseded_by IS NULL",
                    (id, shown[number - 1].id, user),
                )
        return added

    # ---- reading, deleting

    def recall_memories(self, user_id: str) -> list[Remembered]:
        """Every memory of this producer, oldest first, superseded ones included: whoever
        wants only what still holds filters on `holding`."""
        rows = self._store.query(
            "SELECT id, user_id, character_id, text, at, superseded_by FROM memories"
            " WHERE user_id = ? ORDER BY id",
            (user_id,),
        )
        return [
            Remembered(int(row["id"]), str(row["user_id"]), str(row["character_id"]), str(row["text"]),
                   str(row["at"]), row["superseded_by"])
            for row in rows
        ]

    def recall(self, user_id: str) -> list[str]:
        """What still holds, oldest first, as text: what the turn's prompt carries."""
        return [memory.text for memory in self.recall_memories(user_id) if memory.holding]

    def everything(self, user_id: str) -> list[dict[str, object]]:
        """Every memory of this producer, newest first, with who it was told to and whether
        it has been superseded — what the settings page lists."""
        return [
            {
                "id": memory.id,
                "character_id": memory.source_character_id,
                "source_character_id": memory.source_character_id,
                "source_character_name": self._names(memory.source_character_id),
                "text": memory.text,
                "at": memory.at,
                "created_at": memory.at,
                "superseded": not memory.holding,
            }
            for memory in reversed(self.recall_memories(user_id))
        ]

    def delete(self, user_id: str, memory_id: int) -> bool:
        """Forget one memory; what it superseded is superseded by what superseded it, or
        holds again if nothing did."""
        self.settle(user_id)
        rows = self._store.query(
            "SELECT superseded_by FROM memories WHERE id = ? AND user_id = ?", (memory_id, user_id)
        )
        if not rows:
            return False
        self._store.execute(
            "UPDATE memories SET superseded_by = ? WHERE superseded_by = ? AND user_id = ?",
            (rows[0]["superseded_by"], memory_id, user_id),
        )
        self._store.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        return True

    def clear(self, user_id: str) -> None:
        """Forget all of this producer's memories, including those a turn is still writing:
        what that turn writes down after this is dropped."""
        self._clearings[user_id] = self._clearings.get(user_id, 0) + 1
        self.settle(user_id)
        self._store.execute("DELETE FROM memories WHERE user_id = ?", (user_id,))

    def settle(self, user_id: str) -> None:
        """Wait a while for what this producer's turns are writing down, then go on."""
        with self._lock:
            threads = list(self._pending.get(user_id, []))
        for thread in threads:
            thread.join(SETTLE_TIMEOUT)

    # ---- endpoints

    def routes(self) -> dict[tuple[str, str], Route]:
        async def read(query, body):  # noqa: ANN001 - plugin.Route
            user_id = str(query.get("u") or "")
            if not user_id:
                return {"error": "需要 u（用户）"}
            self.settle(user_id)
            found = [m for m in self.recall_memories(user_id) if m.holding]
            return {
                "memories": [
                    {"text": m.text, "at": m.at, "character_id": m.source_character_id} for m in found
                ]
            }

        async def delete_one(query, body):  # noqa: ANN001
            user_id = str(body.get("u") or "")
            memory_id = body.get("id")
            if not user_id or memory_id is None:
                return {"error": "需要 u（用户）和 id（记忆）"}
            return {"ok": self.delete(user_id, int(memory_id))}

        async def all_of(query, body):  # noqa: ANN001
            user_id = str(query.get("u") or "")
            if not user_id:
                return {"error": "需要 u（用户）"}
            self.settle(user_id)
            return {"memories": self.everything(user_id)}

        async def clear_all(query, body):  # noqa: ANN001
            user_id = str(body.get("u") or query.get("u") or "")
            if not user_id:
                return {"error": "需要 u（用户）"}
            self.clear(user_id)
            return {"ok": True}

        return {
            ("GET", "/api/memory"): read,
            ("POST", "/api/memory/delete"): delete_one,
            ("GET", "/api/memory/all"): all_of,
            ("POST", "/api/memory/clear"): clear_all,
        }


_INSTRUCTIONS = """\
你负责替《偶像大师 闪耀色彩》283 事务所的偶像们记住关于制作人的事。制作人就是正在和偶像对话的用户。
读下面的对话，从制作人最新的一句话里找出值得长期记住的、关于制作人本人的信息。

只记过后仍有意义的事。
要记的：制作人自己透露的个人情况、喜好、经历、计划、身边的人和宠物、希望偶像记住的事；\
会持续几天、朋友过后会问起的近况，例如生病、考试、面试、搬家、出差。
不要记的：当天就会过去的状态（例如累、困、饿、无聊、心情不好）和一时的情绪，\
同一句话里还说了值得记的事时只记那件事、不记这种状态；寒暄；偶像说的话；原作剧情和偶像的设定；制作人向偶像提的问题。

每条记忆是一句能单独看懂的中文陈述，以「制作人」开头，例如「制作人养了一只叫小白的猫」。
提到「明天」「下周六」这样的相对时间时，换算成具体日期写下来。

「已经记住的事」按编号列出了和这轮对话最相关的记忆，写之前先对照：
- 和已经记住的一样的，不要再写。
- 更新或修正了已经记住的事（例如搬了家、换了工作），写一条单独看也完整的新记忆，写清从什么变成了什么，\
例如「制作人从大阪搬到了东京」，并在 supersedes 里写上被它取代的记忆的编号，可以写多个。
- 补充了已经记住的事的细节（例如说出了猫的名字），写一条把细节合进去的完整记忆，取代原来那条。
- 一件事只变了一部分的，新记忆要把仍然成立的部分也写进去，例如原来记着养了一只猫和一只狗、\
现在狗去世了，就写「制作人的狗去世了，现在还养着那只猫」，取代原来那条。
- 两件事可以同时成立的（例如喜欢喝咖啡，最近也开始喝茶），新的照常写，不取代旧的。

只输出 JSON，格式为 {"memories": [{"text": "……", "supersedes": [编号]}]}，不取代任何记忆时 \
supersedes 写 []；没有值得记住的就输出 {"memories": []}。"""


def _run(stream: Any) -> str:
    """The whole answer of one non-streaming use of the model, from an async stream."""
    import asyncio

    async def collect() -> str:
        return "".join([chunk async for chunk in stream])

    return asyncio.run(collect())


def _said(reply: Any) -> str:
    """Her reply as people see it: the Chinese subtitle when there is one."""
    for segment in reply.segments:
        if segment.zh:
            return segment.zh
    for segment in reply.segments:
        if segment.ja:
            return segment.ja
    return ""


def _parse(at: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(at))
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _date(moment: datetime, zone: str) -> str:
    try:
        from zoneinfo import ZoneInfo

        moment = moment.astimezone(ZoneInfo(zone))
    except Exception:
        pass
    return f"{moment.year}年{moment.month}月{moment.day}日"
