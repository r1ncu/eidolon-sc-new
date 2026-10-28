"""Gifts: draw chances, a backpack, and the one way into them for the gifts page.

Everything the page can do is here, and the numbers are upstream's
(`backend/src/eidolon/gifts.py`, `affinity.py`), because they hold each other in check:

- **One draw chance a day**, as the day begins at midnight Beijing time, kept up to seven.
  Nothing gives them on a schedule: whoever reads them first is given what the days since
  they were last given have brought. A producer without any yet is given one that day.
- **What a draw gives**, in percent: a reset card 1, a common gift 69, an uncommon 25, a
  rare 5. Any of that rarity's gifts not retired as likely as another; nothing rarer is
  ever guaranteed. Drawing calls no model and costs nothing.
- **A gift raises affinity by its rarity**, more if she likes it, at most a little while
  she is in a cooldown. A draw's odds and a gift's worth play no part in each other.
- **A reset card** brings every character below trust back to trust out of any cooldown,
  and nobody is told it happened.

Where this differs from upstream: a gift record is kept in this plugin's own table rather
than as a conversation event, so the conversation timeline does not show a row for it. The
gift still counts in the stats and still moves affinity, which is what the page shows.
"""

import random
import threading
import tomllib
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from core.plugin import Plugin, Route
from core.store import Store
from plugins.affinity import BELOW_TRUST, Affinity, day_of, next_day

CATEGORIES = ("food", "drink", "flower", "trinket")
RARITIES = ("common", "uncommon", "rare")
MAX_LIKED = 3

RESET_CARD = "reset_card"
"""What a draw gives that is not a gift."""

DRAWS_PER_DAY = 1
MAX_DRAWS = 7
DRAW_ODDS: dict[str, int] = {"reset_card": 1, "common": 69, "uncommon": 25, "rare": 5}
"""What a draw gives, in percent; the sum is 100."""

GIFTS_FILE = "gifts.toml"
"""The catalog's name, in the directory of the character configuration."""

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS gift_balances ("
    " user_id TEXT PRIMARY KEY,"
    " draws INTEGER NOT NULL,"
    " given_on TEXT NOT NULL,"
    " reset_cards INTEGER NOT NULL DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS gift_backpacks ("
    " user_id TEXT NOT NULL,"
    " gift_id TEXT NOT NULL,"
    " count INTEGER NOT NULL,"
    " PRIMARY KEY (user_id, gift_id))",
    "CREATE TABLE IF NOT EXISTS gift_records ("
    " id TEXT PRIMARY KEY,"
    " user_id TEXT NOT NULL,"
    " character_id TEXT NOT NULL,"
    " gift_id TEXT NOT NULL,"
    " liked INTEGER NOT NULL DEFAULT 0,"
    " rarity TEXT NOT NULL,"
    " at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS gift_records_by_pair ON gift_records(user_id, character_id, at)",
)


class GiftCatalogError(RuntimeError):
    """The gift catalog is missing or broken."""


@dataclass(frozen=True)
class Gift:
    id: str
    name: str
    category: str
    rarity: str
    retired: bool = False

    def described(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "rarity": self.rarity,
            "retired": self.retired,
        }


def load_catalog(path: Path) -> dict[str, Gift]:
    """The gifts in the catalog at `path`, keyed by identifier in file order. Raises
    GiftCatalogError if it is missing or broken."""
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise GiftCatalogError(f"找不到礼物目录 {path}") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise GiftCatalogError(f"礼物目录 {path} 格式有误：{exc}") from None
    entries = config.get("gifts", [])
    if not isinstance(entries, list):
        raise GiftCatalogError(f"礼物目录 {path} 里的 gifts 应当是 [[gifts]] 段")
    catalog: dict[str, Gift] = {}
    for entry in entries:
        gift = _parse_gift(entry)
        if gift.id in catalog:
            raise GiftCatalogError(f"礼物标识 {gift.id} 重复")
        catalog[gift.id] = gift
    for rarity in RARITIES:
        if not any(g.rarity == rarity and not g.retired for g in catalog.values()):
            raise GiftCatalogError(f"礼物目录里稀有度 {rarity} 没有一件没下架的礼物，这一档抽不出东西")
    return catalog


