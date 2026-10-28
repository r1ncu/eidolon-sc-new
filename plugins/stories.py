"""What she lived through, found by topic instead of pasted whole.

The story corpus is 1.1MB of scene text: far too much for a prompt, and most of it
irrelevant to any one sentence. So it stays on disk until a topic is named, and
then one or two scenes ride along in the turn — the trade `channels/world.py`
makes with a colleague's file, for the same reason: a deterministic match on what
was actually said. Missing a lookup is safe (she answers from her sheet and her
facts); a false one is not (she would talk about a scene nobody mentioned).

Three keyword sources, the hand-written one first:

    the topic table    `world/<id>-stories.toml`: the words people actually say
                       （甜点 / クリスマス / 数羊） and the scenes they point at
    the card name      【おっきくならなくちゃ】, for a fan who names the card
    the scene title    「ほら、取り零した」, for someone quoting her own words

The judge gate rides on the request the affinity question already makes:
`stories.needed` asks whether this turn is about something she lived through, and
one extra key on an existing request costs nothing. A turn that says 雪 about the
weather carries no scenes; a judge that failed to answer means the scenes go in
anyway, because a turn that missed what it needed is worse than one that carried
a paragraph too many.

Nothing here may fail a turn. A missing corpus, an unreadable table, a scene that
will not parse — each costs the turn its excerpts, never its reply.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from core.live import Live
from core.plugin import Plugin, Question, Route, TurnContext
from core.prompt import Section, Slot

MAX_TOPICS = 2
"""Topics that may speak in one turn. Two is already a strange sentence."""

MAX_SCENES = 3
"""Scenes in one turn. A scene is a whole 話 of dialogue; three is a lot of reading."""

MAX_CHARS = 1500
"""Characters of scene text in one turn, the whole section counted."""

PER_SCENE = 700
"""Characters of one scene. Longer scenes are cut at a line, never mid-sentence."""

_HEADING = "# 想起来的剧情"

_NOTE = (
    "下面几段是她真实经历过的事的原文（日文，附汉化）。话题撞上了，就当作自己想起来的那样"
    "自然地用上——原话和语气都可以化用，不要整段复述，也不要提「剧情」「原文」「设定」"
    "这类词。没撞上的部分就当没看见。"
)

_NAME_TAIL = ("黛冬優子", "黛 冬優子", "黛冬优子")

_HAS_CJK = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_HAS_MARK = re.compile(r"[^\x00-\x7f]")


@dataclass(frozen=True)
class Scene:
    """One 話, with the card it belongs to and the heading it sat under."""

    file: str
    card: str
    title: str
    text: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.file, self.title)

    @property
    def label(self) -> str:
        """The card and the scene, for her and for the log. The heading's first
        half is the name a person would say; the second is often a file id."""
        title = self.title.split(" / ")[0].strip() if " / " in self.title else self.title
        title = title if len(title) <= 24 else title[:24] + "…"
        return f"【{self.card} / {title}】" if self.card else f"【{title}】"

    @property
    def long_label(self) -> str:
        title = self.title if len(self.title) <= 40 else self.title[:40] + "…"
        return f"【{self.card} / {title}】" if self.card else f"【{title}】"


@dataclass(frozen=True)
class Topic:
    """Words that name a subject, and where that subject was lived."""

    name: str
    words: tuple[str, ...]
    where: tuple[tuple[str, str], ...]
    """`(file, scene title fragment)` per scene; an empty fragment means the file."""


class Stories(Plugin):
    """Her story corpus, one or two scenes at a time."""

    name = "stories"

    def __init__(
        self,
        directory: str | Path,
        *,
        on: bool = True,
        top: int = MAX_TOPICS,
        settle: float = 0.35,
        log: object | None = None,
    ) -> None:
        self._root = Path(directory)
        self._on = bool(on)
        self._top = max(1, top)
        self._log = log or (lambda line: print(line, flush=True))
        self._maps: dict[str, Live] = {}
        self._topic_cache: dict[str, tuple[tuple, tuple[Topic, ...]]] = {}
        self._indexes: dict[str, tuple[tuple, list[Scene], list[tuple[Scene, tuple[str, ...]]]]] = {}

    # ---- what the page shows, and what the kernel asks

    def capabilities(self) -> dict[str, object]:
        return {"on": self._on}

    def judge_questions(self) -> tuple[Question, ...]:
        """One question, on the request the judge already makes: is this turn about
        something she lived through? Upstream asks JEV; any judge model answers here."""
        if not self._on:
            return ()
        return (
            Question(
                "stories.needed",
                "这一句是不是在问她过去经历过的事、她的喜好和过去，或者要她的往事才答得上？"
                "寒暄、当下的心情、今天的安排都不算",
                kind="bool",
            ),
        )

    async def prompt_sections(self, turn: TurnContext) -> tuple[Section, ...]:
        if not self._on:
            return ()
        # A missing answer is a failed judge, and a failed judge means look anyway.
        if turn.facts.get("stories.needed") is False:
            return ()
        scenes = self._match(turn.conversation.character_id, turn.text)
        if not scenes:
            return ()
        self._log(
            f"stories: {turn.conversation.character_id} 带上了 {len(scenes)} 段剧情"
            f"（{'、'.join(scene.label for scene in scenes)}）"
        )
        return (Section(Slot.TURN, _render(scenes), order=25, title="stories"),)

    # ---- the match

    def _match(self, character_id: str, text: str) -> list[Scene]:
        """The scenes this sentence is about, in table order, capped. Pure string
        matching on purpose: the topics are the only thing that decides."""
        if not self._on:
            return []
        said = (text or "").casefold()
        if not said.strip():
            return []
        try:
            scenes, auto = self._index(character_id)
            topics = self._topics(character_id)
        except Exception:  # an index that cannot be built costs the turn its scenes
            return []
        found: list[Scene] = []
        seen: set[tuple[str, str]] = set()
        used = 0
        for topic in topics:
            if used >= self._top or len(found) >= MAX_SCENES:
                break
            if not any(word.casefold() in said for word in topic.words):
                continue
            used += 1
            for file, fragment in topic.where:
                for scene in _pick(scenes, file, fragment):
                    if scene.key in seen:
                        continue
                    seen.add(scene.key)
                    found.append(scene)
                    if len(found) >= MAX_SCENES:
                        break
                if len(found) >= MAX_SCENES:
                    break
        if len(found) < MAX_SCENES:
            # A card name or a scene title said out loud: nobody had to write a topic
            # for it, which is what keeps the table from having to be exhaustive.
            for scene, words in auto:
                if len(found) >= MAX_SCENES:
                    break
                if scene.key in seen:
                    continue
                if any(word in said for word in words):
                    seen.add(scene.key)
                    found.append(scene)
        return found[:MAX_SCENES]

    # ---- reading the corpus without a service

    def _index(self, character_id: str) -> tuple[list[Scene], list[tuple[Scene, tuple[str, ...]]]]:
        """`facts/<id>-stories/` as scenes, re-scanned when a file changes. The scan
        is a handful of small reads; a turn that pays for it once pays milliseconds."""
        root = self._root / "facts" / f"{character_id}-stories"
        stamp = _stamp(root)
        cached = self._indexes.get(character_id)
        if cached is not None and cached[0] == stamp:
            return cached[1], cached[2]
        scenes = _scan(root)
        auto = _auto(scenes)
        self._indexes[character_id] = (stamp, scenes, auto)
        return scenes, auto

    def _topics(self, character_id: str) -> tuple[Topic, ...]:
        """The hand-written table, re-parsed when the file changes. Read through
        `core.live`, so an edit is picked up on the next turn without a restart."""
        live = self._maps.get(character_id)
        if live is None:
            live = Live(
                self._root / "world" / f"{character_id}-stories.toml",
                settle=0.35,
                log=self._log,
            )
            self._maps[character_id] = live
        stamp = live.stamp()
        cached = self._topic_cache.get(character_id)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        topics = _parse_topics(live.text())
        self._topic_cache[character_id] = (stamp, topics)
        return topics

    # ---- the page's own endpoint

    def routes(self) -> dict[tuple[str, str], Route]:
        async def state(query, body):  # noqa: ANN001 - the shape is in plugin.Route
            character_id = str(query.get("c") or "")
            if not character_id:
                return {"error": "需要 c（角色）"}
            scenes, _ = self._index(character_id)
            topics = self._topics(character_id)
            said = str(query.get("q") or "")
            picked = self._match(character_id, said) if said else []
            return {
                "on": self._on,
                "files": len({scene.file for scene in scenes}),
                "scenes": len(scenes),
                "topics": [topic.name for topic in topics],
                "matched": [scene.label for scene in picked],
                "section": _render(picked) if picked else "",
            }

        return {("GET", "/api/stories"): state}


def _scan(root: Path) -> list[Scene]:
    """Every 話 in the corpus, in file order. A file that will not read is skipped,
    not fatal: one broken file costs the turns its scenes. `README.md` is the corpus's
    own documentation — its headings are not scenes."""
    out: list[Scene] = []
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        parts = re.split(r"^## ", text, flags=re.M)
        card = _card_name(parts[0])
        for part in parts[1:]:
            title, _, body = part.partition("\n")
            title, body = title.strip(), _clean(body)
            if title and body:
                out.append(Scene(rel, card, title, body))
    return out


def _auto(scenes: list[Scene]) -> list[tuple[Scene, tuple[str, ...]]]:
    """Card names and scene titles as keywords. Only names that carry a Japanese
    or Chinese character survive: an English title is noise in a Chinese room
    («get back», «Early stage»), and a wrong hit is worse than a missed one."""
    per_file: dict[str, set[str]] = {}
    per_scene: dict[tuple[str, str], set[str]] = {}
    for scene in scenes:
        if _named(scene.card):
            per_file.setdefault(scene.file, set()).add(scene.card)
        words = per_scene.setdefault(scene.key, set())
        words.update(_title_words(scene.title))
    return [
        (scene, tuple(sorted(per_file.get(scene.file, set()) | per_scene.get(scene.key, set()))))
        for scene in scenes
    ]


def _named(word: str) -> bool:
    return bool(word) and bool(_HAS_CJK.search(word) or _HAS_MARK.search(word))


def _title_words(title: str) -> set[str]:
    """A heading like `2022情人节 / 【黛 冬優子】2022 バレンタイン準備` gives the
    whole thing, each side of the slash, and the side without its year — so saying
    情人节 finds it without typing the year."""
    out: set[str] = set()
    for piece in re.split(r"\s*/\s*", title):
        piece = re.sub(r"【[^】]*】", "", piece).strip()
        if not piece:
            continue
        for variant in (piece, re.sub(r"^\d{4}", "", piece).strip()):
            if _named(variant) and "_" not in variant and len(variant) >= 2:
                out.add(variant)
    return out


def _pick(scenes: list[Scene], file: str, fragment: str) -> list[Scene]:
    """The scenes of `file`, or just the ones whose heading carries `fragment`."""
    out = []
    for scene in scenes:
        if scene.file != file:
            continue
        if fragment and fragment not in scene.title:
            continue
        out.append(scene)
    return out


def _card_name(header: str) -> str:
    """`# 【オ♡フ♡レ♡コ】黛 冬優子` gives `オ♡フ♡レ♡コ`; a chapter file keeps its name.
    Only the first line: what follows it is the file's own preamble, not its name."""
    first = header.strip().splitlines()[0] if header.strip() else ""
    text = first.lstrip("#").strip()
    found = re.match(r"^【(.+?)】", text)
    if found:
        return found.group(1).strip()
    for tail in _NAME_TAIL:
        text = text.replace(tail, "")
    return text.strip()


