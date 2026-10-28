"""Who she is, and who else you could be talking to.

The roster is data, not code: `characters.toml` beside a `sheets/` directory of Markdown,
the same shape the deployment this replaces already has, so moving to `scbot-new` is
pointing it at the directory rather than exporting anything. A character is her name, her
colour, her avatar, her sheet — the kernel only ever needs the sheet text; everything else
is for the page.

The plugin owns the roster because the roster is a surface: a client asks
`GET /api/characters`, gets what it may draw, and the turn loop stays ignorant of the fact
that there are twenty-eight of them or that a colour exists. Without this plugin the
server still talks to one character, from `SCCORE_CHARACTERS`' first `*.md`.
"""

import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from core.plugin import Plugin, Route

SHEETS = "sheets"
FALLBACK_COLOR = "#8ab4f8"

# 组合（原版 backend/src/eidolon/world.py 的 World.units：官方顺序、组名和平文名读音）。
# 对话列表的筛选面板按它分组，搜索也拿组名和组读音当候选。按角色 id 归属，不按名字——
# 这台部署没有剧情库，World 的那套推导在这里是一张表。
UNITS_FILE = "units.toml"
"""Where her unit lives: `<characters>/units.toml`, next to the roster.

Deliberately **not** a constant in this module. A table of unit names and member ids is
somebody's setting, and a kernel that ships with one baked in is a kernel every other
deployment has to edit out. Without the file every character's `unit` is None: the filter
panel groups nothing, the rest works. See `characters/units.example.toml`.
"""


def _read_units(root: Path) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """The unit table, or nothing. A missing or broken file is not an error — it is a
    deployment that did not write one, and every character simply has no unit."""
    path = root / UNITS_FILE
    if not path.exists():
        return ()
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return ()
    out: list[tuple[str, str, tuple[str, ...]]] = []
    for entry in data.get("units", ()):
        name = str(entry.get("name") or "").strip()
        members = tuple(
            str(one).strip() for one in entry.get("members", ()) if str(one).strip()
        )
        if name and members:
            out.append((name, str(entry.get("reading") or "").strip(), members))
    return tuple(out)


@dataclass(frozen=True)
class Character:
    id: str
    name: str
    name_zh: str
    intro: str
    color: str
    sheet: str
    """The sheet's text, not its path: read once at startup, as upstream does."""
    avatar: Path | None = None
    scsp_avatar: Path | None = None
    voice_id: str = ""
    name_kana: str = ""
    name_romaji: str = ""
    unit: str | None = None
    """Her unit's name; None where the table above does not place her."""
    unit_reading: str | None = None
    unit_order: int | None = None
    """Where her unit is in the official order; None without one."""
    liked_gifts: tuple[str, ...] = ()
    """The gifts she likes, by identifier in the gift catalog; giving her one raises
    affinity more. Never told to the user."""

    adult: bool | None = None
    """Has she cleared the line? 高中生及以上 = true，初中生・小学生 = false。

    `None` is 「nobody wrote it down」 — **not** 「no」. 这条线是**学段**不是民法成年：
    15 岁既可能是初三也可能是高一，光看 `age` 分不出来，所以这里一个个填。
    The policy for a missing answer (safe by default = treat as underage) belongs to the
    channel that needs it, not to the roster. See `channels/romance.py::grown`."""
    age: int | None = None
    """Her age as the character is written, not as of today. The setting's age does not move
    with the calendar, and neither should this."""
    plove: int = 3
    """0–5: how far she would ever go toward her producer.

    0 is not 「hard」 but 「there is no such line」 — see `channels/romance.py`. 3 is the
    neutral default for a character nobody has judged."""
    heavy: int = 3
    """1–5 of 「重」: how much weight she puts on him — strength only, never direction.

    A 5 is 「把你照顾得无微不至」 or 「两小时没回就发十条」 depending on who she is; that
    difference is written in her own file, not here."""

    def brief(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "name_zh": self.name_zh,
            "name_kana": self.name_kana,
            "name_romaji": self.name_romaji,
            "intro": self.intro,
            "color": self.color,
            "avatar": bool(self.avatar),
            "scsp_avatar": bool(self.scsp_avatar),
            "unit": self.unit,
            "unit_reading": self.unit_reading,
            "unit_order": self.unit_order,
        }


