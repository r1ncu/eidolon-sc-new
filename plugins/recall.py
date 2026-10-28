"""What she knows about herself: pasted whole, or looked up by meaning.

Upstream embeds the character's facts — `characters/facts/<id>.md`, about 8KB each — and
the story corpus, finds the nearest passages with the query vector, reranks them, and
lets the judge model decide whether a turn needs any of it at all. This is that, cut down
to what one small machine can run with no dependency: the facts file beside the sheet, one
OpenAI-compatible `/embeddings` service, cosine instead of a reranker, and the judge gate
kept because it is the cheap half and the half that saves money.

Three modes, chosen by `SCCORE_RECALL`:

    off      nothing but the sheet — the default, and all a deployment with no facts has
    whole    the facts file, in full, in the cached persona slot: no service, no per-turn
             cost, and the model reads everything whether it needed it or not
    vector   the facts split into passages and embedded once; each turn carries the few
             nearest, which is the difference between 8KB and 1KB of prompt per turn and
             the only version that still works when the file grows to a corpus

`SCCORE_RECALL_GATE=judge` asks the judge whether the turn needs them at all — upstream
asks JEV, `typesafe/jev-1.13` through OpenRouter, and any OpenAI-compatible judge model
does here, since it is one more question on the request the judge already makes. A judge
that fails to answer means the turn looks anyway: a turn that missed what it needed is
worse than one that looked for nothing.

Nothing here may fail a turn. An embedding service that is down costs this turn its
passages, not its reply, and the failures are left in the service status so the page can
say so.

Where this deliberately differs from upstream: no story corpus (unlicensed, and 47MB of
CSV is a deployment of its own), no reranker, and no `recall_story` tool. The facts file,
the embedding and the gate are the parts that change what she says.
"""

import asyncio
import json
import logging
import math
import threading
import urllib.request
from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from core.plugin import Plugin, Question, Route, TurnContext
from core.prompt import Section, Slot
from core.store import Store

CHUNK = 700
"""Roughly how many characters of a facts file go into one passage. Big enough that a
passage means something on its own, small enough that four of them are cheaper than the
whole file."""

TOP = 4
"""How many passages a turn carries."""

MIN_SCORE = 0.35
"""The lowest cosine a passage may have and still go in. Low on purpose: the embedding
models differ, and a threshold that hides everything is worse than one that shows a
paragraph too many. Raise it (`SCCORE_RECALL_MIN`) on a deployment where she quotes
herself too much."""

BATCH = 16
"""Passages sent to the embedding service in one request, as upstream sends them."""

TIMEOUT = 30.0
"""Seconds one embedding request may take before the turn goes on without it."""

DEFAULT_MODEL = "Qwen/Qwen3-Embedding-0.6B"

_HEADING = "# 关于你自己的资料"

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS recall_vectors ("
    " character_id TEXT NOT NULL,"
    " model TEXT NOT NULL,"
    " text TEXT NOT NULL,"
    " vec BLOB NOT NULL,"
    " PRIMARY KEY (character_id, model, text))",
)

log = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    """The embedding service refused, or answered with something that is not vectors."""


@dataclass(frozen=True)
class Passage:
    """One piece of a facts file, with the heading it sat under, for the prompt and for
    the cache key."""

    heading: str
    text: str


class OpenAIEmbeddings:
    """`POST /embeddings`, the shape every provider speaks: OpenAI, SiliconFlow, Jina,
    DeepInfra, Ollama, LM Studio.

    Blocking on purpose: the caller runs it on a thread, as the chat client does, because
    the turn loop must not wait on a socket. `open` is the seam the tests use — given the
    request body, return the decoded answer — so nothing above it knows whether the bytes
    came from a service or a string.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = TIMEOUT,
        opener: object | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.model = model
        self._timeout = timeout
        self._open = opener or self._post

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in the order given."""
        if not texts:
            return []
        body = {"model": self.model, "input": list(texts)}
        try:
            payload = self._open(body)
        except Exception as exc:
            raise EmbeddingError(f"{type(exc).__name__}: {exc}") from exc
        if isinstance(payload, bytes):
            payload = json.loads(payload.decode("utf-8", "replace"))
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise EmbeddingError("the service answered without data")
        try:
            ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
            vectors = [[float(value) for value in item["embedding"]] for item in ordered]
        except (TypeError, KeyError, ValueError) as exc:
            raise EmbeddingError(f"unreadable vectors: {exc}") from exc
        if len(vectors) != len(texts):
            raise EmbeddingError(f"asked for {len(texts)} vectors, got {len(vectors)}")
        if not all(len(vector) == len(vectors[0]) for vector in vectors):
            raise EmbeddingError("the vectors differ in length")
        return vectors

    def _post(self, body: dict) -> dict:
        request = urllib.request.Request(
            f"{self._base_url}/embeddings",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))


