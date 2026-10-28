"""Her own Qzone: where a post comes from, and what happens when it will not go out.

The channel can talk to people; this is the one place it speaks to nobody in particular. A
说说 is not a reply — there is no question, no room, no one to answer — so the machinery that
makes a turn work (merging messages, deciding whether to speak, streaming a reply) is all
wrong for it. What it needs instead is *timing* and *restraint*: one post a day at most, never
twice by accident, never a paragraph of somebody's private conversation.

Three things live here:

- **When.** `maybe()` is the whole calendar in one function: the hour, one post a day, twenty
  hours between two posts, a waiting period after a failure, and a daily coin flip. Every
  decision is written into `qq_flags` before it takes effect, because this box reboots and a
  decision kept only in memory is a decision that gets made again.
- **What.** `compose()` asks the model, then refuses most of what comes back: too long, a
  phone number, a link, an `@`, or the word 「AI」. A public post is the one output that
  cannot be taken back by saying something else afterwards.
- **Whether it worked.** `send_qzone_msg` answers with a `tid`, and a post without one is
  treated as *possibly published* — the day is spent rather than risk posting it twice.

The interface is NapCat's extension (`send_qzone_msg` / `delete_qzone_msg`), not OneBot 11:
there is no read-only way to ask whether this account may post, so the first failure is the
only probe, and `unsupported` is remembered for a day instead of retried every minute.
"""

import json
import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from core.live import source

POST_LIMIT = 240
"""Characters in one post. Qzone takes two thousand; two thousand is an essay, not a status."""

POST_FLOOR = 16
"""Below this it is not a post either — it is a fragment that got cut off."""

POST_GAP = 20.0
"""Hours between two posts. 「One a day」 alone lets 23:50 and 00:10 count as two days."""

RETRY_GAP = 2.0
"""Hours to wait after a failed post. A failure is usually the login or a rate limit, and
retrying every five minutes is how an account ends up on a list."""

UNSUPPORTED_TTL = 24.0
"""How long 「这个 NapCat 没有这个接口」 is believed. Long enough not to spam the log, short
enough that upgrading NapCat takes effect without a code change."""

REPEAT = 0.6
"""Bigram overlap above which a new post counts as the same post again."""

RECENT = 8
"""How many past posts the repeat check and the prompt look at."""

RIGHT = {1: "所有人可见", 4: "好友可见", 16: "部分好友可见", 64: "仅自己可见", 128: "部分好友不可见"}
"""`ugc_right`. 4 is the default: a persona account's diary belongs to its friends list, not
to everyone who finds the number."""

BANNED = ("AI", "ai", "机器人", "模型", "提示词", "prompt", "prompt", "系统消息", "角色扮演", "虚拟主播")
"""Words that mean she has stepped out of character. A 说说 is the one place nothing can
correct it afterwards, so the check is a refusal, not a warning."""

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS qzone_posts ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " tid TEXT NOT NULL DEFAULT '',"
    " content TEXT NOT NULL,"
    " ugc_right INTEGER NOT NULL DEFAULT 4,"
    " ok INTEGER NOT NULL DEFAULT 1,"
    " error TEXT NOT NULL DEFAULT '',"
    " written_at TEXT NOT NULL,"
    " posted_at TEXT)",
    "CREATE INDEX IF NOT EXISTS qzone_posts_ok ON qzone_posts(ok, id)",
)


class ContentError(RuntimeError):
    """The model wrote something that must not be published. `why` is the log line."""

    def __init__(self, why: str) -> None:
        self.why = why
        super().__init__(why)


class PublishError(RuntimeError):
    """`kind` is what the caller reacts to: `network`, `login`, `limited`, `unsupported`,
    `rejected`, or `unclear` (it may have gone out)."""

    def __init__(self, kind: str, detail: str = "") -> None:
        self.kind = kind
        self.detail = detail
        super().__init__(f"{kind}: {detail}" if detail else kind)


class Flags(Protocol):
    """The two methods `Qzone` needs from the channel's flag store (`qq_flags`)."""

    def flag(self, key: str) -> str: ...

    def set_flag(self, key: str, value: str) -> None: ...


class Memory:
    """A flag store that forgets: for a test, and for a deployment with no table yet."""

    def __init__(self) -> None:
        self._rows: dict[str, str] = {}

    def flag(self, key: str) -> str:
        return self._rows.get(key, "")

    def set_flag(self, key: str, value: str) -> None:
        self._rows[key] = value


@dataclass(frozen=True)
class Posted:
    """One row of history: what was written, whether it went out, and why not."""

    content: str
    ok: bool
    at: datetime
    tid: str = ""
    error: str = ""


