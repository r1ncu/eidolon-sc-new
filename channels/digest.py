"""她的记忆，写成一份会自己重写的文件。

一段对话的记录不是记忆。记录只会变长：两个月以后没有人能读它——包括她自己，因为
它进不了提示词，进了也只是把上下文占满。所以这里做的事是**定期重写整份**：还在起作用的
留下，被新事实推翻的改掉，重复的合并，没用的丢掉。磁盘上那个 `.md` 就是记忆本身，
所以动词是「覆盖」——每一版都留着，那是日记，而人不是用日记记住事的。

两种节奏，因为两个房间不一样大：
- 私聊每 `days` 天（默认 3）——那是**一段**对话，他也会注意到她忘了什么；
- 一个群每周一次（默认 7）——五十个人说很多话，但事实很少。

不做的事：不改写历史消息、不删任何数据库里的东西。它只读，然后写一个文件。
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from core.llm import ChatMessage
from core.prompt import Section, Slot

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS qq_digest ("
    " kind TEXT NOT NULL,"
    " key TEXT NOT NULL,"
    " last_seq INTEGER NOT NULL DEFAULT 0,"
    " written_at TEXT NOT NULL,"
    " path TEXT NOT NULL,"
    " PRIMARY KEY (kind, key))",
)

DM = "dm"
GROUP = "group"

MIN_ROWS = 4
"""Fewer new messages than this and there is nothing to rewrite. The watermark is left
alone, so the next round picks them up instead of dropping them."""

MAX_ROWS = 400
"""How many messages one rewrite reads. A week of a busy group is thousands; the tail is
what a memory is made of, and the model cannot hold the rest anyway."""

MAX_INPUT = 24000
"""A character ceiling on the transcript, so one chatty night cannot make a call that costs
more than the whole week."""

MAX_BODY = 4000
"""A hard ceiling on the file. The instruction asks for far less; this exists so a model
that ignores instructions cannot grow the file without limit."""

_AS_MEMORY = (
    "【你记得的事】下面是你自己以前写下的，最近一次整理过。"
    "像记得的事一样用它：该想起就想起，不要复述，不要说「根据我的记录」，"
    "也不要把它当成今天才发生的事。\n\n"
)

_ASK = """你在整理自己留着的记忆，不是整理聊天记录。

下面是「上一版的记忆」和「最近这些天说过的话」。你要用它们写出一版新的记忆，
写完之后上一版就不存在了——所以留下的必须是还在起作用的，不是有意思的。

怎么写：
- 用中文，{self_rule}短句，就是平时说话的样子。
  他说的日语句子可以原样留一小句，不要整段抄。