class Recall(Plugin):
    """Her facts, in whichever of the three shapes the deployment chose."""

    name = "recall"

    def __init__(
        self,
        store: Store,
        directory: str | Path,
        *,
        mode: str = "off",
        embedder: OpenAIEmbeddings | None = None,
        model: str = DEFAULT_MODEL,
        top: int = TOP,
        threshold: float = MIN_SCORE,
        gate: bool = False,
        status: object | None = None,
    ) -> None:
        self._store = store
        self._root = Path(directory)
        self._mode = mode if mode in ("off", "whole", "vector") else "off"
        self._embedder = embedder
        self._model = model
        self._top = max(1, top)
        self._threshold = threshold
        self._gate = bool(gate) and self._mode == "vector"
        self._status = status
        self._lock = threading.Lock()
        self._indexes: dict[str, tuple[float, list[Passage], list[list[float]]]] = {}

    # ---- what the page shows, and what the kernel asks

    def capabilities(self) -> dict[str, object]:
        return {
            "mode": self._mode,
            "model": self._model if self._mode == "vector" else "",
            "top": self._top,
            "gate": self._gate,
        }

    def migrations(self) -> Sequence[str]:
        return _SCHEMA

    def persona(self, character_id: str) -> str:
        """The facts file in full, for `whole`; empty in the other two modes. It goes in
        the persona slot beside the sheet, so it is cached with it and costs nothing per
        turn — which is the whole trade of this mode: prompt size once, no service."""
        if self._mode != "whole":
            return ""
        return read_facts(self._root, character_id)

    # ---- the vector version

    def judge_questions(self) -> Sequence[Question]:
        """One question, on the request the judge already makes: does this turn need her
        facts at all? Upstream asks JEV; any judge model answers it here."""
        if not self._gate:
            return ()
        return (
            Question(
                "recall.needed",
                "这一句是不是在问她过去经历过的事、她的喜好和经历，或者要她的设定才答得上？"
                "寒暄、当下的心情、和她说她自己的日常都不算",
                kind="bool",
            ),
        )

    async def prompt_sections(self, turn: TurnContext) -> Sequence[Section]:
        if self._mode != "vector" or self._embedder is None:
            return ()
        # A missing answer is a failed judge, and a failed judge means look anyway.
        if turn.facts.get("recall.needed") is False:
            return ()
        found = await self._find(turn.conversation.character_id, turn.text)
        if not found:
            return ()
        lines = [
            _HEADING,
            "下面是和你有关的几段资料。和这句话有关的部分，就当作你本来就知道的事自然地用上；"
            "不要提「资料」「设定」这些词，也不要整段复述。",
        ]
        for passage in found:
            head = f"【{passage.heading}】" if passage.heading else ""
            lines.append(f"- {head}{passage.text}")
        return (Section(Slot.TURN, "\n".join(lines), order=15, title="recall"),)

    async def _find(self, character_id: str, query: str) -> list[Passage]:
        try:
            passages, vectors = await self._index(character_id)
        except Exception:  # an index that cannot be built costs the turn its passages
            return []
        if not passages:
            return []
        try:
            [vector] = await asyncio.to_thread(self._embed_now, [query])
        except Exception:
            return []
        scored = sorted(
            ((_cosine(vector, stored), passage) for passage, stored in zip(passages, vectors)),
            key=lambda pair: pair[0],
            reverse=True,
        )
        return [passage for score, passage in scored[: self._top] if score >= self._threshold]

    async def _index(self, character_id: str) -> tuple[list[Passage], list[list[float]]]:
        text = read_facts(self._root, character_id)
        if not text.strip():
            return [], []
        stamp = _stamp(self._root, character_id)
        with self._lock:
            cached = self._indexes.get(character_id)
        if cached is not None and cached[0] == stamp:
            return cached[1], cached[2]
        passages, vectors = await asyncio.to_thread(self._build, character_id, text)
        with self._lock:
            self._indexes[character_id] = (stamp, passages, vectors)
        return passages, vectors

    def _build(self, character_id: str, text: str) -> tuple[list[Passage], list[list[float]]]:
        """Read the passages, keep the vectors already stored for this model, embed what is
        new — an edited facts file costs only the passages that changed, as upstream's
        `embedded_texts` does. Runs on a thread: SQLite and one HTTP call are both blocking.
        """
        passages = read_passages(text)
        stored = {
            str(row["text"]): _from_blob(row["vec"])
            for row in self._store.query(
                "SELECT text, vec FROM recall_vectors WHERE character_id = ? AND model = ?",
                (character_id, self._model),
            )
        }
        missing = [passage.text for passage in passages if passage.text not in stored]
        for start in range(0, len(missing), BATCH):
            batch = missing[start : start + BATCH]
            for piece, vector in zip(batch, self._embed_now(batch)):
                stored[piece] = vector
                self._store.execute(
                    "INSERT OR REPLACE INTO recall_vectors (character_id, model, text, vec)"
                    " VALUES (?, ?, ?, ?)",
                    (character_id, self._model, piece, _to_blob(vector)),
                )
        return passages, [stored[passage.text] for passage in passages]

    def _embed_now(self, texts: Sequence[str]) -> list[list[float]]:
        """The blocking call, with its outcome left in the service status. A failure is
        raised: the caller decides to go on without, never to fail the turn."""
        if self._embedder is None:
            raise EmbeddingError("no embedding service is configured")
        try:
            vectors = self._embedder.embed(texts)
        except Exception as exc:
            self._record(False)
            log.warning("embedding failed: %s", exc)
            raise
        self._record(True)
        return vectors

    def _record(self, ok: bool) -> None:
        record = getattr(self._status, "record", None)
        if record is None:
            return
        try:
            record("embedding", ok=ok)
        except Exception:  # the status page is never worth a failed turn
            pass

    # ---- reading the index without building it

    def routes(self) -> dict[tuple[str, str], Route]:
        async def state(query, body):  # noqa: ANN001 - the shape is in plugin.Route
            character_id = str(query.get("c") or "")
            if not character_id:
                return {"error": "需要 c（角色）"}
            text = read_facts(self._root, character_id)
            passages = read_passages(text)
            rows = self._store.query(
                "SELECT COUNT(*) AS n FROM recall_vectors WHERE character_id = ? AND model = ?",
                (character_id, self._model),
            )
            return {
                "mode": self._mode,
                "model": self._model if self._mode == "vector" else "",
                "passages": len(passages),
                "embedded": int(rows[0]["n"]) if rows else 0,
                "chars": len(text),
            }

        return {("GET", "/api/recall"): state}


