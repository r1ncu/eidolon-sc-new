"""How many turns a user may take in a sliding hour.

The first plugin, and deliberately the smallest: one table, one gate, one observer. If
this file needed anything from the kernel that `plugin.Plugin` does not offer, the
boundary would be wrong.

Three things it copies from upstream `usage.py`, each because getting it wrong is
noticed by users:

- **A sliding hour, not a clock hour.** Every turn is a row with a timestamp; the count is
  "rows newer than an hour", and `retry_after` is when the oldest of them ages out. An
  hourly bucket would let a user spend a whole hour's worth in the last minute of it and
  another whole hour's worth in the next minute.
- **Charged on the way out, not on the way in.** `before_turn` only reads. The row is
  written in `after_turn`, which the kernel calls once the reply is stored — so a model
  that drops the line costs the user nothing, and the words can be sent again.
- **Rows outlive their use.** Kept for a day (the window is an hour), so an admin can
  still see what happened this morning. Pruned when a row is written.

`0` and `None` both mean no limit: that is what `EIDOLON_TURNS_PER_HOUR=0` means in the
deployment this replaces. The third state upstream has — "0, so own keys only" — needs
own keys to exist, and belongs here the day they do.
"""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from core.plugin import Plugin, TurnContext, Veto
from core.store import Store

WINDOW = timedelta(hours=1)
KEPT = timedelta(hours=24)
"""A use lives an hour for the limit and a day for the record, as upstream."""
KIND = "turn"

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS quota_uses ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " user_id TEXT NOT NULL,"
    " kind TEXT NOT NULL,"
    " at REAL NOT NULL,"
    " exempt INTEGER NOT NULL DEFAULT 0)",
    "CREATE INDEX IF NOT EXISTS quota_uses_by_user ON quota_uses(user_id, kind, at)",
    "CREATE INDEX IF NOT EXISTS quota_uses_by_time ON quota_uses(at)",
)


class Quota(Plugin):
    name = "quota"

    def __init__(
        self,
        store: Store,
        *,
        turns_per_hour: int | None = 60,
        clock: Callable[[], datetime] | None = None,
        exempt: Callable[[TurnContext], bool] | None = None,
    ) -> None:
        self._store = store
        self._limit = turns_per_hour
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._exempt = exempt or (lambda turn: False)

    def capabilities(self) -> dict[str, object]:
        return {"limits": {"turns_per_hour": self._limit if self._limited else None}}

    def migrations(self) -> tuple[str, ...]:
        return _SCHEMA

    @property
    def _limited(self) -> bool:
        """`None` and `0` are both "no limit", as the deployment's env var means them."""
        return self._limit is not None and self._limit > 0

    async def before_turn(self, turn: TurnContext) -> Veto | None:
        if not self._limited or self._exempt(turn):
            return None
        user = turn.conversation.user_id
        now = self._clock()
        used = self._used(user, now)
        if used < int(self._limit or 0):
            return None
        return Veto("quota.turns", retry_after=self._retry_after(user, now))

    async def after_turn(self, turn: TurnContext) -> None:
        if not self._limited or turn.reply is None or self._exempt(turn):
            return
        now = self._clock()
        self._store.execute(
            "INSERT INTO quota_uses (user_id, kind, at, exempt) VALUES (?, ?, ?, ?)",
            (turn.conversation.user_id, KIND, now.timestamp(), 0),
        )
        self._store.execute(
            "DELETE FROM quota_uses WHERE at < ?", ((now - KEPT).timestamp(),)
        )

    def _used(self, user: str, now: datetime) -> int:
        row = self._store.query(
            "SELECT COUNT(*) AS n FROM quota_uses WHERE user_id = ? AND kind = ? AND at > ?",
            (user, KIND, (now - WINDOW).timestamp()),
        )[0]
        return int(row["n"])

    def _retry_after(self, user: str, now: datetime) -> int:
        """Seconds until the oldest use of this hour ages out: the soonest this user could
        be let through, which is what a client shows as "wait a moment"."""
        row = self._store.query(
            "SELECT MIN(at) AS oldest FROM quota_uses WHERE user_id = ? AND kind = ? AND at > ?",
            (user, KIND, (now - WINDOW).timestamp()),
        )[0]
        oldest = row["oldest"]
        if oldest is None:
            return 1
        wait = float(oldest) + WINDOW.total_seconds() - now.timestamp()
        return max(1, int(wait + 0.999))
