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
    ) -> None:
        self._store = store
        self._call = call
        self._limit = limit
        self._gap = gap
        self._retries = retries
        self._resume_gap = resume_gap
        self._log = log or (lambda line: None)
        self._last = 0.0
        self.sent = 0
        self.failed = 0

    def migrations(self) -> Sequence[str]:
        return SCHEMA

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
                await self._deliver(group_id, chunk, at, quote, private)
            except Exception as exc:
                self.failed += 1
                if row_id:
                    self._store.execute(
                        "UPDATE qq_outbox SET attempts = attempts + 1 WHERE id = ?", (row_id,)
                    )
                self._log(f"outbox: send failed ({type(exc).__name__}: {exc})")
                continue
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
                await self._deliver(
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
    ) -> None:
        message: list[dict[str, Any]] = []
        if reply_to:
            message.append({"type": "reply", "data": {"id": reply_to}})
        if mention:
            message.append({"type": "at", "data": {"qq": mention}})
            message.append({"type": "text", "data": {"text": " "}})
        message.append({"type": "text", "data": {"text": text}})
        await self._push(target, message, private)

    async def _push(
        self, target: str, message: list[dict[str, Any]], private: bool, *, retries: int | None = None
    ) -> None:
        last: Exception | None = None
        for attempt in range((self._retries if retries is None else retries) + 1):
            try:
                if private:
                    await self._call("send_private_msg", user_id=target, message=message)
                else:
                    await self._call("send_group_msg", group_id=target, message=message)
                return
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


def plain_text(message: list[dict[str, Any]]) -> str:
    """The words of an event, with the @-mentions and the reply header dropped — and with
    every face left behind as something readable in its place.

    Faces used to be dropped on the floor, which had two consequences. A message that was
    *only* a face arrived as an empty string and was discarded before it cost anything, so
    somebody who answered her with a sticker got nothing back — which reads as being
    ignored, not as being quiet. And a face in the middle of a sentence vanished, leaving a
    sentence that said less than the person wrote.

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
