"""Sending, and the three things that go wrong when sending.

A reply is several messages, and in a group each one is a separate chance to fail: the
socket is down, the platform refuses, the person was kicked out. This file owns the queue
that makes those failures recoverable, and the two courtesies that make a bot feel human:

- **Pacing.** Segments go out 0.8–1.5s apart, like someone typing. Sending five messages in
  40ms is how a bot gets muted.
- **Splitting.** One segment can still be longer than a comfortable message. Cuts land on
  sentence ends, never inside one.

The queue is in SQLite rather than in memory, because the interesting failure is a restart
halfway through a reply: the words are already written, and throwing them away because the
process died is the kind of thing that makes a character feel broken.
"""

import asyncio
import json
import random
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from core.store import Store

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS qq_outbox ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " group_id TEXT NOT NULL,"
    " body TEXT NOT NULL,"
    " mention TEXT NOT NULL DEFAULT '',"
    " reply_to TEXT NOT NULL DEFAULT '',"
    " state TEXT NOT NULL DEFAULT 'pending',"
    " attempts INTEGER NOT NULL DEFAULT 0,"
    " created_at REAL NOT NULL)",
    "CREATE INDEX IF NOT EXISTS qq_outbox_pending ON qq_outbox(state, id)",
)

BREAKS = "。！？…\n！？"
LIMIT = 900
"""Characters per message. QQ accepts far more; a wall of text is a wall of text."""

DM = "dm:"
"""Marks a private target inside the queue's target column. The column was written when there
was only one kind of chat; adding a prefix keeps the table — and anyone's existing rows —
working, where a second column would have meant a migration on a live database."""

Call = Callable[..., Awaitable[dict[str, Any]]]
"""`await call("send_group_msg", group_id=..., message=[...])` — the OneBot client's shape."""

Keep = Callable[[str, str], None]
"""`keep(message_id, text)` — remember what went out under which platform id.

QQ answers a send with an id, and that id is the only thing a later 「引用」 carries: the
quote segment says *which* message, not what it said. Without this the channel can read a
reply to her words and still have no idea what her words were."""