def _kind(exc: BaseException) -> str:
    """Which failure this is, from the only thing OneBot gives us: a string.

    `OneBotDown` is a type; everything else NapCat says in prose, and the prose is Chinese
    and changes between builds — so this reads the message rather than the class, and
    anything it does not recognise is `rejected` (retryable later, unlike `unsupported`).
    """
    name = type(exc).__name__
    text = str(exc)
    if name == "OneBotDown" or "not connected" in text:
        return "network"
    if any(word in text for word in ("不支持", "没有实现", "not implemented", "unknown action", "未知的")):
        return "unsupported"
    if any(word in text for word in ("登录", "login", "skey", "ptlogin", "未登录", "失效")):
        return "login"
    if any(word in text for word in ("频繁", "风控", "限制", "验证", "try again", "too often")):
        return "limited"
    return "rejected"


def _bulk(text: str) -> set[str]:
    """Character bigrams, for the repeat check. Cheap, and it does not care about spacing."""
    clean = re.sub(r"\s+", "", text)
    return {clean[i : i + 2] for i in range(len(clean) - 1)}


def _similar(one: str, other: str) -> float:
    a, b = _bulk(one), _bulk(other)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _clean(raw: str) -> str:
    """The model's answer as text a person would read.

    Strips the scaffolding a model adds unasked: fences, a 「说说：」 label, wrapper quotes,
    markdown emphasis. It does not rewrite anything — if what is left still fails `_check`,
    the post does not happen.
    """
    text = raw.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    text = re.sub(r"^(说说|文案|正文|内容)\s*[:：]\s*", "", text)
    text = re.sub(r"^[\"'“”『』「」]+|[\"'“”『』「」]+$", "", text).strip()
    text = re.sub(r"^\s*[-*#]+\s*", "", text, flags=re.M)
    text = text.replace("**", "").replace("　", " ")
    return re.sub(r"\n{3,}", "\n\n", text)


def _check(text: str, limit: int = POST_LIMIT) -> tuple[bool, str]:
    """`(may publish, why not)`. Conservative on purpose: a skipped post costs a day, a bad
    post costs an account."""
    if not text:
        return False, "空的"
    if len(text) < POST_FLOOR:
        return False, f"太短（{len(text)} 字）"
    if len(text) > limit:
        return False, f"太长（{len(text)} 字）"
    if re.search(r"\d{5,}", text):
        return False, "带了一串数字（QQ 号、群号或电话）"
    if re.search(r"https?://|www\.|\.com|\.cn/|\.net", text, re.I):
        return False, "带了链接"
    if "@" in text:
        return False, "在空间里 @ 人"
    for word in BANNED:
        if word in text:
            return False, f"破功了（{word}）"
    return True, ""