class Characters(Plugin):
    name = "characters"

    def __init__(self, directory: str | Path, *, config: str = "characters.toml") -> None:
        self._root = Path(directory)
        self._config = self._root / config
        self._units = _read_units(self._root)
        self._all: tuple[Character, ...] = _load(self._root, self._config, self._units)
        if not self._all:
            raise RuntimeError(
                f"{self._root} 里没有角色。先建一个角色表：\n"
                f"  cp {self._root}/characters.example.toml {self._root}/characters.toml\n"
                "然后把它改成你自己的人——字段说明见 characters/README.md。"
            )
        self._by_id = {character.id: character for character in self._all}
        self._paths = _sheet_paths(self._root, self._config)
        self._stamp = self._signature()

    # ---- keeping up with edits on disk

    def stale(self) -> bool:
        """Whether a sheet or the roster table changed since it was last read."""
        return self._signature() != self._stamp

    def reload(self) -> bool:
        """Re-read everything, and keep the old roster if the new one is unusable.

        A half-saved `characters.toml` parses as nothing, and dropping every character
        because of one bad edit would take the page down. So a failed or empty load is
        refused, and the previous roster stays in place until the file is valid again.
        """
        try:
            found = _load(self._root, self._config)
        except Exception:
            return False
        if not found:
            return False
        self._all = found
        self._by_id = {character.id: character for character in found}
        self._paths = _sheet_paths(self._root, self._config)
        self._stamp = self._signature()
        return True

    def _signature(self) -> tuple:
        out: list[tuple[str, int, int]] = []
        for path in (self._config, self._root / UNITS_FILE, *self._paths.values()):
            try:
                info = path.stat()
                out.append((str(path), info.st_mtime_ns, info.st_size))
            except OSError:
                out.append((str(path), 0, 0))
        return tuple(out)

    # ---- what the page and the turn loop ask for

    @property
    def roster(self) -> tuple[Character, ...]:
        return self._all

    def character(self, character_id: str | None = None) -> Character:
        """One of them; the first when nobody said which, so a bare `GET /api/state`
        still answers."""
        if character_id:
            found = self._by_id.get(character_id)
            if found is None:
                raise KeyError(character_id)
            return found
        return self._all[0]

    def avatar(self, character_id: str, *, scsp: bool = False) -> Path | None:
        if character_id not in self._by_id:
            return None
        character = self._by_id[character_id]
        if scsp:
            return character.scsp_avatar or character.avatar
        return character.avatar

    def name_of(self, character_id: str) -> str | None:
        """Her display name, or None for an identifier this deployment does not know —
        what a memory told to a character no longer here is labelled with."""
        character = self._by_id.get(character_id)
        return character.name if character else None

    def capabilities(self) -> dict[str, object]:
        return {
            "count": len(self._all),
            "default": self._all[0].id,
            "colors": True,
        }

    def routes(self) -> dict[tuple[str, str], Route]:
        async def characters(query, body):  # noqa: ANN001 - the shape is in plugin.Route
            return {"characters": [character.brief() for character in self._all]}

        async def character(query, body):  # noqa: ANN001
            try:
                found = self.character(_q(query, "c") or None)
            except KeyError:
                return {"error": "没有这个角色"}
            return found.brief()

        return {("GET", "/api/characters"): characters, ("GET", "/api/character"): character}


def _q(query, name: str) -> str:  # noqa: ANN001 - a flat dict of first values
    return str(query.get(name, ""))


def _load(
    root: Path,
    config: Path,
    units: tuple[tuple[str, str, tuple[str, ...]], ...] = (),
) -> tuple[Character, ...]:
    if config.exists():
        with config.open("rb") as file:
            data = tomllib.load(file)
        return tuple(
            _from_entry(root, entry, units)
            for entry in data.get("characters", ())
            if str(entry.get("id") or "").strip()
        )
    return _from_sheets(root)


def _from_entry(
    root: Path,
    entry: dict,
    units: tuple[tuple[str, str, tuple[str, ...]], ...] = (),
) -> Character:
    """One `[[characters]]` block. Paths in the file are relative to the file, and a
    missing avatar or sheet is a fact rather than an error: a character without a picture
    is a character without a picture."""
    sheet_path = _path(root, entry.get("sheet"))
    sheet = _text(sheet_path) if sheet_path else ""
    if not sheet:
        sheet = _text(root / SHEETS / f"{entry['id']}.md")
    if not sheet:
        sheet = f"# 人设：{entry.get('name', entry['id'])}\n\n（这个人设还没有写。）\n"
    avatar = _path(root, entry.get("avatar"))
    scsp = _path(root, entry.get("scsp_avatar"))
    character_id = str(entry["id"])
    unit, reading, order = _unit_of(character_id, units)
    return Character(
        id=character_id,
        name=str(entry.get("name") or entry["id"]),
        name_zh=str(entry.get("name_zh") or ""),
        name_kana=str(entry.get("name_kana") or ""),
        name_romaji=str(entry.get("name_romaji") or ""),
        intro=str(entry.get("intro") or ""),
        color=_color(str(entry.get("color") or "")),
        sheet=sheet,
        avatar=avatar if avatar and avatar.exists() else None,
        scsp_avatar=scsp if scsp and scsp.exists() else None,
        voice_id=str(entry.get("voice_id") or ""),
        unit=unit,
        unit_reading=reading,
        unit_order=order,
        liked_gifts=_liked(entry.get("liked_gifts")),
        adult=_flag(entry.get("adult")),
        age=_int_or_none(entry.get("age")),
        plove=_level(entry.get("plove"), 3, low=0, high=5),
        heavy=_level(entry.get("heavy"), 3, low=1, high=5),
    )