class Outbox:
    def __init__(
        self,
        store: Store,
        call: Call,
        *,
        limit: int = LIMIT,
        gap: tuple[float, float] = (0.8, 1.5),
        retries: int = 2,
        resume_gap: float = 3.0,
        log: Callable[[str], None] | None = None,
        keep: Keep | None = None,
    ) -> None:
        self._store = store
        self._call = call
        self._limit = limit
        self._gap = gap
        self._retries = retries
        self._resume_gap = resume_gap
        self._log = log or (lambda line: None)
        self._keep = keep
        self._last = 0.0
        self.sent = 0
        self.failed = 0

    def migrations(self) -> Sequence[str]:
        return SCHEMA

    def keep(self, fn: Keep | None) -> None:
        """Who hears about the ids from here on.

        Set after construction as well as in it, because a channel can be handed an Outbox
        somebody else built (the QQ plugin does exactly that) — and an id nobody wrote down
        is a «引用» that arrives as 「一条你没看到的消息」.
        """
        self._keep = fn

    async def send(
        self,
        group_id: str,
        text: str,
        *,
        private: bool = False,
        mention: str = "",
        reply_to: str = "",
        persist: bool = True,
    ) -> int:
        """Send one reply's worth of text; returns how many messages it became.

        `group_id` is the room, or the person when `private`. A failed chunk is kept as
        `pending` and does not raise: the rest of the reply still deserves to arrive, and
        `resend_pending` picks the failure up after a reconnect.
        """
        mention = "" if private else mention
        reply_to = "" if private else reply_to
        chunks = split(text, self._limit)
        for index, chunk in enumerate(chunks):
            at = mention if index == 0 else ""
            quote = reply_to if index == 0 else ""
            row_id = self._add(group_id, chunk, at, quote, private) if persist else 0
            await self._pace()
            try:
                delivered = await self._deliver(group_id, chunk, at, quote, private)
            except Exception as exc:
                self.failed += 1
                if row_id:
                    self._store.execute(
                        "UPDATE qq_outbox SET attempts = attempts + 1 WHERE id = ?", (row_id,)
                    )
                self._log(f"outbox: send failed ({type(exc).__name__}: {exc})")
                continue
            if delivered and self._keep is not None:
                # Before the queue row is dropped: from here on this sentence is quotable,
                # and 「引用」 is the only way she ever hears her own words back.
                self._keep(delivered, chunk)
            if row_id:
                self._store.execute("DELETE FROM qq_outbox WHERE id = ?", (row_id,))
            self.sent += 1
        return len(chunks)

    async def resend_pending(self) -> int:
        """Whatever the last run did not manage to say, oldest first, slowly."""
        rows = self._store.query(
            "SELECT id, group_id, body, mention, reply_to, attempts FROM qq_outbox"
            " WHERE state = 'pending' AND attempts < 5 ORDER BY id"
        )
        done = 0
        for row in rows:
            await asyncio.sleep(self._resume_gap)
            target, private = _decode(str(row["group_id"]))
            try:
                delivered = await self._deliver(
                    target,
                    str(row["body"]),
                    str(row["mention"]),
                    str(row["reply_to"]),
                    private,
                )
            except Exception:
                self._store.execute(
                    "UPDATE qq_outbox SET attempts = attempts + 1 WHERE id = ?", (row["id"],)
                )
                continue
            if delivered and self._keep is not None:
                self._keep(delivered, str(row["body"]))
            self._store.execute("DELETE FROM qq_outbox WHERE id = ?", (row["id"],))
            done += 1
        if done:
            self._log(f"outbox: resent {done} message(s) left over from the last run")
        return done

    async def send_image(self, target: str, file: str, *, private: bool = False) -> bool:
        """One picture, no text. `file` is `base64://…` — the only form that works when the
        bytes live outside NapCat's container. Not queued: a sticker that could not be sent
        is not worth replaying three minutes later."""
        await self._pace()
        try:
            await self._push(target, [{"type": "image", "data": {"file": file}}], private)
        except Exception as exc:
            self.failed += 1
            self._log(f"outbox: sticker failed ({type(exc).__name__}: {exc})")
            return False
        self.sent += 1
        return True

    async def _deliver(
        self, target: str, text: str, mention: str, reply_to: str, private: bool = False
    ) -> str:
        """Send one bubble; returns the platform's id for it, or "" if it answered with none."""
        message: list[dict[str, Any]] = []
        if reply_to:
            message.append({"type": "reply", "data": {"id": reply_to}})
        if mention:
            message.append({"type": "at", "data": {"qq": mention}})
            message.append({"type": "text", "data": {"text": " "}})
        message.append({"type": "text", "data": {"text": text}})
        return await self._push(target, message, private)

    async def _push(
        self, target: str, message: list[dict[str, Any]], private: bool, *, retries: int | None = None
    ) -> str:
        last: Exception | None = None
        for attempt in range((self._retries if retries is None else retries) + 1):
            try:
                if private:
                    data = await self._call("send_private_msg", user_id=target, message=message)
                else:
                    data = await self._call("send_group_msg", group_id=target, message=message)
                return _said_id(data)
            except Exception as exc:
                last = exc
                await asyncio.sleep(0.4 * (attempt + 1))
        raise last or RuntimeError("send failed")

    async def _pace(self) -> None:
        """At least `gap[0]` since the last message, plus a little jitter."""
        wait = self._gap[0] + random.random() * (self._gap[1] - self._gap[0])
        since = time.monotonic() - self._last
        if since < wait:
            await asyncio.sleep(wait - since)
        self._last = time.monotonic()

    def _add(self, target: str, body: str, mention: str, reply_to: str, private: bool) -> int:
        self._store.execute(
            "INSERT INTO qq_outbox (group_id, body, mention, reply_to, state, created_at)"
            " VALUES (?, ?, ?, ?, 'pending', ?)",
            (_encode(target, private), body, mention, reply_to, time.time()),
        )
        row = self._store.query("SELECT MAX(id) AS id FROM qq_outbox")[0]
        return int(row["id"] or 0)