- 只写还在起作用的事：他的近况和喜好、你们之间变了的东西、还没解决的事、约好了什么、他说过而你在意的话。
- 上一版里仍然对的留下；被新事实推翻的改掉或删掉；重复的合成一句。
- 删掉这些：寒暄、一次性的玩笑、天气、当场就结束了的闲聊、你当时的表情和动作。
- 不要逐条复述对话，不要写「他说……我说……」的记录体，不要写日期流水账；需要时间就用「前几天」「上次」。
- 用 `##` 小标题分组，下面用短句或短列表。总共不超过 900 字。
- 只输出正文，不要开场白，不要代码块，不要重复标题。"""


class Digest:
    """Reads a surface's messages, asks the model to rewrite the memory, writes the file."""

    def __init__(
        self,
        store: Any,
        llm: Any,
        characters: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
        log: Callable[[str], None] | None = None,
        timezone_name: str = "Asia/Shanghai",
        self_name: str = "",
        owner_name: str = "",
    ) -> None:
        """`self_name` is the short form she calls herself in the file, and only her own
        stage file knows it; `owner_name` is what the private chat calls him when the line is
        not addressed by nickname. Empty means the neutral wording — the file is hers, so a
        checkout with no character data should not sign somebody else's name."""
        self._self = (self_name or "").strip()
        self._owner = (owner_name or "").strip() or "对方"
        self._store = store
        self._llm = llm
        self._dir = Path(characters) / "memory"
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._log = log or (lambda line: None)
        self._zone = _zone(timezone_name)
        self._store.migrate(SCHEMA)

    # ---- where it lives

    def path(self, kind: str, key: str) -> Path:
        return self._dir / f"{kind}-{_safe(key)}.md"

    @property
    def directory(self) -> Path:
        return self._dir

    def text(self, kind: str, key: str) -> str:
        """The file as it stands, or `""`. Never raises: a missing memory is an empty one."""
        try:
            return self.path(kind, key).read_text(encoding="utf-8")
        except OSError:
            return ""

    def section(self, kind: str, key: str) -> Section | None:
        """What the turn sees. Background, so it goes late in the turn's own slot."""
        text = self.text(kind, key)
        if not text.strip():
            return None
        return Section(Slot.TURN, _AS_MEMORY + text.strip(), order=30, title="qq:memory")

    # ---- when

    def due(self, kind: str, key: str, days: float, now: datetime | None = None) -> bool:
        """Never written means due now: a deployment that just turned this on should not
        wait three days to find out whether it works."""
        now = now or self._clock()
        rows = self._store.query(
            "SELECT written_at FROM qq_digest WHERE kind = ? AND key = ?", (kind, key)
        )
        if not rows:
            return True
        last = _parse(rows[0]["written_at"])
        if last is None:
            return True
        return now - last >= timedelta(days=max(0.0, float(days)))

    # ---- what it reads

    def _titles(self, kind: str, key: str) -> tuple[str, ...]:
        """A private chat is one thread with one title, even across `/重开`; a group's
        messages are stored one conversation per speaker, all under the room's title."""
        return ("qq:dm",) if kind == DM else (f"qq:group:{key}",)

    def _rows(self, kind: str, key: str, after: int) -> list[Any]:
        found: list[Any] = []
        for title in self._titles(kind, key):
            found.extend(
                self._store.query(
                    "SELECT m.seq, m.role, m.text, m.segments, m.created_at, u.name AS who"
                    " FROM messages m"
                    " JOIN conversations c ON c.id = m.conversation_id"
                    " LEFT JOIN users u ON u.id = c.user_id"
                    " WHERE c.title = ? AND m.seq > ? ORDER BY m.seq",
                    (title, after),
                )
            )
        found.sort(key=lambda row: int(row["seq"]))
        return found

    def _watermark(self, kind: str, key: str) -> int:
        rows = self._store.query(
            "SELECT last_seq FROM qq_digest WHERE kind = ? AND key = ?", (kind, key)
        )
        return int(rows[0]["last_seq"]) if rows else 0

    # ---- the rewrite

    async def run(
        self,
        kind: str,
        key: str,
        *,
        heading: str,
        days: float,
        now: datetime | None = None,
    ) -> Path | None:
        """One rewrite. Returns the file written, or None when there was nothing to do."""
        now = now or self._clock()
        rows = self._rows(kind, key, self._watermark(kind, key))
        if len(rows) < MIN_ROWS:
            return None
        old = self.text(kind, key)
        transcript, dropped = _transcript(
            rows, named=kind != DM, owner=self._owner, self_name=self._self
        )
        prompt = self._prompt(heading, old, transcript, now)
        answer = await _collect(self._llm.stream(prompt))
        body = _clean(answer, heading)
        if not body:
            self._log(f"qq: {kind} 的记忆这次没写成（模型给了空回答）")
            return None
        stamp = now.astimezone(self._zone).strftime("%Y-%m-%d %H:%M")
        page = f"# {heading}\n\n> 上次整理：{stamp}\n\n{body}\n"
        target = self.path(kind, key)
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            _write_atomic(target, page)
        except OSError as exc:  # a disk that will not take it must not stop the channel
            hint = ""
            if isinstance(exc, PermissionError):
                hint = (
                    "（目录不给容器用户写。在宿主机上执行："
                    "mkdir -p characters/memory && chown 10001:999 characters/memory "
                    "&& chmod 2777 characters/memory）"
                )
            self._log(f"qq: {kind} 的记忆写不进文件（{type(exc).__name__}: {exc}）{hint}")
            return None
        self._store.execute(
            "INSERT INTO qq_digest (kind, key, last_seq, written_at, path)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(kind, key) DO UPDATE SET last_seq = excluded.last_seq,"
            " written_at = excluded.written_at, path = excluded.path",
            (kind, key, int(rows[-1]["seq"]), now.isoformat(), str(target)),
        )
        self._log(
            f"qq: {kind} 的记忆写好了 —— {len(rows)} 条消息"
            + (f"（更早的 {dropped} 条没读完）" if dropped else "")
            + f" → {len(body)} 字，{target}"
        )
        return target

    def _ask(self) -> str:
        """The instruction, with the character's own name in the one place it belongs.

        「第一人称要写名字，不要写「我」」 is a rule about one character. Without a name the sentence
        is dropped rather than replaced with 「我」 — a memory written in first person reads
        like a diary, and the point of the file is that she recognises it as hers.
        """
        rule = f"第一人称一律写「{self._self}」，**不要写「我」**；" if self._self else ""
        return _ASK.replace("{self_rule}", rule)

    def _prompt(
        self, heading: str, old: str, transcript: str, now: datetime
    ) -> list[ChatMessage]:
        lines = [
            f"今天是 {now.astimezone(self._zone):%Y-%m-%d}。",
            "",
            f"# 上一版的记忆（{heading}）",
            old.strip() or "（这是第一次整理，还没有上一版）",
            "",
            "# 最近说过的话",
            transcript,
        ]
        return [ChatMessage("system", self._ask()), ChatMessage("user", "\n".join(lines))]