def _parse_gift(entry: object) -> Gift:
    if not isinstance(entry, dict):
        raise GiftCatalogError(f"礼物目录里的 {entry!r} 应当是一个 [[gifts]] 段")
    label = entry.get("id", "（没有标识）")
    missing = [f for f in ("id", "name", "category", "rarity") if f not in entry]
    if missing:
        raise GiftCatalogError(f"礼物 {label} 缺少 {'、'.join(missing)}")
    id, name, category, rarity = entry["id"], entry["name"], entry["category"], entry["rarity"]
    if not isinstance(id, str) or not id or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in id):
        raise GiftCatalogError(f"礼物标识 {id} 只能用小写字母、数字、- 和 _")
    if not isinstance(name, str) or not name.strip():
        raise GiftCatalogError(f"礼物 {id} 的 name 应当是一段文字")
    if category not in CATEGORIES:
        raise GiftCatalogError(f"礼物 {id} 的类别 {category} 不在 {'、'.join(CATEGORIES)} 之中")
    if rarity not in RARITIES:
        raise GiftCatalogError(f"礼物 {id} 的稀有度 {rarity} 不在 {'、'.join(RARITIES)} 之中")
    retired = entry.get("retired", False)
    if not isinstance(retired, bool):
        raise GiftCatalogError(f"礼物 {id} 的 retired 应当是 true 或 false")
    return Gift(id, name, category, rarity, retired)