def _encode(target: str, private: bool) -> str:
    return f"{DM}{target}" if private else target


def _decode(stored: str) -> tuple[str, bool]:
    if stored.startswith(DM):
        return stored[len(DM):], True
    return stored, False


def _said_id(data: Any) -> str:
    """The id a send was filed under, as text.

    OneBot v11 answers `{"message_id": 12345}`; some builds write it as a string, and a
    server with nothing to say answers `{}` — which is not an error, only an id we do not
    have. Numbers become text because the id is used as a dictionary key and as a CQ
    parameter, and 「引用」 gives it to us as text anyway: comparing `123` to `"123"` would
    silently never match.
    """
    if not isinstance(data, dict):
        return ""
    for key in ("message_id", "messageId"):
        value = data.get(key)
        if value not in (None, "", 0):
            return str(value)
    return ""


def split(text: str, limit: int = LIMIT) -> list[str]:
    """Cut into messages on sentence ends, never inside one."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(window.rfind(mark) for mark in BREAKS)
        cut = cut + 1 if cut >= limit // 2 else limit
        piece = rest[:cut].strip()
        if piece:
            out.append(piece)
        rest = rest[cut:].strip()
    if rest:
        out.append(rest)
    return out


def image_segment(name_or_url: str) -> dict[str, Any]:
    return {"type": "image", "data": {"file": name_or_url}}


def record_segment(path: str) -> dict[str, Any]:
    return {"type": "record", "data": {"file": path}}


BRACKETED = re.compile(r"(\[[^\[\]]{1,24}\])")
"""The shape of every marker `plain_text` writes. The group is deliberate: `re.split` keeps
what it captures, and folding runs needs the markers and the words between them apart."""

MOST_IN_A_ROW = 3
"""The same face four times over is one act, not four. A run stops being information — and
somebody mashing one button forty times (it happens) must not cost forty times the tokens."""


def plain_text(
    message: list[dict[str, Any]],
    *,
    self_id: str = "",
    name_of: Callable[[str], str] | None = None,
    quote_of: Callable[[str], str] | None = None,
) -> str:
    """The words of an event, with the reply header dropped — every face and every @ left
    behind as something readable in its place.

    Faces used to be dropped on the floor, which had two consequences. A message that was
    *only* a face arrived as an empty string and was discarded before it cost anything, so
    somebody who answered her with a sticker got nothing back — which reads as being
    ignored, not as being quiet. And a face in the middle of a sentence vanished, leaving a
    sentence that said less than the person wrote.

    **@-mentions were dropped the same way, and worse.** Somebody wrote 「没关系，有@愛美
    请客」 and she received 「没关系，有 请客」: not a shorter sentence but an *unanswerable*
    one — 「请客的是你」 was her best guess, and it was wrong. So an @ of somebody *else*
    stays in the text as 「@名字」; only the @ of herself is dropped, because that one is the
    address rather than a word (being called is `mentions`, and the sentence is read without
    it — 「@ふゆ /好感」 is a command).

    `name_of` turns a QQ number into what that person is called in the room (the channel
    passes `Identities.card`, which knows the cards it has seen). When nobody knows it yet,
    the number itself is left behind: 「@123456」 is still a fact — somebody pointed at that
    person — while a missing name is not.

    `face` is a QQ emoji (小黄脸), sent by id; a few of them carry a name in `raw`.
    `mface` is a favourited or store face, and what it carries is a `summary`. Neither is a
    picture we can show her, so the marker is the whole of what she gets: she knows one was
    sent, and that is the part that matters for answering.
    """
    out: list[str] = []
    for segment in message:
        kind = segment.get("type")
        data = segment.get("data") or {}
        if kind == "text":
            out.append(str(data.get("text", "")))
        elif kind == "at":
            qq = str(data.get("qq") or "")
            if qq and qq == str(self_id):
                continue  # 那是「叫她」，不是她要说的话
            out.append("@" + _at_name(qq, data, name_of))
        elif kind == "reply":
            # 「引用」也是内容，不是排版：有人按着某一条说「这句什么意思」，只收到「这句
            # 什么意思」的话，她只能猜那句是哪句。引用段只带被引用消息的 id，所以内容得问
            # `quote_of`（频道去自己记得的那几条里找）。找不到就明说找不到，别让她编。
            quoted = ""
            if quote_of is not None:
                try:
                    quoted = str(quote_of(str(data.get("id") or "")) or "").strip()
                except Exception:  # 查不到一条旧消息，不该让整句失败
                    quoted = ""
            out.append(f"（引用：「{_shorten(quoted)}」）" if quoted else _UNSEEN_QUOTE)
        elif kind == "face":
            out.append(_face(data))
        elif kind == "mface":
            # NapCat sends 「[动画表情]」 here; a store face with a name sends the name.
            out.append(str(data.get("summary") or "[动画表情]").strip() or "[动画表情]")
    return _squeeze("".join(out)).strip()


def _face(data: dict[str, Any]) -> str:
    """`[表情]`, or `[表情 偷感]` for the ones that carry a name."""
    raw = str(data.get("raw") or "").strip()
    named = raw.strip("[]").strip()
    if named.startswith("表情"):
        named = named[len("表情") :].strip()
    return f"[表情 {named}]" if named else "[表情]"


_UNSEEN_QUOTE = "（引用了一条你没看到的消息，不要猜内容）"
"""引用段只带被引用那条的 id。id 也查不到时，宁可告诉她「没看到」，也不让她补一个出来——
猜一句别人的话，比不接更糟。"""


def _shorten(text: str, limit: int = 60) -> str:
    line = " ".join((text or "").split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _at_name(
    qq: str, data: dict[str, Any], name_of: Callable[[str], str] | None = None
) -> str:
    """@ 后面那个名字：segment 自带的名字 → 频道见过的名片 → 号码本身。

    三段是有顺序的：NapCat 有时把名字放在 `data.name`，更多时候只给号码；号码要靠
    `Identities` 的名片去换（那个群里说过话的人才有）。都没有时留号码——「@123456」
    仍然是一个事实（他圈了这个人），而丢掉名字等于那句句子少一个成分。
    """
    if qq == "all":
        return "全体成员"
    given = str(data.get("name") or data.get("card") or "").strip()
    if given:
        return given
    if qq and name_of is not None:
        try:
            known = str(name_of(qq) or "").strip()
        except Exception:  # 名片查不到只是少一个名字，不能让整句消息失败
            known = ""
        if known:
            return known
    return qq or "某人"


def _squeeze(text: str) -> str:
    """Fold `[表情][表情][表情][表情][表情]` into three. Only consecutive *identical*
    markers collapse: a run of different faces is somebody saying something."""
    out: list[str] = []
    last = ""
    run = 0
    for piece in BRACKETED.split(text):
        if not piece:
            continue
        if BRACKETED.fullmatch(piece):
            if piece == last:
                run += 1
                if run > MOST_IN_A_ROW:
                    continue
            else:
                last, run = piece, 1
        else:
            last, run = "", 0
        out.append(piece)
    return "".join(out)


def faces(text: str) -> bool:
    """Whether a line mentions a face. The turn says what the markers mean only when there
    is one — an explanation on every message is a line of tokens no turn needs."""
    return any(
        marker.startswith(("[表情", "[动画表情")) for marker in BRACKETED.findall(text)
    )


def mentions(message: list[dict[str, Any]], self_id: str) -> bool:
    return any(
        segment.get("type") == "at" and str(segment.get("data", {}).get("qq")) == str(self_id)
        for segment in message
    )


def images(message: list[dict[str, Any]]) -> list[str]:
    """Picture URLs in an event, in order. `file` is a path inside NapCat's container and is
    not reachable from here, so only the URL is of any use."""
    out: list[str] = []
    for segment in message:
        if segment.get("type") != "image":
            continue
        data = segment.get("data", {})
        url = str(data.get("url") or "")
        if url.startswith(("http://", "https://")):
            out.append(url)
    return out


def dump(message: Sequence[dict[str, Any]]) -> str:
    """For logs: the shape of an event without its payloads."""
    return json.dumps([segment.get("type") for segment in message], ensure_ascii=False)
