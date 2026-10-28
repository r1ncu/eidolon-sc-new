"""What she knows about the office, in three sizes.

She has to recognise twenty-seven colleagues, the office staff, and a handful of things that
are true about her work rather than about her life. Putting all of that in the prompt every
turn would cost more than it is worth; putting none of it in means she misnames people in
front of fifty witnesses. So it is three layers:

    office.md                 everyone, one line each      every turn, cached with the sheet
    idols/<id>.md             one colleague, in detail     when that name is said
    meta.md                   work, events, the people who make them   when that is asked about

The middle layer is looked up by name, not by meaning: a deterministic match on the names in
`characters.toml`. That is deliberate. An embedding lookup can return 三峰結華 for a question
about 幽谷霧子, and being confidently wrong about a colleague is worse than having only the
one-line version. Missing a lookup is safe; a false one is not.

Everything here is read through `core.live`, so these files are editable while she is
running: the roster is re-read on the next turn, and one colleague's file the next time that
name comes up. `core.live` is what makes that safe — a file that is empty or mid-write is
ignored and the previous version is kept, so an editor's autosave cannot blank her memory of
the office for one turn.
"""

import tomllib
from pathlib import Path
from typing import Any

from core.live import Live
from core.prompt import Section, Slot

META_WORDS = (
    # 作品本身：活动、发行、制作方。命中才注入，未命中的话题不带这层。
    "生放送",
    "高山",
    "アニメ",
    "动画",
    "声優",
    "声优",
    "ゲーム",
    "游戏",
    "シングル",
    "单曲",
    "アルバム",
    "专辑",
    "ライブ",
    "演唱会",
    "イベント",
    "活动",
    "コラボ",
    "联动",
    "開発",
    "开发",
    "中の人",
    "シャニマス",
    "シャイニーカラーズ",
    "ブランド",
)

MAX_IDOL = 1600
"""Characters of one colleague's file that may enter a turn. Longer files are cut, not
summarised: a summary written by this file would be a second author of her memory of them."""

MAX_META = 2200

MAX_HITS = 2
"""Two colleagues in one message is already a strange message; more is a list, not a question."""


class World:
    def __init__(
        self,
        characters: str | Path,
        *,
        config: str | Path | None = None,
        meta: bool = True,
        limit: int = MAX_HITS,
        settle: float = 0.35,
        log: Any | None = None,
    ) -> None:
        self._root = Path(characters)
        self._world = self._root / "world"
        self._meta_on = meta
        self._limit = limit
        self._log = log or (lambda line: None)
        self._office = Live(self._world / "office.md", settle=settle, log=self._log)
        self._meta = Live(self._world / "meta.md", settle=settle, log=self._log) if meta else None
        self._names = Live(
            self._root / (Path(config).name if config else "characters.toml"),
            settle=settle,
            log=self._log,
        )
        self._aliases: dict[str, tuple[str, ...]] = {}
        self._aliases_stamp: tuple = ()
        self._idols: dict[str, Live] = {}

    def refresh(self) -> None:
        """Re-read the roster, the work notes and every colleague now."""
        self._office.refresh(force=True)
        if self._meta is not None:
            self._meta.refresh(force=True)
        self._names.refresh(force=True)
        for file in self._idols.values():
            file.refresh(force=True)
        self._aliases_stamp = ()

    def stamp(self) -> tuple:
        """Changes when a file that feeds the *persona* changes. Colleagues' files are not in
        it: they arrive as turn sections, which are rebuilt every turn anyway."""
        return (self._office.stamp(), self._meta.stamp() if self._meta else (0, 0),
                self._names.stamp())

    @property
    def ready(self) -> bool:
        """Whether the roster file was found. Without it she still talks, from her sheet."""
        return bool(self._office.text())

    def persona(self) -> Section | None:
        """The one-line-each roster, for the engine the channel builds."""
        text = self._office.text()
        if not text:
            return None
        return Section(Slot.PERSONA, text, order=10, title="world:office")

    def lookup(self, text: str, *, speaking: str = "") -> list[Section]:
        """Colleagues named in the message, in the order they are named.

        `speaking` is who is answering, so that a message mentioning her own name does not
        drag her own colleague file into the turn. Empty means "nobody in particular": the
        caller that knows the character always passes it.
        """
        said = (text or "").casefold()
        if not said.strip():
            return []
        hits: list[tuple[int, str]] = []
        for character_id, aliases in self._alias_table().items():
            if character_id == speaking:
                continue
            spot = min((said.find(alias) for alias in aliases if alias and alias in said), default=-1)
            if spot >= 0:
                hits.append((spot, character_id))
        sections: list[Section] = []
        for _, character_id in sorted(hits)[: self._limit]:
            detail = self._idol(character_id)
            if detail:
                sections.append(
                    Section(Slot.TURN, detail[:MAX_IDOL], title=f"world:idol:{character_id}")
                )
        return sections

    def knowledge(self, text: str) -> list[Section]:
        """The work itself — releases, events, the people who make them."""
        meta = self._meta.text() if self._meta is not None else ""
        if not self._meta_on or not meta:
            return []
        said = (text or "").casefold()
        if not any(word.casefold() in said for word in META_WORDS):
            return []
        return [Section(Slot.TURN, meta[:MAX_META], title="world:meta")]

    def _idol(self, character_id: str) -> str:
        file = self._idols.get(character_id)
        if file is None:
            file = Live(
                self._world / "idols" / f"{character_id}.md", settle=0.0, log=self._log
            )
            self._idols[character_id] = file
        return file.text()

    def _alias_table(self) -> dict[str, tuple[str, ...]]:
        """The names in `characters.toml`, re-parsed when the file changes."""
        stamp = self._names.stamp()
        if stamp != self._aliases_stamp:
            self._aliases = _parse_aliases(self._names.text())
            self._aliases_stamp = stamp
        return self._aliases


def _parse_aliases(text: str) -> dict[str, tuple[str, ...]]:
    """Every way a name is written, per character, lower-cased.

    Full names only. A given name alone (「透」「凛世」) is left out on purpose: in Chinese
    prose those characters appear inside ordinary words, and a wrong match would put another
    idol's history into a turn about something else entirely.
    """
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for character in data.get("characters", []):
        character_id = str(character.get("id") or "")
        if not character_id:
            continue
        names = [
            character.get("name"),
            character.get("name_zh"),
            character.get("name_romaji"),
            character.get("name_kana"),
            character_id,
        ]
        out[character_id] = tuple(
            str(name).casefold().replace(" ", "") for name in names if name
        )
    return out