def _flag(value: object) -> bool | None:
    """`true` / `false` / absent. A typo is 「nobody wrote it down」, not a silent `false`."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "yes", "1", "adult", "成年"):
            return True
        if text in ("false", "no", "0", "minor", "未成年"):
            return False
    return None


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _level(value: object, default: int, *, low: int, high: int) -> int:
    """A 0–5 / 1–5 grade. Out of range is clamped rather than fatal: a wrong number in the
    roster must not stop a character from talking, and the honest fallback for 「nonsense」
    is the neutral middle."""
    number = _int_or_none(value)
    if number is None:
        return default
    return min(max(number, low), high)


def _liked(value: object) -> tuple[str, ...]:
    """`liked_gifts` from the configuration: a list of catalog identifiers, at most
    `MAX_LIKED` of them, duplicates dropped. Anything else is a configuration mistake the
    gift catalog reports, not a reason to fail here."""
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip() and item.strip() not in out:
            out.append(item.strip())
    return tuple(out[:3])


def _unit_of(
    character_id: str,
    units: tuple[tuple[str, str, tuple[str, ...]], ...] = (),
) -> tuple[str | None, str | None, int | None]:
    """Her unit's name, its reading in hiragana, and where it is in the declared order;
    all None where the deployment's `units.toml` does not place her."""
    for order, (name, reading, members) in enumerate(units):
        if character_id in members:
            return name, reading, order
    return None, None, None


def _sheet_paths(root: Path, config: Path) -> dict[str, Path]:
    """Where each character's sheet lives, so a change can be noticed without re-reading
    every file. Mirrors the fallback order in `_from_entry`: the path in the table, else
    `sheets/<id>.md`, else the flat `*.md` this directory happens to have."""
    paths: dict[str, Path] = {}
    if config.exists():
        try:
            with config.open("rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            return paths
        for entry in data.get("characters", ()):
            character_id = str(entry.get("id") or "").strip()
            if not character_id:
                continue
            found = _path(root, entry.get("sheet")) or (root / SHEETS / f"{character_id}.md")
            paths[character_id] = found
        return paths
    for file in sorted(root.glob("*.md")) or sorted((root / SHEETS).glob("*.md")):
        paths[file.stem] = file
    return paths


_NOT_A_CHARACTER = re.compile(
    r"^(readme|changelog|contributing|license|index|example.*|.*\\.example)$", re.I
)
"""Files that sit next to the sheets but are not one.

A directory that ships with a `README.md` used to wake up with a character called 「README」:
a bare deployment looked like it had somebody in it, and the 「no characters」 error — the one
that tells you to copy the example — never fired."""

def _from_sheets(root: Path) -> tuple[Character, ...]:
    """No `characters.toml`: every `*.md` is a character, named by its heading. This is
    what a bare directory — a test, a demo, a half-copied deployment — still gives you."""
    files = [
        file
        for file in (sorted(root.glob("*.md")) or sorted((root / SHEETS).glob("*.md")))
        if not _NOT_A_CHARACTER.match(file.stem) and not file.name.startswith(".")
    ]
    return tuple(
        Character(
            id=file.stem,
            name=_name_of(_text(file)) or file.stem,
            name_zh="",
            intro="",
            color=FALLBACK_COLOR,
            sheet=_text(file),
        )
        for file in files
        if _text(file).strip()
    )


def _path(root: Path, value: object) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    return path if path.is_absolute() else (root / path)


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _name_of(sheet: str) -> str:
    for line in sheet.splitlines():
        if line.startswith("# 人设："):
            return line[len("# 人设：") :].strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _color(value: str) -> str:
    """`#rrggbb` or the fallback: a colour from the file reaches CSS, so nothing else is
    allowed through."""
    if len(value) == 7 and value.startswith("#"):
        try:
            int(value[1:], 16)
        except ValueError:
            return FALLBACK_COLOR
        return value.lower()
    return FALLBACK_COLOR


def load_problems(characters: Sequence[Character]) -> list[str]:
    """What a deployment would want to know at startup. Kept tiny on purpose: a sheet that
    is missing is the only thing that changes behaviour."""
    return [character.id for character in characters if not character.sheet.strip()]
