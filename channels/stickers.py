"""The group's own stickers, kept and handed back.

Nothing is drawn here, and nothing is bundled: the pack has two sources, both of them already
in QQ — the pictures people in the group send, and the faces this account has favourited
(`channel.import_faces` pulls those through `fetch_custom_face`). A sticker that arrives once
is saved by content hash, listed in `qq_stickers`, and can be sent back later — which is how a
person actually uses stickers in a group, and which keeps her reacting with the pictures
everyone already recognises.

Two things need deciding for a sticker, and neither can be guessed from the bytes:

- **Is it a sticker at all?** A market face (`mface`) always is; an `image` sometimes is, and
  OneBot marks those with `sub_type: 1` / `summary: "[动画表情]"`. Both are accepted, because
  NapCat reports the same 表情包 either way depending on where it came from.
- **What does it mean?** Nothing here tries. A tag is given by a person (`/表情标 上班`), and
  an untagged sticker can still be sent at random. Guessing from pixels would be a lie with
  good formatting.

`qq_flags` is the tiny key-value store for "this has already happened" — currently the
Monday sticker, so a restart does not send it twice.
"""

import base64
import hashlib
import json
import random
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

KINDS = ("1", "sticker")
"""`sub_type` values OneBot uses for a picture that is really a 表情包."""

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS qq_stickers ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " sha1 TEXT NOT NULL UNIQUE,"
    " path TEXT NOT NULL,"
    " group_id TEXT NOT NULL DEFAULT '',"
    " sender TEXT NOT NULL DEFAULT '',"
    " tag TEXT NOT NULL DEFAULT '',"
    " created_at REAL NOT NULL)",
    "CREATE INDEX IF NOT EXISTS qq_stickers_tag ON qq_stickers(tag, id)",
    "CREATE TABLE IF NOT EXISTS qq_flags ("
    " key TEXT PRIMARY KEY,"
    " value TEXT NOT NULL DEFAULT '',"
    " updated_at TEXT NOT NULL)",
)

MAX_BYTES = 3 * 1024 * 1024
"""A sticker is small. Above this it is a photo, and storing photos here would fill a disk."""

SUFFIX = {"\xff\xd8\xff": "jpg", "\x89PNG\r\n\x1a\n": "png", "GIF8": "gif", "RIFF": "webp"}


