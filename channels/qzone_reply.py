"""Her own wall, answered: somebody comments on her 说说, she says something back.

`channels/qzone.py` writes the post. This reads what came after it — not the friends' stream,
not other people's walls, only **her own posts and the comments under them**. That is a
different job from 「回别人的动态」 in one way that matters:

- the audience is people who already chose to talk to her, so the answer is a **楼中楼** —
  under that person's words, with their name on it — not a fresh comment on the thread. A
  plain comment is not an acceptable fallback here: it reads as her talking to the room
  instead of to the person who spoke to her;
- a comment can be answered days later — it is a message to her, not a feed item.

What it deliberately does **not** do:

- **It does not answer every comment** — but the line is 辱骂, not 「不客气」. 低俗 / 性相关 /
  骂人（人身攻击）gets no answer; the model says `SKIP` in one word rather than trying. A
  comment that is merely 找茬 — 抬杠、拿她跟别人比、让她去炒菜 — **does** get one: that is
  the kind of thing she is good at, and 「要扣分哦」 is a better answer than silence. Silence
  is reserved for the two things silence is for: content she will not touch, and a person who
  is only shouting.
- **It does not answer itself.** Her own comments are filtered by `uin`.
- **It does not answer history.** The first poll after install records what is already there
  and answers nothing (`qzone:reply:begun`); otherwise a fresh deployment would reply to a
  month of comments in one burst.

Everything that costs a request is gated: a post whose `cmtnum` has not changed since the last
look is not fetched again, and a comment already in the table is never fetched twice.
"""

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core.live import source

from . import qzone as wall

POSTS = 3
"""How many of her recent 说说 are looked at. A comment on the fourth-newest post is old news."""

HOURLY = 6
"""Answers per hour.

A cap, because 「每条都回」 is a bot's idea. It is per **hour**, not per day: a day is
long enough that the cap turns into 「今天到他了，明天再说」—— 她的墙一晚上来十条，
第七个人要等到第二天早上，留言过夜就凉了。一小时是「别一下子回一串」的尺度，
不是「今天别说话了」的尺度。"""

GAP = 5.0
"""Minutes between two answers.

Five, not thirty. Thirty came from the version that answered *strangers'* walls, where a run
of comments reads as a bot working through a feed. Here the comments are messages left for
her on her own wall: somebody writes at 20:20 and a reply at 20:50 is not restraint, it is
being ignored. Five minutes still keeps two answers from landing as one burst."""

AGE = 72.0
"""Hours a comment stays answerable. Three days: a comment is a message, not a feed item, so
the window is wider than a 说说's — but answering last week's is not a conversation either."""

RECHECK = 30.0
"""Minutes before a post whose comment count has not moved is read again.

`cmtnum` on a 说说 counts **top-level comments only** — a reply written inside a thread does
not move it (measured: `cmtnum=1` with two 楼中楼 under it). So 「评论数没变就不看」 alone would
never notice somebody answering her answer. The count is still the fast path; this is the
slow one that makes the thread visible."""

RETRY_GAP = 2.0
UNSUPPORTED_TTL = 24.0
THROTTLE_GAP = 10.0 / 60.0
"""Hours to wait after the endpoint answered 500. QZone starts refusing writes after a few
comments in a row; the fix is to stop knocking, not to try the other endpoint immediately —
which is exactly what 「reply 失败就改成直接评论」 used to do, two writes in the same second."""

TRIES = 3

REPLY_FLOOR = 4
REPLY_LIMIT = 60
"""Characters in an answer. A comment back is a sentence."""

SKIP = "SKIP"
"""What the model says when the comment is not worth answering. One word, so the check is a
comparison rather than a judgement call on prose."""

BANNED = wall.BANNED

