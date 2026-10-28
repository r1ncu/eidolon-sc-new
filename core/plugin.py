"""What a plugin is, and the three ways it may take part in a turn.

There is no `kind` field to pick. A plugin is what its hooks do:

    gate     `before_turn` returns a `Veto`   — the turn cannot start
    enrich   `prompt_sections`, `judge_questions` — adds context, never blocks
    observe  `after_turn`                     — runs after the reply is stored

A plugin may be more than one at once (affinity reads a judgement, writes a line of the
prompt, and records what changed), which is why the three are methods and not classes:
the constraint that matters is per-hook, not per-plugin — `enrich` must be pure, `observe`
must be asynchronous, `gate` must not touch the prompt at all.

Everything is `async`, including `prompt_sections`, so that a plugin needing to look
something up does not have to pretend it is pure.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from .events import KernelEvent
from .messages import Conversation, Message
from .prompt import Section

Route = Callable[[Mapping[str, str], Mapping[str, Any]], Awaitable[Any]]
"""A plugin's own HTTP surface: query as a flat dict, body as JSON, answer as JSON.

The kernel never sees these; `serve.py` collects them and answers. That is the point of a
surface being a plugin — a feature that needs an endpoint of its own adds one without the
turn loop learning about it.
"""


@dataclass(frozen=True)
class Veto:
    """A gate said no. `reason` is a stable key a client can translate; `retry_after` is
    seconds, when the answer is "not yet" rather than "never"."""

    reason: str
    retry_after: int | None = None


@dataclass(frozen=True)
class Question:
    """Something a plugin wants a judge model to decide, once per turn.

    Plugins declare questions; they never call a model themselves. Three plugins asking
    three questions is one request, not three — which is the whole reason this type
    exists instead of plugins holding an LLM.
    """

    key: str
    ask: str
    kind: Literal["bool", "int", "text"] = "bool"
    least: int = 0
    most: int = 0
    """For `int` only: the answers are clamped into `[least, most]`."""


@dataclass
class TurnContext:
    """Everything a plugin may look at during one turn, and the only way plugins talk to
    each other: `facts` is keyed by whoever writes it (`affinity.delta`, `photos.wants`).
    Two plugins that both write the same key are a bug, not a merge."""

    conversation: Conversation
    text: str
    now: datetime
    surface: str = "page"
    """Where this turn happens: `page`, `group`, `dm`. Not a rendering detail — a plugin
    that keeps a number per relationship needs it, because what fifty people do to her mood
    and what he says to her alone are two different relationships. Defaults to the page."""
    facts: dict[str, Any] = field(default_factory=dict)
    reply: Message | None = None
    """Set once the reply is stored; `None` in the hooks before that."""
    events: list[KernelEvent] = field(default_factory=list)
    """Plugin events collected during `after_turn`; the kernel hands them out."""


class Plugin:
    """Subclass this, set `name`, override what you need. Every default does nothing."""

    name: str = ""
    requires: tuple[str, ...] = ()
    """Plugins that must be enabled for this one to be: it reads their `facts`, their
    tables, or both."""

    def capabilities(self) -> dict[str, Any]:
        """What a client should show: `{"panel": "affinity"}` and the like. Merged into
        `GET /api/capabilities` under `name`, and the only thing the frontend keys off."""
        return {}

    def migrations(self) -> Sequence[str]:
        """`CREATE TABLE IF NOT EXISTS …` for this plugin's own tables. Run at startup,
        in dependency order. Stopping a plugin never drops them."""
        return ()

    def routes(self) -> Mapping[tuple[str, str], Route]:
        """Its own endpoints, keyed `("GET", "/api/whatever")`. Merged into the server's
        table at startup; a path two plugins claim is an error, not a merge."""
        return {}

    def judge_questions(self) -> Sequence[Question]:
        return ()

    async def before_turn(self, turn: TurnContext) -> Veto | None:
        return None

    async def prompt_sections(self, turn: TurnContext) -> Sequence[Section]:
        return ()

    async def after_turn(self, turn: TurnContext) -> None:
        return None

    # ---- the lifecycle, for the few plugins that have one

    def start(self) -> None:
        """Called once, after every plugin is constructed and its tables exist.

        Most plugins never override this: a turn hook needs no beginning. It is here for the
        ones that are not only a turn hook — a socket to open, a thread to run, a timer to
        arm — because "a feature you can drop in" and "a feature that starts itself" are the
        same requirement, and the host is the only place that knows the order.

        Raising is allowed and not fatal: the host writes the plugin off with the reason and
        carries on serving turns without it. A channel that cannot connect must not take the
        page down with it.
        """
        return None

    def stop(self) -> None:
        """Called once on shutdown, in reverse order of `start`.

        Must be quick and must not raise: `serve.py` is already on its way out, and a
        five-second wait is the budget the socket threads are given.
        """
        return None
