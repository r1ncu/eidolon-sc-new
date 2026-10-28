"""Who is talking, and what this channel remembers about them between two @s.

A QQ number is not a person the kernel knows. This file is the join: one row per person,
pointing at the `users` row the rest of the system already understands — so her memory, the
affinity counter and the quota are all keyed by the same id the web page uses.

Nothing here is a profile. The only things kept are the ones a channel cannot work without:
the number, the name the group shows, whether this person is the producer, whether they
asked her to be quiet, and **how many times she has answered them** — that last one is the
whole of her idea of how close the two of them are, and it is one integer per person.

The one exception is `note`: a short fact about a person that a *human* typed in — 「她是女性」
「是我同事」. It is not inferred, not scored, and never written by a turn; it goes into the
prompt as a fact and the model decides for itself whether it changes how she talks. Anything
that needs a number, a formula or a schedule belongs somewhere else.
"""

import time
from collections.abc import Sequence
from typing import Any

from core.store import Store

from .romance import RELATIONS

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS qq_identities ("
    " qq_id TEXT PRIMARY KEY,"
    " user_id TEXT NOT NULL,"
    " group_card TEXT NOT NULL DEFAULT '',"
    " role TEXT NOT NULL DEFAULT 'member',"
    " first_seen_at TEXT NOT NULL,"
    " muted INTEGER NOT NULL DEFAULT 0,"
    " turns INTEGER NOT NULL DEFAULT 0,"
    " loving INTEGER NOT NULL DEFAULT 0,"
    " note TEXT NOT NULL DEFAULT '',"
    " relation TEXT NOT NULL DEFAULT 'none',"
    " warm_since TEXT,"
    " offered_at TEXT)",
    "ALTER TABLE qq_identities ADD COLUMN turns INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE qq_identities ADD COLUMN loving INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE qq_identities ADD COLUMN note TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE qq_identities ADD COLUMN relation TEXT NOT NULL DEFAULT 'none'",
    "ALTER TABLE qq_identities ADD COLUMN warm_since TEXT",
    "ALTER TABLE qq_identities ADD COLUMN offered_at TEXT",
    # One row, one migration: whoever was in a relationship stays in one. `loving` is kept as
    # a mirror of `relation` from here on (see `set_relation`), so this cannot re-fire for a
    # couple that was deliberately separated — that write sets both columns at once.
    "UPDATE qq_identities SET relation = 'loving' WHERE loving = 1 AND relation = 'none'",
    "CREATE INDEX IF NOT EXISTS qq_identities_by_user ON qq_identities(user_id)",
    # 告白记录：两种方向都写进来（`by` = him / her），冷却与次数都从这里查，不另开一列。
    "CREATE TABLE IF NOT EXISTS qq_confessions ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " qq_id TEXT NOT NULL,"
    " character_id TEXT NOT NULL,"
    " by TEXT NOT NULL DEFAULT 'him',"
    " at TEXT NOT NULL,"
    " affinity REAL NOT NULL DEFAULT 0,"
    " turns INTEGER NOT NULL DEFAULT 0,"
    " plove INTEGER NOT NULL DEFAULT 0,"
    " heavy INTEGER NOT NULL DEFAULT 0,"
    " score REAL NOT NULL DEFAULT 0,"
    " outcome TEXT NOT NULL,"
    " reason TEXT NOT NULL DEFAULT '',"
    " adult INTEGER NOT NULL DEFAULT 1)",
    "CREATE INDEX IF NOT EXISTS qq_confessions_by_couple"
    " ON qq_confessions(qq_id, character_id, at)",
    "CREATE TABLE IF NOT EXISTS qq_seen ("
    " message_id TEXT PRIMARY KEY,"
    " at REAL NOT NULL)",
)