class Pack:
    def __init__(
        self,
        store: Any,
        root: str | Path,
        *,
        rng: random.Random | None = None,
        max_bytes: int = MAX_BYTES,
        log: Any | None = None,
    ) -> None:
        self._store = store
        self._root = Path(root)
        self._rng = rng or random.Random()
        self._max = max_bytes
        self._log = log or (lambda line: None)

    def migrations(self) -> Sequence[str]:
        return SCHEMA

    # ---- arriving

    def save(self, raw: bytes, *, group: str = "", sender: str = "") -> dict[str, Any] | None:
        """Keep one sticker, once. Returns the row it belongs to, or `None` if it is not a
        picture, is too big, or was already here."""
        if not raw or len(raw) > self._max:
            return None
        suffix = _suffix(raw)
        if suffix is None:
            return None
        sha1 = hashlib.sha1(raw).hexdigest()
        found = self._store.query("SELECT * FROM qq_stickers WHERE sha1 = ?", (sha1,))
        if found:
            return dict(found[0])
        folder = self._root / "harvest"
        folder.mkdir(parents=True, exist_ok=True)
        path = (folder / f"{sha1}.{suffix}").resolve()
        if not path.exists():
            path.write_bytes(raw)
        self._store.execute(
            "INSERT INTO qq_stickers (sha1, path, group_id, sender, tag, created_at)"
            " VALUES (?, ?, ?, ?, '', ?)",
            (sha1, str(path), group, sender, time.time()),
        )
        row = self._store.query("SELECT * FROM qq_stickers WHERE sha1 = ?", (sha1,))
        return dict(row[0]) if row else None

    def adopt(self, row: dict[str, Any], *, group: str = "", sender: str = "") -> dict[str, Any]:
        """A sticker that was already here has now appeared in a room.

        The favourites imported from the account (`import_faces`) have no room — they belong to
        the account, not to a group. But `/表情标` names 「the newest one in *this* room」, on
        purpose, so an imported face could never be tagged: the producer would send it in the
        group and nothing would change, because the row already existed. Filling an empty
        `group_id` when the picture is finally seen in a room is the smallest fix that keeps
        both rules — the room is remembered, and where it was first seen is not overwritten.
        """
        if str(row.get("group_id") or ""):
            return row
        self._store.execute(
            "UPDATE qq_stickers SET group_id = ?, sender = ? WHERE sha1 = ?",
            (group, sender or str(row.get("sender") or ""), row["sha1"]),
        )
        return {**row, "group_id": group, "sender": sender or row.get("sender", "")}

    def prune(self) -> int:
        """Forget rows whose file is gone — a rebuild before the pack lived on a volume, or
        someone emptying the folder by hand. A row pointing at nothing would make `/表情`
        answer 「ないの」 while the count says otherwise."""
        rows = self._store.query("SELECT id, path FROM qq_stickers")
        gone = [int(row["id"]) for row in rows if not Path(str(row["path"])).exists()]
        for row_id in gone:
            self._store.execute("DELETE FROM qq_stickers WHERE id = ?", (row_id,))
        if gone:
            self._log(f"stickers: dropped {len(gone)} row(s) with no file")
        return len(gone)

    # ---- going out

    def count(self, tag: str = "") -> int:
        if tag:
            row = self._store.query("SELECT COUNT(*) AS n FROM qq_stickers WHERE tag = ?", (tag,))
        else:
            row = self._store.query("SELECT COUNT(*) AS n FROM qq_stickers")
        return int(row[0]["n"])

    def tagged(self, tag: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self._store.query(
                "SELECT * FROM qq_stickers WHERE tag = ? ORDER BY id DESC", (tag,)
            )
        ]

    def favourites(self) -> int:
        """How many of these came from the account's own collection (`group_id` empty)."""
        row = self._store.query(
            "SELECT COUNT(*) AS n FROM qq_stickers WHERE group_id = ''"
        )
        return int(row[0]["n"])

    def pick(self, tag: str = "", *, source: str = "") -> dict[str, Any] | None:
        """Random among the stickers with this tag, or among all of them when no tag is
        asked for. A named tag that has nothing behind it returns `None` rather than
        something else — 「/表情 上班」 sending a random cat would be a lie.

        `source` is which half of the pack to draw from, and it matters: `fav` is the
        fourteen pictures the account kept for itself, `group` is the hundred and seventy
        other people happened to send. Drawing from the whole pack is what made her own
        collection effectively unreachable — at 14/184 it is a 7% chance per draw, so the
        feature looked broken while working exactly as written.

        A tag still wins over `source`: naming 「上班」 is about the picture, not about where
        it came from.
        """
        if tag:
            rows = self.tagged(tag)
        elif source == "fav":
            rows = [
                dict(row)
                for row in self._store.query("SELECT * FROM qq_stickers WHERE group_id = ''")
            ]
        elif source == "group":
            rows = [
                dict(row)
                for row in self._store.query("SELECT * FROM qq_stickers WHERE group_id != ''")
            ]
        else:
            rows = [dict(row) for row in self._store.query("SELECT * FROM qq_stickers")]
        rows = [row for row in rows if Path(str(row["path"])).exists()]
        return self._rng.choice(rows) if rows else None

    def latest(self) -> dict[str, Any] | None:
        rows = self._store.query("SELECT * FROM qq_stickers ORDER BY id DESC LIMIT 1")
        return dict(rows[0]) if rows else None

    def tag(self, tag: str, *, sha1: str = "", group: str = "") -> dict[str, Any] | None:
        """Name a sticker. Without a hash it is the newest one — the picture someone just
        posted before typing 「/表情标 上班」. `group` narrows that to one room, because in a
        busy group the newest sticker overall is usually somebody else's."""
        if sha1:
            row = self._store.query("SELECT * FROM qq_stickers WHERE sha1 = ?", (sha1,))
        elif group:
            row = self._store.query(
                "SELECT * FROM qq_stickers WHERE group_id = ? ORDER BY id DESC LIMIT 1", (group,)
            )
        else:
            row = self._store.query("SELECT * FROM qq_stickers ORDER BY id DESC LIMIT 1")
        if not row:
            return None
        self._store.execute("UPDATE qq_stickers SET tag = ? WHERE id = ?", (tag, row[0]["id"]))
        return {**dict(row[0]), "tag": tag}

    def data_url(self, row: dict[str, Any]) -> str:
        """`base64://…` — the one form of `image` a sticker kept outside NapCat's container
        can be sent in. A filesystem path would only be valid inside that container."""
        raw = Path(str(row["path"])).read_bytes()
        return "base64://" + base64.b64encode(raw).decode("ascii")

    # ---- the tiny flag store

    def flag(self, key: str) -> str:
        rows = self._store.query("SELECT value FROM qq_flags WHERE key = ?", (key,))
        return str(rows[0]["value"]) if rows else ""

    def set_flag(self, key: str, value: str) -> None:
        self._store.execute(
            "INSERT INTO qq_flags (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, time.strftime("%Y-%m-%dT%H:%M:%S%z")),
        )


def _suffix(raw: bytes) -> str | None:
    for magic, suffix in SUFFIX.items():
        if raw.startswith(magic.encode("latin-1")):
            return suffix
    return None


def is_sticker(segment: dict[str, Any]) -> bool:
    kind = segment.get("type")
    if kind == "mface":
        return True
    if kind != "image":
        return False
    data = segment.get("data") or {}
    if str(data.get("sub_type") or "") in KINDS:
        return True
    return "动画表情" in str(data.get("summary") or "")


def urls(message: Sequence[dict[str, Any]]) -> list[str]:
    """Where the sticker bytes can be fetched from. `file` is a path inside NapCat's
    container; only the URL is reachable from this process."""
    out: list[str] = []
    for segment in message:
        if not is_sticker(segment):
            continue
        data = segment.get("data") or {}
        url = str(data.get("url") or data.get("file") or "")
        if url.startswith(("http://", "https://")):
            out.append(url)
    return out


def dump(row: dict[str, Any] | None) -> str:
    return json.dumps(
        {k: row[k] for k in ("sha1", "tag", "sender") if k in row} if row else {}, ensure_ascii=False
    )
