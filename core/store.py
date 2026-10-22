"""SQLite: three tables, one connection, one lock.

`users`, `conversations`, `messages` — nothing else is the kernel's business. A plugin
keeps its own tables and hands the kernel `CREATE TABLE IF NOT EXISTS` to run at startup,
so stopping a plugin never drops what it knew.

One connection and a global lock is upstream's choice and stays: it is enough for one
small machine and a handful of users, and it is the reason there is no migration framework here.
The schema is created on open and repaired in place, the same blunt way upstream does it.
"""

import json
import sqlite3
import threading
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .messages import Conversation, Message
from .protocol import Segment
from .protocol.cues import without_cues

SCHEMA = """\
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    character_id TEXT NOT NULL,
    title TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    segments TEXT NOT NULL DEFAULT '[]',
    photo_tag TEXT,
    photo_failed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_by_conversation ON messages(conversation_id, seq);
"""


class Store:
    def __init__(self, path: str | Path) -> None:
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.execute("PRAGMA journal_mode=WAL")  # as the deployed database is
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def migrate(self, statements: Sequence[str]) -> None:
        """Run a plugin's own `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ADD COLUMN`, in
        one transaction. The plugin owns the table; the kernel only runs the statement.

        A column that is already there is not an error: a plugin that adds one states both
        the table with it and the `ALTER` that puts it into a database from before, and the
        second is expected to fail on every database created since.
        """
        with self._lock, self._db:
            for statement in statements:
                try:
                    self._db.execute(statement)
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        """Rows of a plugin's own table. The same connection and lock as everything else:
        a plugin does not get its own, because two writers to one file is the thing the
        whole file avoids."""
        with self._lock:
            return self._db.execute(sql, tuple(params)).fetchall()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        with self._lock, self._db:
            self._db.execute(sql, tuple(params))

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ---- what a conversation is

    def create_user(self, name: str) -> str:
        id = _id()
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO users (id, name, created_at) VALUES (?, ?, ?)", (id, name, _now())
            )
        return id

    def create_conversation(
        self, user_id: str, character_id: str, title: str | None = None
    ) -> Conversation:
        conversation = Conversation(_id(), user_id, character_id, _now(), title)
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO conversations (id, user_id, character_id, title, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    conversation.id,
                    conversation.user_id,
                    conversation.character_id,
                    conversation.title,
                    conversation.created_at,
                ),
            )
        return conversation

    def conversation(self, id: str) -> Conversation | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM conversations WHERE id = ?", (id,)).fetchone()
        return _conversation(row) if row else None

    # ---- what was said

    def messages(self, conversation_id: str) -> list[Message]:
        """Every message of the conversation, oldest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY seq", (conversation_id,)
            ).fetchall()
        return [_message(row) for row in rows]

    def message(self, id: str) -> Message | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM messages WHERE id = ?", (id,)).fetchone()
        return _message(row) if row else None

    def add_turn(
        self,
        conversation_id: str,
        text: str,
        segments: Sequence[Segment],
        photo_tag: str | None = None,
        photo_failed: bool = False,
    ) -> str:
        """Store what the user said and what she said back, in one transaction, and return
        the id of her reply. Both or neither: a turn that failed leaves nothing behind, so
        the words can be sent again."""
        reply_id = _id()
        now = _now()
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO messages (id, conversation_id, role, text, created_at)"
                " VALUES (?, ?, 'user', ?, ?)",
                (_id(), conversation_id, text, now),
            )
            self._db.execute(
                "INSERT INTO messages (id, conversation_id, role, segments, photo_tag,"
                " photo_failed, created_at) VALUES (?, ?, 'assistant', ?, ?, ?, ?)",
                (
                    reply_id,
                    conversation_id,
                    json.dumps([[s.ja, s.zh] for s in segments], ensure_ascii=False),
                    photo_tag,
                    int(photo_failed),
                    now,
                ),
            )
        return reply_id


def _id() -> str:
    return uuid.uuid4().hex[:16]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conversation(row: sqlite3.Row) -> Conversation:
    return Conversation(
        id=row["id"],
        user_id=row["user_id"],
        character_id=row["character_id"],
        created_at=row["created_at"],
        title=row["title"],
    )


def _message(row: sqlite3.Row) -> Message:
    """A row as a message, as people see it: a reply is kept as the model wrote it,
    delivery cues and all (upstream ADR-0007), but the cues are stripped on the way out
    — only speech synthesis ever hears them."""
    segments = tuple(Segment(ja, zh) for ja, zh in json.loads(row["segments"] or "[]"))
    return Message(
        id=row["id"],
        conversation_id=row["conversation_id"],
        role=row["role"],
        text=row["text"],
        segments=tuple(
            Segment(
                None if seg.ja is None else without_cues(seg.ja),
                None if seg.zh is None else without_cues(seg.zh),
            )
            for seg in segments
        ),
        photo_tag=row["photo_tag"],
        photo_failed=bool(row["photo_failed"]),
        created_at=row["created_at"],
    )