COARSE = (
    "约炮", "裸", "黄图", "色情", "性欲", "艹", "操你", "欠操", "傻逼", "智障", "去死", "滚",
    "nude", "naked", "porn", "sex",
)
"""Words that end the conversation before the model sees it. Not a content filter for the
account — a filter for *this* answer: she does not reply to these, and a model asked politely
would sometimes try."""

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS qzone_comments ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " post_tid TEXT NOT NULL,"
    " comment_tid TEXT NOT NULL,"
    " uin TEXT NOT NULL DEFAULT '',"
    " name TEXT NOT NULL DEFAULT '',"
    " content TEXT NOT NULL DEFAULT '',"
    " post_text TEXT NOT NULL DEFAULT '',"
    " at TEXT NOT NULL DEFAULT '',"
    " seen_at TEXT NOT NULL,"
    " state INTEGER NOT NULL DEFAULT 0,"
    " reply TEXT NOT NULL DEFAULT '',"
    " root_tid TEXT NOT NULL DEFAULT '',"
    " root_uin TEXT NOT NULL DEFAULT '',"
    " error TEXT NOT NULL DEFAULT '',"
    " tries INTEGER NOT NULL DEFAULT 0,"
    " replied_at TEXT NOT NULL DEFAULT '',"
    " UNIQUE(post_tid, comment_tid))",
    "ALTER TABLE qzone_comments ADD COLUMN root_tid TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE qzone_comments ADD COLUMN root_uin TEXT NOT NULL DEFAULT ''",
    "CREATE INDEX IF NOT EXISTS qzone_comments_waiting ON qzone_comments(state, at)",
    "CREATE INDEX IF NOT EXISTS qzone_comments_answered ON qzone_comments(state, replied_at)",
)

WAITING = 0
"""Seen, not answered yet."""
ANSWERED = 1
"""Answered. `reply` holds what was said."""
SKIPPED = 2
"""Not to be answered: it was already there before the bot started watching, she said nothing
to it, or it is a person on the ignore list."""


class ReplyError(RuntimeError):
    """`kind` follows `qzone.PublishError`: `unsupported`, `network`, `login`, `limited`,
    `rejected`."""

    def __init__(self, kind: str, detail: str = "") -> None:
        self.kind = kind
        self.detail = detail
        super().__init__(f"{kind}: {detail}" if detail else kind)


@dataclass(frozen=True)
class Comment:
    """One comment under one of her posts."""

    post_tid: str
    tid: str
    """Key in the table. For a reply inside a thread this is `<root>.<tid>`, because QZone
    numbers the two levels separately and `1` exists in both."""
    uin: str = ""
    name: str = ""
    content: str = ""
    at: datetime | None = None
    post_text: str = ""
    root: str = ""
    """The top-level comment this one hangs under; empty when it *is* top-level. A reply has
    to be anchored at the root — QZone ignores `commentId` pointing at a reply."""
    root_uin: str = ""


def _kind(exc: BaseException) -> str:
    kind = getattr(exc, "kind", "")
    return str(kind) if kind else wall._kind(exc)


def check(text: str) -> tuple[bool, str]:
    """`(may send, why not)`. Same refusals as a 说说, at a comment's length."""
    if not text:
        return False, "空的"
    if len(text) < REPLY_FLOOR:
        return False, f"太短（{len(text)} 字）"
    if len(text) > REPLY_LIMIT:
        return False, f"太长（{len(text)} 字）"
    if re.search(r"\d{5,}", text):
        return False, "带了一串数字（QQ 号、群号或电话）"
    if re.search(r"https?://|www\.|\.com|\.cn/|\.net", text, re.I):
        return False, "带了链接"
    if "@" in text:
        return False, "在评论里 @ 人"
    for word in BANNED:
        if word in text:
            return False, f"破功了（{word}）"
    return True, ""


def coarse(text: str) -> bool:
    """Whether this comment is the kind she does not answer at all."""
    low = text.lower()
    return any(word in low for word in COARSE)