class Qzone:
    """The account's own space: one post a day, and only when every gate agrees."""

    def __init__(
        self,
        client: Any,
        llm: Any,
        store: Any,
        *,
        character: str = "",
        persona: str | Path | None = None,
        flags: Flags | None = None,
        ugc_right: int = 4,
        hour: int = 21,
        chance: float = 0.35,
        gap: float = POST_GAP,
        retry: float = RETRY_GAP,
        limit: int = POST_LIMIT,
        clock: Callable[[], datetime] | None = None,
        rng: Callable[[], float] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._client = client
        self._llm = llm
        self._store = store
        self._chars = character
        self._src = source(persona) if persona else None
        self._flags = flags or Memory()
        self.right = ugc_right if ugc_right in RIGHT else 4
        self.hour = hour
        self.chance = min(max(chance, 0.0), 1.0)
        self.gap = gap
        self.retry = retry
        self.limit = limit
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._rng = rng or random.random
        self._log = log or (lambda line: None)

    # ---- what the page and the tests read

    def persona(self) -> str:
        return self._src.text() if self._src is not None else ""

    def recent(self, count: int = RECENT) -> list[Posted]:
        rows = self._store.query(
            "SELECT content, ok, tid, error, written_at, posted_at FROM qzone_posts"
            " ORDER BY id DESC LIMIT ?",
            (int(count),),
        )
        return [_posted(row) for row in rows]

    def last_ok(self) -> datetime | None:
        rows = self._store.query(
            "SELECT posted_at FROM qzone_posts WHERE ok = 1 AND posted_at IS NOT NULL"
            " ORDER BY id DESC LIMIT 1"
        )
        return _parse(rows[0]["posted_at"]) if rows else None

    def last_fail(self) -> datetime | None:
        rows = self._store.query(
            "SELECT written_at FROM qzone_posts WHERE ok = 0 ORDER BY id DESC LIMIT 1"
        )
        return _parse(rows[0]["written_at"]) if rows else None

    def status(self) -> dict[str, Any]:
        last = self.last_ok()
        today = self._flag("qzone:day") == self._clock().date().isoformat()
        return {
            "hour": self.hour,
            "chance": round(self.chance, 2),
            "right": self.right,
            "right_label": RIGHT.get(self.right, ""),
            "last": last.isoformat(timespec="seconds") if last else "",
            "today": today,
            "unsupported": bool(self._flag("qzone:unsupported")),
            "count": len(self.recent(50)),
            "recent": [
                {
                    "content": one.content,
                    "ok": one.ok,
                    "at": one.at.isoformat(timespec="seconds"),
                    "tid": one.tid,
                    "error": one.error,
                }
                for one in self.recent(5)
            ],
        }

    # ---- when

    async def maybe(self, now: datetime | None = None) -> dict[str, Any]:
        """One tick. Returns what it did and why, so the caller can log one line.

        Every early return is a *reason*, because 「她今天没发」 is not an answer when the
        question is why — it took a full day to notice last time.
        """
        now = now or self._clock()
        day = now.date().isoformat()

        if not self.persona().strip():
            return self._skip("没有写作口径（characters/world/<id>-qzone.md）", quiet=False)
        if self._unsupported(now):
            return self._skip("这个 NapCat 没有 send_qzone_msg", quiet=True)
        if now.hour < self.hour:
            return self._skip("还不到点", quiet=True)
        if self._flag("qzone:day") == day:
            return self._skip("今天已经发过", quiet=True)

        last = self.last_ok()
        if last is not None and (now - last).total_seconds() < self.gap * 3600:
            return self._skip("离上一条太近", quiet=True)
        failed = _parse(self._flag("qzone:fail"))
        if failed is not None and (now - failed).total_seconds() < self.retry * 3600:
            return self._skip("上次没成，还在等", quiet=True)
        if not self._roll(day):
            return self._skip("今天抽到的签是不写", quiet=False)

        try:
            text = await self.compose(now)
        except ContentError as exc:
            # The day is not spent: a model that returned nothing, or wrote a phone number,
            # is worth one more try after the wait. What stops a loop is the wait itself.
            self._set_flag("qzone:fail", now.isoformat(timespec="seconds"))
            return self._skip(f"写不出来（{exc.why}）", quiet=False)

        result = await self.post(text, now=now)
        return result

    def _skip(self, why: str, *, quiet: bool) -> dict[str, Any]:
        if not quiet:
            self._log(f"qq: 空间今天不发（{why}）")
        return {"posted": False, "why": why}

    def _roll(self, day: str) -> bool:
        """The day's coin flip, decided once. Rolling per tick would make `chance` a lie:
        a minute-by-minute 35% is a certainty by breakfast."""
        key = f"qzone:roll:{day}"
        stored = self._flag(key)
        if stored in ("0", "1"):
            return stored == "1"
        win = self._rng() < self.chance
        self._set_flag(key, "1" if win else "0")
        return win

    def _unsupported(self, now: datetime) -> bool:
        stamp = _parse(self._flag("qzone:unsupported"))
        if stamp is None:
            return False
        if (now - stamp).total_seconds() > UNSUPPORTED_TTL * 3600:
            self._set_flag("qzone:unsupported", "")
            return False
        return True

    # ---- what

    async def compose(self, now: datetime | None = None) -> str:
        """One post's text, or `ContentError`. Two attempts, never three: a model that wrote
        a phone number twice will write it a third time."""
        now = now or self._clock()
        why = "没写出来"
        for _ in range(2):
            raw = await self._ask(now)
            text = _clean(raw)
            ok, why = _check(text, self.limit)
            if not ok:
                continue
            if self._repeat(text):
                why = "和最近一条太像"
                continue
            return text
        raise ContentError(why)

    def _repeat(self, text: str) -> bool:
        """Only against what actually went out. A failed attempt left nothing on the wall, so
        its text is not a reason to refuse the retry — otherwise one bad night makes the same
        sentence unusable for a week of retries."""
        return any(
            _similar(text, one.content) >= REPEAT for one in self.recent(RECENT) if one.ok
        )

    async def _ask(self, now: datetime) -> str:
        messages = self._messages(now)
        try:
            chunks = [chunk async for chunk in self._llm.stream(messages)]
        except Exception as exc:
            raise ContentError(f"模型没回话（{type(exc).__name__}）") from exc
        return "".join(chunks)

    def _messages(self, now: datetime) -> list[Any]:
        from core.llm import ChatMessage

        week = "一二三四五六日"[now.weekday()]
        recent = [one.content for one in self.recent(3) if one.ok]
        lines = [
            self.persona().strip(),
            "",
            "## 这一次",
            f"现在是 {now.month} 月 {now.day} 日（周{week}）{now:%H:%M}。",
            "写一条 QQ 空间的说说。没有人问她话，也没有上文——这是她自己空间里的一条。",
        ]
        if recent:
            lines.append("最近已经发过：")
            lines.extend(f"- {one}" for one in recent)
            lines.append("不要和这些重复，也不要接着上一条往下说。")
        lines += [
            f"只输出正文，{POST_FLOOR}～{self.limit} 字，一到三句。",
            "不要引号、不要「说说：」这类标签、不要话题标签、不要解释你在做什么。",
        ]
        return [ChatMessage("system", "\n".join(lines))]

    # ---- whether it worked

    async def post(self, text: str, *, now: datetime | None = None, images: Sequence[str] = ()) -> dict[str, Any]:
        """Publish one post that has already been written and checked."""
        now = now or self._clock()
        ok, why = _check(text, self.limit)
        if not ok:
            raise ContentError(why)
        try:
            tid = await self.publish(text, images=images)
        except PublishError as exc:
            self._record(text, "", ok=False, error=str(exc), now=now)
            self._set_flag("qzone:fail", now.isoformat(timespec="seconds"))
            if exc.kind == "unsupported":
                self._set_flag("qzone:unsupported", now.isoformat(timespec="seconds"))
                self._log("qq: 空间发不出去 —— 这个 NapCat 没有 send_qzone_msg（升级 NapCat 后会自己重试）")
            elif exc.kind == "unclear":
                # It may be on the wall already. Spend the day rather than post it twice.
                self._set_flag("qzone:day", now.date().isoformat())
                self._log(f"qq: 空间可能发出去了，但没拿到 tid，今天不再发（{exc.detail}）")
            elif exc.kind == "login":
                self._log(f"qq: 空间发不出去 —— 账号登录态没了（{exc.detail}）")
            else:
                self._log(f"qq: 空间发不出去 —— {exc.kind}（{exc.detail}）")
            return {"posted": False, "why": exc.kind, "text": text}
        self._record(text, tid, ok=True, error="", now=now)
        # One line here rather than at each caller: a post that went out should be readable in
        # the log whichever way it went out, including the hand-sent ones.
        self._log(f"qq: 空间发了 —— {len(text)} 字，{RIGHT.get(self.right, '')}")
        # Any successful post spends the day, including one a person sent by hand: waking up
        # to two posts because the calendar did not know about the manual one is exactly the
        # kind of detail that makes an account look automated.
        self._set_flag("qzone:day", now.date().isoformat())
        self._set_flag("qzone:last", now.isoformat(timespec="seconds"))
        return {"posted": True, "tid": tid, "text": text}

    async def publish(self, text: str, *, images: Sequence[str] = (), ugc_right: int | None = None) -> str:
        """`send_qzone_msg`, and a `tid` or an exception. No retry here: the caller owns the
        calendar, and a retry inside this method would post twice on a slow answer."""
        right = ugc_right if ugc_right in RIGHT else self.right
        try:
            data = await self._client.call(
                "send_qzone_msg", content=text, images=list(images), ugc_right=right
            )
        except Exception as exc:
            raise PublishError(_kind(exc), str(exc)) from exc
        tid = str((data or {}).get("tid") or "").strip()
        if not tid:
            raise PublishError("unclear", "接口没给 tid")
        return tid

    async def delete(self, tid: str) -> None:
        """Take one back. The only undo a 说说 has."""
        try:
            await self._client.call("delete_qzone_msg", tid=tid)
        except Exception as exc:
            raise PublishError(_kind(exc), str(exc)) from exc

    # ---- the small ones

    def _record(self, text: str, tid: str, *, ok: bool, error: str, now: datetime) -> None:
        self._store.execute(
            "INSERT INTO qzone_posts (tid, content, ugc_right, ok, error, written_at, posted_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                tid,
                text,
                self.right,
                int(ok),
                error[:200],
                now.isoformat(timespec="seconds"),
                now.isoformat(timespec="seconds") if ok else None,
            ),
        )

    def _flag(self, key: str) -> str:
        return str(self._flags.flag(key) or "")

    def _set_flag(self, key: str, value: str) -> None:
        self._flags.set_flag(key, value)


def _posted(row: Any) -> Posted:
    at = _parse(row["posted_at"]) or _parse(row["written_at"]) or datetime.now().astimezone()
    return Posted(
        content=str(row["content"]),
        ok=bool(row["ok"]),
        at=at,
        tid=str(row["tid"] or ""),
        error=str(row["error"] or ""),
    )


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.astimezone()


def dump(result: dict[str, Any]) -> str:
    """One line for the log: what happened, and the post's first few characters."""
    text = str(result.get("text") or "")
    head = text.replace("\n", " ")[:24]
    return json.dumps(
        {"posted": result.get("posted"), "why": result.get("why", ""), "head": head},
        ensure_ascii=False,
    )