def _clean(body: str) -> str:
    """The scene's dialogue, without the file's scaffolding: the `*原始剧情 …*`
    note and the `---` rules are for a reader of the corpus, not for her."""
    keep = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped == "---":
            continue
        if stripped.startswith("*") and not stripped.startswith("**"):
            continue
        keep.append(line.rstrip())
    return "\n".join(keep).strip()


def _parse_topics(text: str) -> tuple[Topic, ...]:
    """The table as topics. A table that will not parse gives no topics: the card
    names and scene titles still match, and the turn still answers."""
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return ()
    out: list[Topic] = []
    for row in data.get("topic") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        words = tuple(str(word) for word in (row.get("words") or []) if str(word).strip())
        where: list[tuple[str, str]] = []
        for item in row.get("from") or []:
            if isinstance(item, str) and item.strip():
                where.append((item.strip(), ""))
            elif isinstance(item, dict):
                file = str(item.get("file") or "").strip()
                if file:
                    where.append((file, str(item.get("scene") or "").strip()))
        if name and words and where:
            out.append(Topic(name, words, tuple(where)))
    return tuple(out)


def _stamp(root: Path) -> tuple:
    """When the corpus last changed, so an edit is picked up on the next turn
    without a restart and without re-reading what did not change."""
    newest = 0
    try:
        for path in root.rglob("*.md"):
            try:
                newest = max(newest, path.stat().st_mtime_ns)
            except OSError:
                continue
    except OSError:
        return ()
    return (newest,)


