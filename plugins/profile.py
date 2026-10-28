"""Who the producer is, kept where the prompt keeps things about them.

The old deployment lets a producer say what characters call them, their gender, their
birthday and their timezone, and every character reads that. Those are exactly what the
kernel's `profile` slot is for — what holds for a user, cached with the profile — so this
plugin is a small form and a `Section(Slot.PROFILE)`.

Empty fields mean "each character follows her own habits", which is also what an absent
row means: the plugin writes nothing into the prompt rather than writing defaults in.
"""

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any

from core.plugin import Plugin, Route, TurnContext
from core.prompt import Section, Slot
from core.store import Store

GENDERS = {"default": "沿用原作设定（男性）", "male": "男性", "female": "女性"}

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS producer_profile ("
    " user_id TEXT PRIMARY KEY,"
    " address TEXT NOT NULL DEFAULT '',"
    " gender TEXT NOT NULL DEFAULT 'default',"
    " birth_month INTEGER,"
    " birth_day INTEGER,"
    " timezone TEXT NOT NULL DEFAULT '',"
    " updated_at TEXT NOT NULL)",
)


class Profile(Plugin):
    name = "profile"

    def __init__(self, store: Store, *, clock: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def capabilities(self) -> dict[str, object]:
        return {"panel": "profile", "genders": GENDERS}

    def migrations(self) -> Sequence[str]:
        return _SCHEMA

    async def prompt_sections(self, turn: TurnContext) -> Sequence[Section]:
        row = self._row(turn.conversation.user_id)
        if row is None:
            return ()
        lines = _prompt_lines(
            address=str(row["address"] or ""),
            gender=str(row["gender"] or "default"),
            month=row["birth_month"],
            day=row["birth_day"],
            timezone_name=str(row["timezone"] or ""),
        )
        if not lines:
            return ()
        return (Section(Slot.PROFILE, "\n".join(lines), order=10, title="profile"),)

    # ---- what the page reads and writes

    def get(self, user_id: str) -> dict[str, object]:
        row = self._row(user_id)
        if row is None:
            return {"address": "", "gender": "default", "birth_month": None, "birth_day": None, "timezone": ""}
        return {
            "address": row["address"],
            "gender": row["gender"],
            "birth_month": row["birth_month"],
            "birth_day": row["birth_day"],
            "timezone": row["timezone"],
        }

    def set(
        self,
        user_id: str,
        *,
        address: str = "",
        gender: str = "default",
        month: int | None = None,
        day: int | None = None,
        timezone_name: str = "",
    ) -> dict[str, object]:
        """Save the form. A birthday needs both parts or neither; a gender that is not one
        of the three falls back to the default rather than reaching the prompt."""
        if gender not in GENDERS:
            gender = "default"
        month = int(month) if month else None
        day = int(day) if day else None
        if (month is None) != (day is None):
            raise ValueError("birthday_needs_both")
        if month is not None and not (1 <= month <= 12 and 1 <= day <= 31):  # type: ignore[arg-type]
            raise ValueError("birthday_out_of_range")
        self._store.execute(
            "INSERT INTO producer_profile (user_id, address, gender, birth_month, birth_day, timezone, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(user_id) DO UPDATE SET address = excluded.address, gender = excluded.gender,"
            " birth_month = excluded.birth_month, birth_day = excluded.birth_day,"
            " timezone = excluded.timezone, updated_at = excluded.updated_at",
            (
                user_id,
                " ".join(address.split())[:40],
                gender,
                month,
                day,
                timezone_name[:64],
                self._clock().isoformat(timespec="seconds"),
            ),
        )
        return self.get(user_id)

    def routes(self) -> dict[tuple[str, str], Route]:
        async def read(query, body):  # noqa: ANN001 - plugin.Route
            user_id = str(query.get("u") or "")
            if not user_id:
                return {"error": "需要 u（用户）"}
            return self.get(user_id)

        async def write(query, body):  # noqa: ANN001
            user_id = str(body.get("u") or query.get("u") or "")
            if not user_id:
                return {"error": "需要 u（用户）"}
            try:
                return self.set(
                    user_id,
                    address=str(body.get("address") or ""),
                    gender=str(body.get("gender") or "default"),
                    month=body.get("birth_month"),
                    day=body.get("birth_day"),
                    timezone_name=str(body.get("timezone") or ""),
                )
            except ValueError as exc:
                return {"error": str(exc)}

        return {("GET", "/api/profile"): read, ("POST", "/api/profile"): write}

    # ---- inside

    def _row(self, user_id: str) -> Any:
        rows = self._store.query("SELECT * FROM producer_profile WHERE user_id = ?", (user_id,))
        return rows[0] if rows else None


def _prompt_lines(*, address: str, gender: str, month: int | None, day: int | None, timezone_name: str) -> list[str]:
    """What she reads. Only what was filled in: a blank form says nothing at all."""
    lines = ["## 制作人"]
    if address:
        lines.append(f"- 你叫他「{address}」。这是他自己填的称呼，除此以外按你的习惯叫。")
    if gender != "default":
        lines.append(f"- 制作人的性别：{GENDERS[gender]}。")
    if month and day:
        lines.append(f"- 制作人的生日是 {month} 月 {day} 日。")
    if timezone_name:
        lines.append(f"- 制作人的时区是 {timezone_name}，他说的「今天」「今晚」按它算。")
    return lines if len(lines) > 1 else []