class Gifts(Plugin):
    name = "gifts"
    requires = ("affinity",)

    def __init__(
        self,
        store: Store,
        affinity: Affinity,
        *,
        characters: Callable[[], Sequence[object]],
        directory: str | Path = "characters",
        catalog: str = GIFTS_FILE,
        clock: Callable[[], datetime] | None = None,
        odds: Callable[[int], int] | None = None,
        step: Callable[[int], int] | None = None,
    ) -> None:
        """`characters` is the roster, asked for identifiers; the catalog is read once, as
        upstream reads it, and a broken one keeps the deployment from starting."""
        self._store = store
        self._affinity = affinity
        self._characters = characters
        self._catalog = load_catalog(Path(directory) / catalog)
        self._order = list(self._catalog)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._rand = odds or (lambda upto: random.randrange(upto))
        self._pick = step or (lambda upto: random.randrange(upto))
        self._lock = threading.Lock()

    def capabilities(self) -> dict[str, object]:
        return {
            "panel": "gifts",
            "open": True,
            "max_draws": MAX_DRAWS,
            "rarities": list(RARITIES),
            "categories": list(CATEGORIES),
        }

    def migrations(self) -> Sequence[str]:
        return _SCHEMA

    @property
    def catalog(self) -> list[Gift]:
        return [self._catalog[g] for g in self._order]

    def liked(self, character: object, gift_id: str) -> bool:
        return gift_id in getattr(character, "liked_gifts", ())

    # ---- what the page reads

    def state(self, user_id: str) -> dict[str, object]:
        with self._lock:
            self.give_due(user_id, day_of(self._clock()))
            rows = self._store.query(
                "SELECT draws, reset_cards FROM gift_balances WHERE user_id = ?", (user_id,)
            )
            held = {
                str(row["gift_id"]): int(row["count"])
                for row in self._store.query(
                    "SELECT gift_id, count FROM gift_backpacks WHERE user_id = ? AND count > 0",
                    (user_id,),
                )
            }
        draws, cards = (int(rows[0]["draws"]), int(rows[0]["reset_cards"])) if rows else (0, 0)
        characters = []
        for character in self._characters():
            standing = self._affinity.read(user_id, character.id)
            characters.append(
                {
                    "id": character.id,
                    "cooling": standing.cooling,
                    **standing.as_dict(),
                }
            )
        return {
            "open": True,
            "draws": draws,
            "max_draws": MAX_DRAWS,
            "backpack": [{"gift_id": g, "count": held[g]} for g in self._order if g in held],
            "reset_cards": cards,
            "next_draw_at": next_day(self._clock()).isoformat(),
            "resettable": [c for c in characters if c["tier"] in BELOW_TRUST],
            "characters": characters,
        }

    # ---- drawing

    def draw(self, user_id: str, *, all: bool = False) -> dict[str, object]:
        """Draw once, or as many times as there are chances if `all`; in one go, so that two
        drawing at once draw no more than there are."""
        with self._lock:
            self.give_due(user_id, day_of(self._clock()))
            rows = self._store.query(
                "SELECT draws FROM gift_balances WHERE user_id = ?", (user_id,)
            )
            draws = int(rows[0]["draws"]) if rows else 0
            if draws == 0:
                return {"error": "没有抽奖次数了", "draws": 0}
            drawn = [self._draw_one() for _ in range(draws if all else 1)]
            cards = 0
            for gift_id in drawn:
                if gift_id == RESET_CARD:
                    cards += 1
                    continue
                self._store.execute(
                    "INSERT INTO gift_backpacks (user_id, gift_id, count) VALUES (?, ?, 1)"
                    " ON CONFLICT (user_id, gift_id) DO UPDATE SET count = count + 1",
                    (user_id, gift_id),
                )
            if cards:
                self._store.execute(
                    "UPDATE gift_balances SET reset_cards = reset_cards + ? WHERE user_id = ?",
                    (cards, user_id),
                )
            left = draws - len(drawn)
            self._store.execute(
                "UPDATE gift_balances SET draws = ? WHERE user_id = ?", (left, user_id)
            )
        return {
            "drawn": [
                {"kind": "reset_card"} if d == RESET_CARD else {"kind": "gift", "gift_id": d}
                for d in drawn
            ],
            "draws": left,
        }

    def _draw_one(self) -> str:
        number = self._rand(100)
        upto = 0
        kind = RESET_CARD
        for name, weight in DRAW_ODDS.items():
            upto += weight
            if number < upto:
                kind = name
                break
        if kind == RESET_CARD:
            return RESET_CARD
        pool = [g.id for g in self._catalog.values() if g.rarity == kind and not g.retired]
        return pool[self._pick(len(pool))]

    # ---- giving

    def give(self, user_id: str, character_id: str, gift_id: str) -> dict[str, object]:
        gift = self._catalog.get(gift_id)
        if gift is None:
            return {"error": "没有这件礼物"}
        character = next((c for c in self._characters() if c.id == character_id), None)
        if character is None:
            return {"error": "角色不存在"}
        with self._lock:
            taken = self._store.query(
                "UPDATE gift_backpacks SET count = count - 1"
                " WHERE user_id = ? AND gift_id = ? AND count > 0 RETURNING count",
                (user_id, gift_id),
            )
        if not taken:
            return {"error": "已经没有了"}
        liked = self.liked(character, gift_id)
        try:
            change, standing = self._affinity.receive_gift(user_id, character_id, gift.rarity, liked)
        except BaseException:
            with self._lock:
                self._store.execute(
                    "UPDATE gift_backpacks SET count = count + 1 WHERE user_id = ? AND gift_id = ?",
                    (user_id, gift_id),
                )
            raise
        self._store.execute(
            "INSERT INTO gift_records (id, user_id, character_id, gift_id, liked, rarity, at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex[:16], user_id, character_id, gift_id, int(liked), gift.rarity,
             self._clock().isoformat(timespec="seconds")),
        )
        return {
            "change": change,
            "affinity": standing.as_dict(),
            "left": int(taken[0]["count"]),
        }

    # ---- reset cards

    def use_reset_card(self, user_id: str) -> dict[str, object]:
        with self._lock:
            taken = self._store.query(
                "UPDATE gift_balances SET reset_cards = reset_cards - 1"
                " WHERE user_id = ? AND reset_cards > 0 RETURNING reset_cards",
                (user_id,),
            )
        if not taken:
            return {"error": "没有重置卡了"}
        brought = self._affinity.reset(user_id, [c.id for c in self._characters()])
        if not brought:
            with self._lock:
                self._store.execute(
                    "UPDATE gift_balances SET reset_cards = reset_cards + 1 WHERE user_id = ?",
                    (user_id,),
                )
            return {"error": "现在没有低于信任的角色"}
        return {"brought_back": brought, "reset_cards": int(taken[0]["reset_cards"])}

    # ---- the day's chances

    def give_due(self, user_id: str, today: date) -> None:
        """Give the producer the draw chances due by `today`: one if there are none yet,
        else one for each day since they were last given, up to the most kept. Called
        holding the lock."""
        rows = self._store.query(
            "SELECT draws, given_on FROM gift_balances WHERE user_id = ?", (user_id,)
        )
        if not rows:
            self._store.execute(
                "INSERT INTO gift_balances (user_id, draws, given_on, reset_cards)"
                " VALUES (?, ?, ?, 0)",
                (user_id, DRAWS_PER_DAY, today.isoformat()),
            )
            return
        draws, given_on = int(rows[0]["draws"]), date.fromisoformat(str(rows[0]["given_on"]))
        if today <= given_on:
            return
        due = (today - given_on).days * DRAWS_PER_DAY
        self._store.execute(
            "UPDATE gift_balances SET draws = ?, given_on = ? WHERE user_id = ?",
            (max(draws, min(draws + due, MAX_DRAWS)), today.isoformat(), user_id),
        )

    # ---- what the stats count

    def records(self, user_id: str) -> list[dict[str, object]]:
        rows = self._store.query(
            "SELECT character_id, gift_id, at, liked FROM gift_records WHERE user_id = ? ORDER BY at",
            (user_id,),
        )
        return [
            {
                "character_id": str(row["character_id"]),
                "gift_id": str(row["gift_id"]),
                "at": str(row["at"]),
                "liked": bool(row["liked"]),
            }
            for row in rows
        ]

    # ---- endpoints

    def routes(self) -> dict[tuple[str, str], Route]:
        async def state(query, body):  # noqa: ANN001 - plugin.Route
            user_id = str(query.get("u") or "")
            if not user_id:
                return {"error": "需要 u（用户）"}
            return self.state(user_id)

        async def catalog(query, body):  # noqa: ANN001
            return {"gifts": [gift.described() for gift in self.catalog]}

        async def draws(query, body):  # noqa: ANN001
            user_id = str(body.get("u") or query.get("u") or "")
            if not user_id:
                return {"error": "需要 u（用户）"}
            return self.draw(user_id, all=bool(body.get("all")))

        async def give(query, body):  # noqa: ANN001
            user_id = str(body.get("u") or "")
            character_id = str(body.get("c") or "")
            gift_id = str(body.get("gift") or "")
            if not (user_id and character_id and gift_id):
                return {"error": "需要 u（用户）、c（角色）、gift（礼物）"}
            return self.give(user_id, character_id, gift_id)

        async def reset(query, body):  # noqa: ANN001
            user_id = str(body.get("u") or "")
            if not user_id:
                return {"error": "需要 u（用户）"}
            return self.use_reset_card(user_id)

        async def records(query, body):  # noqa: ANN001
            user_id = str(query.get("u") or "")
            if not user_id:
                return {"error": "需要 u（用户）"}
            return {"gifts": self.records(user_id)}

        return {
            ("GET", "/api/gifts/state"): state,
            ("GET", "/api/gifts/catalog"): catalog,
            ("POST", "/api/gifts/draws"): draws,
            ("POST", "/api/gifts/give"): give,
            ("POST", "/api/gifts/reset-cards"): reset,
            ("GET", "/api/gifts/records"): records,
        }