def _transcript(
    rows: Sequence[Any], *, named: bool, owner: str = "对方", self_name: str = "", limit: int = MAX_INPUT
) -> tuple[str, int]:
    """The tail of the window, as plain lines. Returns it and how many were left out.

    `named` is the group: there the speaker's name is the point (fifty people, and 「他」
    would be a lie). In a private chat there is only one person, so every line is his.
    """
    kept = list(rows)[-MAX_ROWS:]
    dropped = len(rows) - len(kept)
    lines: list[str] = []
    size = 0
    for row in reversed(kept):
        line = _line(row, named=named, owner=owner, self_name=self_name)
        if not line:
            continue
        if size + len(line) > limit:
            dropped += 1
            continue
        lines.append(line)
        size += len(line) + 1
    lines.reverse()
    return "\n".join(lines), dropped


def _line(row: Any, *, named: bool, owner: str = "对方", self_name: str = "") -> str:
    if row["role"] == "user":
        who = (row["who"] or "谁") if named else owner
        return f"{who}：{(row['text'] or '').strip()}"
    return f"{self_name or '自己'}：{_her_words(row['segments'])}"


def _her_words(raw: Any) -> str:
    """Her line in her own words: the Japanese she actually said, falling back to the
    subtitle. The memory is written by her, so her voice is the useful half."""
    try:
        segments = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return ""
    said = [part[0] for part in segments if part and part[0]]
    if not said:
        said = [part[1] for part in segments if len(part) > 1 and part[1]]
    return " ".join(said).strip()


def _clean(answer: str, heading: str) -> str:
    text = (answer or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text).strip()
    # The heading is written by us; a model that repeats it would give the file two H1s.
    text = re.sub(rf"^#+\s*{re.escape(heading)}\s*\n+", "", text).strip()
    if len(text) > MAX_BODY:
        cut = text[:MAX_BODY]
        text = cut[: cut.rfind("\n")] if "\n" in cut else cut
    return text


def _write_atomic(target: Path, page: str) -> None:
    """Old file stays whole until the new one is complete: a half-written memory is worse
    than a slightly stale one, and this file is the only copy."""
    temporary = target.with_suffix(".md.tmp")
    temporary.write_text(page, encoding="utf-8")
    temporary.replace(target)


def _safe(key: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", str(key)).strip("_") or "x"


def _zone(name: str = "Asia/Shanghai"):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # no tzdata: UTC is wrong by hours, but it is still a date
        return timezone.utc


def _parse(raw: Any) -> datetime | None:
    try:
        found = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    return found if found.tzinfo else found.replace(tzinfo=timezone.utc)


async def _collect(stream: Any) -> str:
    parts: list[str] = []
    try:
        async for chunk in stream:
            parts.append(chunk)
    except Exception as exc:  # a failed summary is a log line, not a lost turn
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc
    return "".join(parts)