def read_facts(root: Path, character_id: str) -> str:
    """`facts/<id>.md` beside the roster; empty when the deployment has none — which is
    what `off` costs, and why it is the default."""
    if not character_id:
        return ""
    for path in (root / "facts" / f"{character_id}.md", root / f"{character_id}.facts.md"):
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return ""


def read_passages(text: str, *, limit: int = CHUNK) -> list[Passage]:
    """The file as passages: split at headings, and again at paragraph breaks when a
    section is longer than `limit`. The heading travels with each piece, so a passage
    carries enough context to mean something in a prompt on its own."""
    out: list[Passage] = []
    heading = ""
    block: list[str] = []

    def flush() -> None:
        body = "\n".join(block).strip()
        block.clear()
        if not body:
            return
        for piece in _pieces(body, limit):
            if piece:
                out.append(Passage(heading, piece))

    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            flush()
            heading = line.strip().lstrip("#").strip()
        else:
            block.append(line)
    flush()
    return out


def _pieces(body: str, limit: int) -> list[str]:
    """A section cut at blank lines into pieces of at most `limit` characters; a single
    paragraph longer than that is cut where it stands."""
    pieces: list[str] = []
    current = ""
    for paragraph in (part.strip() for part in body.split("\n\n")):
        if not paragraph:
            continue
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            pieces.append(current)
        while len(paragraph) > limit:
            pieces.append(paragraph[:limit].strip())
            paragraph = paragraph[limit:].strip()
        current = paragraph
    if current:
        pieces.append(current)
    return pieces


def _stamp(root: Path, character_id: str) -> float:
    """When the facts file last changed, so an edit is picked up on the next turn without
    a restart and without re-embedding the passages that did not change."""
    for path in (root / "facts" / f"{character_id}.md", root / f"{character_id}.facts.md"):
        try:
            return path.stat().st_mtime
        except OSError:
            continue
    return 0.0


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


def _to_blob(vector: Sequence[float]) -> bytes:
    return array("f", vector).tobytes()


def _from_blob(blob: bytes) -> list[float]:
    values = array("f")
    values.frombytes(blob)
    return list(values)