def _cut(text: str, limit: int) -> str:
    """At most `limit` characters, cut at a line: half a line of dialogue means
    nothing, and a scene cut mid-sentence reads like a transmission error."""
    if len(text) <= limit:
        return text
    kept: list[str] = []
    used = 0
    for line in text.splitlines():
        if used + len(line) + 1 > limit:
            break
        kept.append(line)
        used += len(line) + 1
    if not kept:
        return text[:limit].rstrip() + "……"
    return "\n".join(kept).rstrip() + "\n……"


def _render(scenes: list[Scene]) -> str:
    """The section: the note, then the scenes, within the budget. A scene that
    does not fit is left out rather than squeezed: the next turn may want it."""
    lines = [_HEADING, _NOTE]
    labels = [scene.label for scene in scenes]
    # 同一场戏的几话标题常长得一样（2019 圣诞有四话）：撞名了就带全称，免得看起来像重复。
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    budget = MAX_CHARS
    for scene, label in zip(scenes, labels):
        if counts[label] > 1:
            label = scene.long_label
        room = budget - len(label) - 2
        if room < 120:
            break
        text = _cut(scene.text, min(PER_SCENE, room))
        budget -= len(label) + len(text) + 2
        lines.append("")
        lines.append(label)
        lines.append(text)
        if budget <= 0:
            break
    return "\n".join(lines)