class Wall:
    """Her posts, the comments under them, and whether to say anything back."""

    def __init__(
        self,
        client: Any,
        llm: Any,
        store: Any,
        *,
        web: Any = None,
        persona: str | Path | None = None,
        flags: wall.Flags | None = None,
        bots: Mapping[str, str] | None = None,
        self_id: str = "",
        deny: Sequence[str] = (),
        owners: Sequence[str] = (),
        hourly: int = HOURLY,
        gap: float = GAP,
        age: float = AGE,
        posts: int = POSTS,
        clock: Callable[[], datetime] | None = None,
        log: Callable[[str], None] | None = None,
        on: bool = True,
    ) -> None:
        self._client = client
        self._llm = llm
        self._store = store
        self._web = web
        self._src = source(persona) if persona else None
        self._bots = dict(bots or {})
        """QQ 号 → 那个角色在别的系统里的名字（`QQ_BOTS`）。它们不是制作人，是别的
        角色在说话；认错了，她会用「群里的另一位制作人」的口吻去接一句ルカ的台词。"""
        self._flags = flags or wall.Memory()
        self.self_id = str(self_id)
        self.deny = {str(one) for one in deny if str(one)}
        self.owners = {str(one) for one in owners if str(one)}
        """制作人。**不受一天几条的限制**：额度是防「有人在刷」的，不是让他的话排队到明天。
        间隔（`gap`）对他照旧——连着回两句仍然像在上班。"""
        self.hourly = max(int(hourly), 0)
        self.gap = max(float(gap), 0.0)
        self.age = max(float(age), 0.0)
        self.posts = max(int(posts), 1)
        self.on = bool(on)
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._log = log or (lambda line: None)
        self._last: dict[str, str] = {"why": "", "at": ""}
        self._uin = 0
        """Her own uin, learned from the first successful call: every reply needs it as
        `hostUin`, and 「是不是她自己发的」 needs it to filter her own comments."""

    # ---- what the page and the tests read

    def persona(self) -> str:
        return self._src.text() if self._src is not None else ""

    def recent(self, count: int = 8) -> list[dict[str, Any]]:
        rows = self._store.query(
            "SELECT * FROM qzone_comments ORDER BY seen_at DESC, at DESC LIMIT ?", (int(count),)
        )
        return [
            {
                "post_tid": str(row["post_tid"]),
                "comment_tid": str(row["comment_tid"]),
                "uin": str(row["uin"] or ""),
                "name": str(row["name"] or ""),
                "content": str(row["content"] or ""),
                "at": str(row["at"] or ""),
                "state": int(row["state"]),
                "reply": str(row["reply"] or ""),
                "error": str(row["error"] or ""),
            }
            for row in rows
        ]

    def status(self) -> dict[str, Any]:
        return {
            "on": self.on,
            "transport": "web" if self._web is not None else "action",
            "self": self.self_id or str(self._uin or ""),
            "hourly": self.hourly,
            "gap": self.gap,
            "age": self.age,
            "posts": self.posts,
            "last_hour": self._last_hour(),
            "seen": self._count("SELECT COUNT(*) AS n FROM qzone_comments"),
            "answered": self._count(
                "SELECT COUNT(*) AS n FROM qzone_comments WHERE state = ?", (ANSWERED,)
            ),
            # 「为什么不回这个人」的第一个答案：这里列出来的号一律不回。
            "ignore": sorted(self.deny),
            "paused": bool(self._flag("qzone:reply:pause")),
            "last_why": self._last["why"],
            "last_at": self._last["at"],
            "now": self._clock().isoformat(timespec="seconds"),
            "date": self._clock().date().isoformat(),
            "recent": self.recent(5),
        }

    # ---- one tick

    async def poll(self, now: datetime | None = None) -> dict[str, Any]:
        """Look at her wall once, then answer what is waiting."""
        now = now or self._clock()
        if not self.on:
            return self._skip("空间评论没开（QQ_QZONE_REPLY=0）", now)
        if not self.persona().strip():
            return self._skip("没有写作口径（characters/world/<id>-qzone.md）", now)
        if self._web is None:
            return self._skip("没有可用的读接口（QQ_QZONE_WEB=0 且 NapCat 没有 QZone 扩展）", now)
        if self._paused(now):
            return self._skip("上次打接口失败了，还在等", now)

        try:
            posts = await self._web.my_posts(self.posts)
        except Exception as exc:
            return self._fail(exc, now)

        self._uin = self._uin or self._web.self_id
        first = not self._flag("qzone:reply:begun")
        fresh = 0
        for post in posts:
            tid = str(post.get("tid") or "")
            if not tid:
                continue
            cmtnum = int(post.get("cmtnum") or 0)
            if cmtnum <= 0:
                continue  # 没人回的说说，不值得再发一个请求
            if not first and not self._due(tid, cmtnum, now):
                continue  # 评论数没变，而且刚看过
            try:
                comments = await self._web.comments(tid)
            except Exception as exc:
                return self._fail(exc, now)
            self._set_flag(f"qzone:wall:{tid}", f"{cmtnum}@{now.isoformat(timespec='seconds')}")
            for row in comments:
                comment = self._comment(tid, post, row, now)
                if comment is None:
                    continue
                if self._remember(comment, now, watch=not first):
                    fresh += 1

        if first:
            self._set_flag("qzone:reply:begun", now.isoformat(timespec="seconds"))
            self._note("第一次拉取，只记录不回", now)
            self._log(f"qq: 空间评论第一次拉取 —— 记下 {fresh} 条，这一轮不回")
            return {"ok": True, "posts": len(posts), "new": fresh, "replied": 0, "why": "第一次拉取"}

        answered: list[dict[str, Any]] = []
        for comment in self._waiting():
            if not self._may_reply(now, owner=comment.uin in self.owners):
                break
            text = await self.compose(comment)
            if text is None:
                continue  # 判定为不回（低俗 / 无话可说），已记在库里
            result = await self.answer(comment, text, now)
            if result.get("replied"):
                answered.append(result)
        if answered:
            self._log(f"qq: 空间回了 {len(answered)} 条评论（这一小时第 {self._last_hour()} 条）")
        return {
            "ok": True,
            "posts": len(posts),
            "new": fresh,
            "replied": len(answered),
            "said": [one.get("text", "") for one in answered],
        }

    def _due(self, tid: str, cmtnum: int, now: datetime) -> bool:
        """Whether this post is worth a request: the count moved, or it has been `RECHECK`."""
        stamp = self._flag(f"qzone:wall:{tid}")
        if not stamp:
            return True
        saved, _, at = stamp.partition("@")
        if saved != str(cmtnum):
            return True
        seen = wall._parse(at)
        return seen is None or (now - seen).total_seconds() > RECHECK * 60

    def _skip(self, why: str, now: datetime) -> dict[str, Any]:
        self._note(why, now)
        return {"ok": False, "why": why}

    def _fail(self, exc: BaseException, now: datetime) -> dict[str, Any]:
        kind = _kind(exc)
        hours = UNSUPPORTED_TTL if kind == "unsupported" else RETRY_GAP
        self._pause(now, hours, kind)
        self._log(f"qq: 空间评论这一轮拉不到 —— {kind}（{exc}）")
        return {"ok": False, "why": kind, "detail": str(exc)}

    def _note(self, why: str, now: datetime | None = None) -> None:
        at = now or self._clock()
        self._last = {"why": why, "at": at.isoformat(timespec="seconds")}

    # ---- which comments are candidates

    def _comment(
        self, post_tid: str, post: Mapping[str, Any], row: Mapping[str, Any], now: datetime
    ) -> Comment | None:
        uin = str(row.get("uin") or "")
        text = str(row.get("content") or "")
        if not uin or not str(row.get("tid") or ""):
            return None
        me = str(self.self_id or self._uin or "")
        if uin == me:
            return None  # 她自己回的，不用再回
        root = str(row.get("root") or "")
        if root and str(row.get("reply_to") or "") != me:
            # 楼中楼里别人之间的对话。她只接「对她说」的那一句 —— 一整条楼是
            # 群友互相接话，插进去既不礼貌，也会让她以为人家在跟她说话。
            return None
        if uin in self.deny:
            return None
        at = self._stamp(row.get("at"))
        if self.age and at is not None and (now - at).total_seconds() > self.age * 3600:
            return None
        tid = str(row.get("tid"))
        root = str(row.get("root") or "")
        return Comment(
            post_tid=post_tid,
            # 楼中楼的 tid 和一级评论各自编号，`1` 两边都有 —— 加上 root 才不会互相覆盖。
            tid=f"{root}.{tid}" if root else tid,
            uin=uin,
            name=str(row.get("name") or ""),
            content=text,
            at=at,
            post_text=str(post.get("content") or ""),
            root=root,
            root_uin=str(row.get("root_uin") or ""),
        )

    def _stamp(self, value: Any) -> datetime | None:
        try:
            seconds = int(float(value or 0))
        except (TypeError, ValueError):
            return None
        if seconds <= 0:
            return None
        try:
            return datetime.fromtimestamp(seconds, tz=self._clock().tzinfo)
        except (OSError, OverflowError, ValueError):
            return None

    def _waiting(self) -> list[Comment]:
        rows = self._store.query(
            "SELECT * FROM qzone_comments WHERE state = ? AND tries < ? ORDER BY at ASC LIMIT ?",
            (WAITING, TRIES, self.posts * 10),
        )
        waiting = [
            Comment(
                post_tid=str(row["post_tid"]),
                tid=str(row["comment_tid"]),
                uin=str(row["uin"] or ""),
                name=str(row["name"] or ""),
                content=str(row["content"] or ""),
                at=wall._parse(row["at"]),
                post_text=str(row["post_text"] or ""),
                root=str(row["root_tid"] or ""),
                root_uin=str(row["root_uin"] or ""),
            )
            for row in rows
        ]
        # 制作人的话排在最前面：一次只回一条（间隔），排在几条找茬后面的话，他要等到
        # 十几分钟以后。别人按时间先来先回。
        waiting.sort(key=lambda one: (0 if one.uin in self.owners else 1, one.at.timestamp() if one.at else 0))
        return waiting

    # ---- what to say

    async def compose(self, comment: Comment) -> str | None:
        """One answer, or `None` when this comment is not worth one.

        `None` is a decision, not a failure: it is written down as `SKIPPED` with the model's
        reason, so 「为什么不回」 has an answer later.
        """
        if coarse(comment.content):
            self._skip_comment(comment.tid, "低俗/性相关，不回")
            return None
        why = "没写出来"
        for _ in range(2):
            raw = await self._ask(comment)
            text = raw.strip()
            if text.upper().startswith(SKIP):
                self._skip_comment(comment.tid, "判定为不回")
                self._log(f"qq: 这条空间评论不回（{comment.content[:16]}）")
                return None
            text = wall._clean(text)
            ok, why = check(text)
            if ok:
                return text
        self._bump(comment.tid, why)
        self._log(f"qq: 空间这条回不出来（{why}）：{comment.content[:20]}")
        return None

    async def _ask(self, comment: Comment) -> str:
        try:
            chunks = [chunk async for chunk in self._llm.stream(self._messages(comment))]
        except Exception as exc:
            raise wall.ContentError(f"模型没回话（{type(exc).__name__}）") from exc
        return "".join(chunks)

    def _messages(self, comment: Comment) -> list[Any]:
        from core.llm import ChatMessage

        who = comment.name or comment.uin or "一个人"
        lines = [
            self.persona().strip(),
            "",
            "## 这一次",
            "有人在你的空间里，给你的一条说说留了评论。",
        ]
        if comment.post_text:
            lines.append(f"你那条说说：「{comment.post_text}」")
        other = self._bots.get(comment.uin)
        if other:
            lines.append(
                f"（这个号是「{other}」的机器人，用{other}的口吻说话。不是制作人，"
                f"也不是陌生人——你认识{other}，按认识的人接，别把它当成粉丝或路人。）"
            )
        lines += [
            f"{who} 评论说：「{comment.content}」",
            "",
            "回他一句，就一句。",
            f"{REPLY_FLOOR}～{REPLY_LIMIT} 字，一句话，不要引号、不要「回复：」这类标签。",
            "不要写「谢谢」「支持」「加油」这种谁都能说的客套话。",
            "不要提自己的身份，不要提空间以外的事。有人在看这条评论。",
            "这条评论可能是来找茬的、抬杠的、拿你跟别人比的——这些要回。损回去、给对方"
            "打个折、说一句带刺的话，都是你的本事；别客客气气地绕开。",
            f"只有这三种情况只输出 {SKIP}（四个字母，什么都别加）："
            "低俗或跟性有关的；骂人、人身攻击的；纯粹是复读或报错、没有一句话可接的。"
            "不回也是一种回答，但别把它用在只是嘴上不客气的人身上。",
        ]
        return [ChatMessage("system", "\n".join(lines))]

    # ---- saying it

    async def answer(self, comment: Comment, text: str, now: datetime | None = None) -> dict[str, Any]:
        """Post one answer that has already been written and checked."""
        now = now or self._clock()
        ok, why = check(text)
        if not ok:
            raise wall.ContentError(why)
        try:
            via = await self._send(comment, text)
        except Exception as exc:
            kind = _kind(exc)
            self._bump(comment.tid, str(exc))
            self._note(kind, now)
            if kind == "unsupported":
                self._pause(now, UNSUPPORTED_TTL, kind)
            elif kind in ("network", "limited"):
                self._pause(now, RETRY_GAP, kind)
            else:
                # 500 / 参数错误：十分钟后再敲。紧接着换一个端点重试只会撞第二次。
                self._pause(now, THROTTLE_GAP, kind)
            self._log(f"qq: 空间评论回不出去 —— {kind}（{exc}）")
            return {"replied": False, "why": kind, "text": text}
        self._store.execute(
            "UPDATE qzone_comments SET state = ?, reply = ?, error = '', replied_at = ?"
            " WHERE comment_tid = ? AND post_tid = ?",
            (ANSWERED, text, now.isoformat(timespec="seconds"), comment.tid, comment.post_tid),
        )
        self._note("回了一条", now)
        self._log(f"qq: 空间回了一条评论（{via}）—— 给 {comment.name or comment.uin}：{text}")
        return {"replied": True, "comment": comment.tid, "text": text, "via": via}

    async def _send(self, comment: Comment, text: str) -> str:
        """Put the answer under that person's comment — a 楼中楼, or nothing.

        No fallback to a plain comment: that is what the first version did, and it looked
        like success in the log while the answer hung under the post instead of under the
        person. A wrongly-placed answer cannot be taken back (there is no delete endpoint),
        so failing loudly is the only honest outcome.
        """
        host = self.self_id or str(self._uin)
        await self._web.reply(
            comment.post_tid,
            host,
            comment.tid.split(".")[-1],
            comment.uin,
            text,
            name=comment.name,
            root_tid=comment.root,
            root_uin=comment.root_uin,
        )
        return "reply"

    async def answer_tid(
        self, comment_tid: str, text: str = "", now: datetime | None = None
    ) -> dict[str, Any]:
        """The manual door (`POST /api/qzone/reply`): answer one known comment."""
        now = now or self._clock()
        rows = self._store.query(
            "SELECT * FROM qzone_comments WHERE comment_tid = ?", (str(comment_tid),)
        )
        if not rows:
            return {"replied": False, "why": "没有这条评论（先 POST /api/qzone/poll 拉一次）"}
        row = rows[0]
        comment = Comment(
            post_tid=str(row["post_tid"]),
            tid=str(row["comment_tid"]),
            uin=str(row["uin"] or ""),
            name=str(row["name"] or ""),
            content=str(row["content"] or ""),
            at=wall._parse(row["at"]),
            post_text=str(row["post_text"] or ""),
            root=str(row["root_tid"] or ""),
            root_uin=str(row["root_uin"] or ""),
        )
        if not text:
            text = await self.compose(comment)
            if text is None:
                return {"replied": False, "why": "判定为不回"}
        try:
            return await self.answer(comment, text, now)
        except wall.ContentError as exc:
            return {"replied": False, "why": f"不通过（{exc.why}）", "text": text}

    # ---- the small ones

    def _waiting_count(self) -> int:
        return self._count(
            "SELECT COUNT(*) AS n FROM qzone_comments WHERE state = ?", (WAITING,)
        )

    def _may_reply(self, now: datetime, *, owner: bool = False) -> bool:
        if self.hourly and not owner and self._last_hour() >= self.hourly:
            return False
        if self.gap:
            rows = self._store.query(
                "SELECT replied_at FROM qzone_comments WHERE state = ? AND replied_at != ''"
                " ORDER BY replied_at DESC LIMIT 1",
                (ANSWERED,),
            )
            last = wall._parse(rows[0]["replied_at"]) if rows else None
            if last is not None and (now - last).total_seconds() < self.gap * 60:
                return False
        return True

    def _remember(self, comment: Comment, now: datetime, *, watch: bool) -> bool:
        rows = self._store.query(
            "SELECT id FROM qzone_comments WHERE post_tid = ? AND comment_tid = ?",
            (comment.post_tid, comment.tid),
        )
        if rows:
            return False
        self._store.execute(
            "INSERT INTO qzone_comments (post_tid, comment_tid, uin, name, content, post_text,"
            " root_tid, root_uin, at, seen_at, state, error)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                comment.post_tid,
                comment.tid,
                comment.uin,
                comment.name,
                comment.content,
                comment.post_text,
                comment.root,
                comment.root_uin,
                comment.at.isoformat(timespec="seconds") if comment.at else "",
                now.isoformat(timespec="seconds"),
                WAITING if watch else SKIPPED,
                "" if watch else "第一次拉取前就有的",
            ),
        )
        return True

    def _skip_comment(self, comment_tid: str, why: str) -> None:
        self._store.execute(
            "UPDATE qzone_comments SET state = ?, error = ? WHERE comment_tid = ?",
            (SKIPPED, why[:200], comment_tid),
        )

    def _bump(self, comment_tid: str, why: str) -> None:
        self._store.execute(
            "UPDATE qzone_comments SET tries = tries + 1, error = ? WHERE comment_tid = ?",
            (why[:200], comment_tid),
        )

    def _last_hour(self) -> int:
        """Answers in the last sixty minutes — the number the cap is about.

        Rolling, not 「这个小时整点重置」: 21:59 回四条之后 22:00 又能回六条，那等于十
        分钟里回了十条，限额就没意义了。"""
        since = (self._clock() - timedelta(hours=1)).isoformat(timespec="seconds")
        return self._count(
            "SELECT COUNT(*) AS n FROM qzone_comments WHERE state = ? AND replied_at >= ?",
            (ANSWERED, since),
        )

    def _count(self, sql: str, params: Sequence[Any] = ()) -> int:
        rows = self._store.query(sql, tuple(params))
        return int(rows[0]["n"]) if rows else 0

    def _paused(self, now: datetime) -> bool:
        stamp = wall._parse(self._flag("qzone:reply:pause"))
        if stamp is None:
            return False
        hours = float(self._flag("qzone:reply:pause:ttl") or RETRY_GAP)
        if (now - stamp).total_seconds() > hours * 3600:
            self._set_flag("qzone:reply:pause", "")
            self._set_flag("qzone:reply:pause:ttl", "")
            return False
        return True

    def _pause(self, now: datetime, hours: float, why: str) -> None:
        self._set_flag("qzone:reply:pause", now.isoformat(timespec="seconds"))
        self._set_flag("qzone:reply:pause:ttl", f"{hours:g}")
        self._note(f"暂停 {hours:g} 小时（{why}）", now)

    def _flag(self, key: str) -> str:
        return str(self._flags.flag(key) or "")

    def _set_flag(self, key: str, value: str) -> None:
        self._flags.set_flag(key, value)


def dump(result: Mapping[str, Any]) -> str:
    """One line for the log."""
    return json.dumps(
        {
            "ok": result.get("ok"),
            "why": result.get("why", ""),
            "new": result.get("new"),
            "replied": result.get("replied"),
        },
        ensure_ascii=False,
    )
