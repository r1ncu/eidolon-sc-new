"""Pictures the producer sends, and the hour's allowance for them.

Upstream keeps photos in two directions: hers (a `photo_tag` in the reply, rendered by an
image service) and the producer's (an upload that the model sees). This file does the
producer's, because that is the direction the deployed key can actually do: the chat model
takes images, and there is no image model configured.

What it owns:

- **the bytes.** A picture arrives as a data URL, is decoded, checked (a real image type, a
  size ceiling) and written under `SCCORE_MEDIA`. What the model gets is the picture again,
  as a part of the user's message; what the history gets is the text beside it.
- **the allowance.** A sliding hour, charged only when the turn is stored, exactly like
  `quota` — a dropped line costs nothing. The count lives in this plugin's table so
  `quota`'s arithmetic stays about turns.

The kernel hands the ids over in `facts["photos.ids"]` before the gates, which is why a
gate can see them at all: the gate runs before anybody has built a prompt.
"""

import base64
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.plugin import Plugin, Route, TurnContext, Veto
from core.store import Store

PER_HOUR = 8
MAX_BYTES = 6 * 1024 * 1024
MAX_PER_TURN = 4
WINDOW = timedelta(hours=1)

_DATA_URL = re.compile(r"^data:image/(jpeg|png|webp);base64,(.+)$", re.S | re.I)

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS photo_uses ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " user_id TEXT NOT NULL,"
    " at REAL NOT NULL)",
    "CREATE INDEX IF NOT EXISTS photo_uses_by_user ON photo_uses(user_id, at)",
)


class Photos(Plugin):
    name = "photos"

    def __init__(
        self,
        store: Store,
        media: str | Path,
        *,
        per_hour: int = PER_HOUR,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._root = Path(media)
        self._per_hour = per_hour
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def capabilities(self) -> dict[str, object]:
        return {"upload": True, "per_hour": self._per_hour, "max_per_turn": MAX_PER_TURN}

    def migrations(self) -> Sequence[str]:
        return _SCHEMA

    # ---- the bytes

    def save(self, data_url: str) -> dict[str, str]:
        """Store one data URL, return `{"id", "url"}`. Raises `ValueError` with a key the
        client can translate rather than a stack trace."""
        found = _DATA_URL.match(data_url or "")
        if not found:
            raise ValueError("not_an_image")
        kind = found.group(1).lower()
        try:
            raw = base64.b64decode(found.group(2), validate=False)
        except (ValueError, TypeError) as exc:
            raise ValueError("not_base64") from exc
        if len(raw) > MAX_BYTES:
            raise ValueError("too_big")
        if not _looks_right(raw, kind):
            raise ValueError("not_an_image")
        self._root.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex[:16]}.{kind}"
        (self._root / name).write_bytes(raw)
        return {"id": name, "url": f"/media/{name}"}

    def path(self, name: str) -> Path | None:
        """A stored picture by name, only if the name is one of ours: no `..`, no
        subdirectories, and the file must exist."""
        if not re.fullmatch(r"[0-9a-f]{16}\.(jpeg|png|webp)", name or ""):
            return None
        path = self._root / name
        return path if path.exists() else None

    def parts(self, ids: Sequence[str]) -> list[dict]:
        """What the model sees: the pictures, ahead of the words. A file that is gone is
        skipped rather than fatal — the words still deserve an answer."""
        parts: list[dict] = []
        for name in ids[:MAX_PER_TURN]:
            path = self.path(name)
            if path is None:
                continue
            kind = "jpeg" if path.suffix in (".jpg", ".jpeg") else path.suffix.lstrip(".")
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            parts.append(
                {"type": "image_url", "image_url": {"url": f"data:image/{kind};base64,{data}"}}
            )
        return parts

    # ---- the allowance

    async def before_turn(self, turn: TurnContext) -> Veto | None:
        ids = _ids(turn.facts)
        if not ids or self._per_hour <= 0:
            return None
        user = turn.conversation.user_id
        now = self._clock()
        used = self._used(user, now)
        if used + len(ids) <= self._per_hour:
            return None
        return Veto("photos.hourly", retry_after=self._retry_after(user, now))

    async def after_turn(self, turn: TurnContext) -> None:
        ids = _ids(turn.facts)
        if not ids or turn.reply is None or self._per_hour <= 0:
            return
        now = self._clock()
        for _ in ids:
            self._store.execute(
                "INSERT INTO photo_uses (user_id, at) VALUES (?, ?)",
                (turn.conversation.user_id, now.timestamp()),
            )
        self._store.execute(
            "DELETE FROM photo_uses WHERE at < ?", ((now - timedelta(hours=24)).timestamp(),)
        )

    def _used(self, user: str, now: datetime) -> int:
        row = self._store.query(
            "SELECT COUNT(*) AS n FROM photo_uses WHERE user_id = ? AND at > ?",
            (user, (now - WINDOW).timestamp()),
        )[0]
        return int(row["n"])

    def _retry_after(self, user: str, now: datetime) -> int:
        row = self._store.query(
            "SELECT MIN(at) AS oldest FROM photo_uses WHERE user_id = ? AND at > ?",
            (user, (now - WINDOW).timestamp()),
        )[0]
        oldest = row["oldest"]
        if oldest is None:
            return 1
        return max(1, int(float(oldest) + WINDOW.total_seconds() - now.timestamp() + 0.999))

    # ---- what the page reads

    def routes(self) -> dict[tuple[str, str], Route]:
        async def upload(query, body):  # noqa: ANN001 - plugin.Route
            try:
                return self.save(str(body.get("data") or ""))
            except ValueError as exc:
                return {"error": str(exc)}

        async def left(query, body):  # noqa: ANN001
            user = str(query.get("u") or "")
            if not user:
                return {"error": "需要 u（用户）"}
            now = self._clock()
            return {"used": self._used(user, now), "per_hour": self._per_hour}

        return {("POST", "/api/photos"): upload, ("GET", "/api/photos"): left}


def _ids(facts: Mapping[str, object]) -> list[str]:
    raw = facts.get("photos.ids")
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(item) for item in raw][:MAX_PER_TURN]


def _looks_right(raw: bytes, kind: str) -> bool:
    """Cheap magic-number check: the data URL's type must match the bytes, or the media
    directory ends up holding things that are not pictures."""
    if kind == "jpeg":
        return raw[:3] == b"\xff\xd8\xff"
    if kind == "png":
        return raw[:8] == b"\x89PNG\r\n\x1a\n"
    return raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