class Identities:
    def __init__(self, store: Store, *, now: str) -> None:
        """`now` is the timestamp written on a new row — passed in so a test can pin it."""
        self._store = store
        self._now = now

    def migrations(self) -> Sequence[str]:
        return SCHEMA

    def resolve(self, qq_id: str, *, card: str = "", role: str = "member") -> str:
        """The kernel's user id for this QQ number, creating the user on first contact.

        The name the user is created with is the group card, because that is the only name
        the rest of the system will ever see (in a private chat the memory file calls them by
        the name in `[names] owner`). A person who changes their card keeps their old name:
        renaming a user is a data decision, not something a group nickname should do behind
        your back.
        """
        qq_id = str(qq_id)
        row = self._store.query("SELECT user_id, role FROM qq_identities WHERE qq_id = ?", (qq_id,))
        if row:
            user_id, known_role = str(row[0]["user_id"]), str(row[0]["role"])
            if role == "owner" and known_role != "owner":
                self._store.execute("UPDATE qq_identities SET role = 'owner' WHERE qq_id = ?", (qq_id,))
            if card:
                self._store.execute(
                    "UPDATE qq_identities SET group_card = ? WHERE qq_id = ?", (card, qq_id)
                )
            return user_id
        user_id = self._store.create_user(card or qq_id)
        self._store.execute(
            "INSERT INTO qq_identities (qq_id, user_id, group_card, role, first_seen_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (qq_id, user_id, card, role, self._now),
        )
        return user_id

    def role(self, qq_id: str) -> str:
        row = self._store.query("SELECT role FROM qq_identities WHERE qq_id = ?", (str(qq_id),))
        return str(row[0]["role"]) if row else ""

    def muted(self, qq_id: str) -> bool:
        row = self._store.query("SELECT muted FROM qq_identities WHERE qq_id = ?", (str(qq_id),))
        return bool(row[0]["muted"]) if row else False

    def set_muted(self, qq_id: str, muted: bool) -> None:
        self._store.execute(
            "UPDATE qq_identities SET muted = ? WHERE qq_id = ?", (1 if muted else 0, str(qq_id))
        )

    # ---- the one thing a person wrote down about somebody

    def note(self, qq_id: str) -> str:
        """A fact about this person that a human typed in. Empty is the normal case."""
        row = self._store.query("SELECT note FROM qq_identities WHERE qq_id = ?", (str(qq_id),))
        return str(row[0]["note"] or "") if row else ""

    def notes(self, qq_ids: Sequence[str]) -> dict[str, str]:
        """The same for a handful of people at once — one query per turn, not one per line.

        Only rows that actually have something: a caller then reads `notes.get(qq, "")` and
        gets the same answer as `note()` without a second query.
        """
        wanted = [str(one) for one in qq_ids if str(one)]
        if not wanted:
            return {}
        marks = ",".join("?" for _ in wanted)
        rows = self._store.query(
            f"SELECT qq_id, note FROM qq_identities WHERE qq_id IN ({marks})", tuple(wanted)
        )
        return {str(row["qq_id"]): str(row["note"]) for row in rows if row["note"]}

    def set_note(self, qq_id: str, note: str) -> dict[str, Any] | None:
        """Write it down, or clear it with an empty string. `None` means the number is unknown.

        Unknown is not created here: a row appears when somebody actually speaks (`resolve`),
        and inventing one from a typo would put a name in `/api/qq/status` for a person who
        does not exist.
        """
        qq_id = str(qq_id)
        row = self._store.query(
            "SELECT group_card FROM qq_identities WHERE qq_id = ?", (qq_id,)
        )
        if not row:
            return None
        text = " ".join(str(note or "").split())
        self._store.execute(
            "UPDATE qq_identities SET note = ? WHERE qq_id = ?", (text, qq_id)
        )
        return {"qq": qq_id, "name": str(row[0]["group_card"]), "note": text}

    # ---- how well she knows them

    def turns(self, qq_id: str) -> int:
        """How many times she has answered this person. Zero for someone she has never met.

        Counted, not inferred: a number that only ever goes up is easy to explain and easy to
        correct by hand, and 「we have talked eleven times」 is a thing a character can act on
        without anyone writing it down in prose.
        """
        row = self._store.query("SELECT turns FROM qq_identities WHERE qq_id = ?", (str(qq_id),))
        return int(row[0]["turns"]) if row else 0

    def bump(self, qq_id: str) -> int:
        """Add one to that count, and hand back the new value. Called once per answer."""
        self._store.execute(
            "UPDATE qq_identities SET turns = turns + 1 WHERE qq_id = ?", (str(qq_id),)
        )
        return self.turns(qq_id)

    def set_turns(self, qq_id: str, turns: int) -> None:
        """Put the count back to a chosen number — `/阶段 重置` is the whole reason, and it
        is a test harness: the ladder is the one thing here you cannot check by talking."""
        self._store.execute(
            "UPDATE qq_identities SET turns = ? WHERE qq_id = ?", (max(0, int(turns)), str(qq_id))
        )

    def set_all_turns(self, turns: int, *, keep: Sequence[str] = ()) -> int:
        """Put *everyone's* count at one number, except the numbers in `keep`.

        The ladder is a promise about how well she knows someone, and a promise made with
        one set of rules is wrong once the rules change: after the register was rewritten,
        everyone was still carrying a count earned under the old voice. This is the one-line
        correction — 「everyone starts over, the producer stays where he is」.

        A person with no row is left alone: there is nothing to reset, and inventing a row
        would make `/api/qq/status` count people who never spoke.
        """
        value = max(0, int(turns))
        kept = tuple(str(one) for one in keep if str(one))
        if kept:
            marks = ",".join("?" for _ in kept)
            self._store.execute(
                f"UPDATE qq_identities SET turns = ? WHERE qq_id NOT IN ({marks})",
                (value, *kept),
            )
        else:
            self._store.execute("UPDATE qq_identities SET turns = ?", (value,))
        return value

    def count(self) -> int:
        return int(self._store.query("SELECT COUNT(*) AS n FROM qq_identities")[0]["n"])

    # ---- the one relationship that changes kind, not degree

    def loving(self, qq_id: str) -> bool:
        """Whether the two of them are past 「他喜欢我」 and into 「我们在谈恋爱」.

        A flag, not a threshold: the numbers decide when it *starts*, and after that going
        back would be a breakup — a story beat, not something one cold night should do by
        accident. `/阶段 重置` deliberately leaves it alone; ending it is a data decision
        (`UPDATE qq_identities SET loving = 0`).
        """
        row = self._store.query("SELECT loving FROM qq_identities WHERE qq_id = ?", (str(qq_id),))
        return bool(row[0]["loving"]) if row else False

    def set_loving(self, qq_id: str, loving: bool) -> None:
        """Kept for the old callers: 「在一起」 means `relation = 'loving'`, nothing else.

        It writes the relation too, so the two can never disagree — a stale `loving = 1`
        next to `relation = 'none'` would put her in a relationship the prompt does not know
        about, which is exactly the kind of split-brain this column pair is designed out.
        """
        self.set_relation(qq_id, "loving" if loving else "none")

    # ---- the relationship, as a state rather than a flag

    def relation(self, qq_id: str) -> str:
        """`none` / `crush` / `offered` / `loving`. A stranger is `none`.

        An unknown number is `none` rather than an error: the caller asking is usually about
        to create the row anyway.
        """
        row = self._store.query("SELECT relation FROM qq_identities WHERE qq_id = ?", (str(qq_id),))
        if not row:
            return "none"
        value = str(row[0]["relation"] or "none")
        return value if value in RELATIONS else "none"

    def set_relation(self, qq_id: str, relation: str) -> str | None:
        """Put her in a state, and keep `loving` as its mirror. `None` = unknown number.

        **No policy here.** Which states a given character may enter (adult, `PLove ≥ 1`、
        未成年封顶在 crush) is `channels/romance.py::allowed`, checked by the caller. This
        method is the storage layer; it must not grow opinions about age.
        """
        if relation not in RELATIONS:
            raise ValueError(f"unknown relation: {relation!r}")
        qq_id = str(qq_id)
        row = self._store.query("SELECT relation FROM qq_identities WHERE qq_id = ?", (qq_id,))
        if not row:
            return None
        before = str(row[0]["relation"] or "none")
        self._store.execute(
            "UPDATE qq_identities SET relation = ?, loving = ? WHERE qq_id = ?",
            (relation, 1 if relation == "loving" else 0, qq_id),
        )
        return before

    def warm_since(self, qq_id: str) -> str:
        """When she first got above `SHE_FLOOR` **and stayed**. Empty = not above it now.

        Consecutive, not cumulative: the caller clears it the moment the number drops, so
        「等了三周」 cannot be assembled out of scattered good days.
        """
        row = self._store.query(
            "SELECT warm_since FROM qq_identities WHERE qq_id = ?", (str(qq_id),)
        )
        return str(row[0]["warm_since"] or "") if row else ""

    def mark_warm(self, qq_id: str, at: str) -> bool:
        """Note that she is above the line now. Returns True only the first time.

        Read-then-write, and that is fine here: two turns arriving together would write
        nearly the same timestamp into the same column, and both mean 「she has been above
        the line since about now」. Nothing downstream cares which one won.
        """
        if self.warm_since(qq_id):
            return False
        self._store.execute(
            "UPDATE qq_identities SET warm_since = ? WHERE qq_id = ?", (at, str(qq_id))
        )
        return True

    def clear_warm(self, qq_id: str) -> None:
        """She fell back below the line: the clock starts over, not pauses."""
        self._store.execute(
            "UPDATE qq_identities SET warm_since = NULL WHERE qq_id = ?", (str(qq_id),)
        )

    def offered_at(self, qq_id: str) -> str:
        row = self._store.query(
            "SELECT offered_at FROM qq_identities WHERE qq_id = ?", (str(qq_id),)
        )
        return str(row[0]["offered_at"] or "") if row else ""

    def set_offered(self, qq_id: str, at: str | None) -> None:
        """When she said it and started waiting. `None` clears it."""
        self._store.execute(
            "UPDATE qq_identities SET offered_at = ? WHERE qq_id = ?", (at, str(qq_id))
        )

    # ---- things that were said out loud, and what came of them

    def record_confession(
        self,
        qq_id: str,
        character_id: str,
        *,
        by: str,
        at: str,
        outcome: str,
        reason: str = "",
        affinity: float = 0.0,
        turns: int = 0,
        plove: int = 0,
        heavy: int = 0,
        score: float = 0.0,
        adult: bool = True,
    ) -> None:
        """One row per attempt. Written even when it fails: 「他说过一次」 is the fact the
        cooldown and the three-attempt limit are computed from, and a memory cannot count."""
        self._store.execute(
            "INSERT INTO qq_confessions"
            " (qq_id, character_id, by, at, affinity, turns, plove, heavy, score,"
            "  outcome, reason, adult)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(qq_id), character_id, by, at, float(affinity), int(turns),
                int(plove), int(heavy), float(score), outcome, reason, 1 if adult else 0,
            ),
        )

    def confessions(self, qq_id: str, character_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._store.query(
            "SELECT * FROM qq_confessions WHERE qq_id = ? AND character_id = ?"
            " ORDER BY id DESC LIMIT ?",
            (str(qq_id), character_id, int(limit)),
        )
        return [dict(row) for row in rows]

    def asks_by_him(self, qq_id: str, character_id: str) -> int:
        row = self._store.query(
            "SELECT COUNT(*) AS n FROM qq_confessions"
            " WHERE qq_id = ? AND character_id = ? AND by = 'him'",
            (str(qq_id), character_id),
        )
        return int(row[0]["n"]) if row else 0

    def last_refusal_at(self, qq_id: str, character_id: str) -> str:
        """When she last said no (or the wall did). Empty when she never has."""
        row = self._store.query(
            "SELECT at FROM qq_confessions"
            " WHERE qq_id = ? AND character_id = ? AND outcome IN ('refuse', 'minor', 'timeout')"
            " ORDER BY id DESC LIMIT 1",
            (str(qq_id), character_id),
        )
        return str(row[0]["at"] or "") if row else ""

    def pending_offer(self, qq_id: str, character_id: str) -> str:
        """`at` of an unanswered 「她说了」; empty when there is none.

        An `offered` row only exists while it is unanswered — answering it writes
        `loving` / `crush` instead of a second row — so 「有 offered 记录但没有答复」 and
        「还在等」 are the same statement.
        """
        row = self._store.query(
            "SELECT at FROM qq_confessions"
            " WHERE qq_id = ? AND character_id = ? AND outcome = 'offered'"
            " ORDER BY id DESC LIMIT 1",
            (str(qq_id), character_id),
        )
        return str(row[0]["at"] or "") if row else ""

    # ---- events we have already handled

    def first_time(self, message_id: str) -> bool:
        """True the first time this message is seen. A reconnect replays events, and a
        replayed message answered twice is the most visible bug a channel can have."""
        if not message_id:
            return True
        found = self._store.query("SELECT 1 FROM qq_seen WHERE message_id = ?", (message_id,))
        if found:
            return False
        self._store.execute(
            "INSERT OR IGNORE INTO qq_seen (message_id, at) VALUES (?, ?)", (message_id, time.time())
        )
        return True

    def prune(self, *, keep_seconds: float = 24 * 3600) -> None:
        self._store.execute("DELETE FROM qq_seen WHERE at < ?", (time.time() - keep_seconds,))
