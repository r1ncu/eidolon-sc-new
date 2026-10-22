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
UNITS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("illumination STARS", "いるみねーしょんすたーず", ("mano", "hiori", "meguru")),
    ("L'Antica", "あんてぃーか", ("kogane", "mamimi", "sakuya", "yuika", "kiriko")),
    ("放課後クライマックスガールズ", "ほうかごくらいまっくすがーるず", ("kaho", "chiyoko", "juri", "rinze", "natsuha")),
    ("ALSTROEMERIA", "あるすとろめりあ", ("amana", "tenka", "chiyuki")),
    ("Straylight", "すとれいらいと", ("asahi", "fuyuko", "mei")),
    ("noctchill", "のくちる", ("toru", "madoka", "koito", "hinana")),
    ("SHHis", "しーず", ("nichika", "mikoto")),
    ("CoMETIK", "こめてぃっく", ("luca", "hana", "haruki")),
)


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
        self._all: tuple[Character, ...] = _load(self._root, self._config)
        if not self._all:
            raise RuntimeError(f"{self._root} 里没有角色（characters.toml 或 *.md）")
        self._by_id = {character.id: character for character in self._all}

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


def _load(root: Path, config: Path) -> tuple[Character, ...]:
    if config.exists():
        with config.open("rb") as file:
            data = tomllib.load(file)
        return tuple(
            _from_entry(root, entry)
            for entry in data.get("characters", ())
            if str(entry.get("id") or "").strip()
        )
    return _from_sheets(root)


def _from_entry(root: Path, entry: dict) -> Character:
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
    unit, reading, order = _unit_of(character_id)
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
    )


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


def _unit_of(character_id: str) -> tuple[str | None, str | None, int | None]:
    """Her unit's name, its reading in hiragana, and where it is in the official order;
    all None where `UNITS` does not place her."""
    for order, (name, reading, members) in enumerate(UNITS):
        if character_id in members:
            return name, reading, order
    return None, None, None


def _from_sheets(root: Path) -> tuple[Character, ...]:
    """No `characters.toml`: every `*.md` is a character, named by its heading. This is
    what a bare directory — a test, a demo, a half-copied deployment — still gives you."""
    files = sorted(root.glob("*.md")) or sorted((root / SHEETS).glob("*.md"))
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
