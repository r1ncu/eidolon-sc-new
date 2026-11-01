"""节假日：这台部署认为「明天要不要上班」的那张表。

作息不是一条规则，是两条：工作日 23:00 睡，休息日可以熬。要知道今天算哪一种，就得有人
回答「今天是休息日吗」——周末是日历给的，祝日是**这个国家**给的。这个项目演的是日本的
作品、住在日本，所以默认读日本的祝日表（`characters/calendar.toml`），而不是跑代码那台
机器的、也不是部署者本人的。

不引第三方库：一年十几行日期，一张表就够，而且「明年这本表要更新」本身就是一句该写在
文件里的话，不该藏在一个 pip 包的版本号后面。表由日本内阁府的公开 CSV 生成
（https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv），含振替休日与国民の休日。

`QQ_HOLIDAYS` / `QQ_WORKDAYS` 仍然有效，作用是**覆盖**这张表（调休上班的周六、公司自己
放的假）。覆盖优先于表，表优先于周末。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

CALENDAR_FILE = "calendar.toml"
DEFAULT_ZONE = "Asia/Tokyo"
WEEKEND = frozenset({5, 6})
"""Saturday and Sunday. `date.weekday()` counts from Monday."""


@dataclass(frozen=True)
class Calendar:
    """Which days count as days off, and what they are called."""

    timezone: str = DEFAULT_ZONE
    country: str = "JP"
    holidays: Mapping[date, str] = field(default_factory=dict)
    workdays: frozenset[date] = frozenset()
    extra_holidays: frozenset[date] = frozenset()

    def is_off(self, day: date) -> bool:
        """A day she does not have to be up early for.

        Order: an explicit workday beats everything (调休上班的周六 really is a workday);
        then an explicit extra holiday; then the country's table; then the weekend.
        """
        if day in self.workdays:
            return False
        if day in self.extra_holidays:
            return True
        if day in self.holidays:
            return True
        return day.weekday() in WEEKEND

    def name(self, day: date) -> str:
        """`元日`, or `週末`, or `""` for an ordinary day — for a log line or the page."""
        if day in self.holidays:
            return self.holidays[day]
        if day in self.extra_holidays:
            return "休み"
        if day.weekday() in WEEKEND and day not in self.workdays:
            return "週末"
        return ""

    def as_dict(self) -> dict[str, object]:
        days = sorted(self.holidays)
        return {
            "country": self.country,
            "timezone": self.timezone,
            "count": len(self.holidays),
            "from": days[0].isoformat() if days else "",
            "to": days[-1].isoformat() if days else "",
        }


def load(
    characters: str | Path,
    *,
    timezone: str = "",
    holidays: Iterable[str] = (),
    workdays: Iterable[str] = (),
) -> Calendar:
    """`characters/calendar.toml` plus the two environment overrides.

    A missing or broken file is a calendar with weekends only: she still sleeps, she just
    does not know about 祝日. That is the right failure — refusing to start over a holiday
    table would take the bot down for something cosmetic.
    """
    root = Path(characters)
    data = _read(root / CALENDAR_FILE)
    table = data.get("calendar") or {}
    zone = str(table.get("timezone") or timezone or DEFAULT_ZONE).strip() or DEFAULT_ZONE
    country = str(table.get("country") or "JP").strip() or "JP"
    listed = table.get("holidays") or {}
    known: dict[date, str] = {}
    if isinstance(listed, dict):
        for key, value in listed.items():
            day = _date(key)
            if day is not None:
                known[day] = str(value or "").strip()
    return Calendar(
        timezone=zone,
        country=country,
        holidays=known,
        workdays=frozenset(d for d in (_date(one) for one in workdays) if d is not None),
        extra_holidays=frozenset(
            d for d in (_date(one) for one in holidays) if d is not None
        ),
    )


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None
