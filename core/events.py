"""What a turn hands out, in order, as it happens.

The kernel's own events are few and fixed; that is the contract a channel or a browser
renders. A plugin adds events of its own through `PluginEvent`, which carries a
namespace — a renderer must ignore a namespace it does not know, so that adding a plugin
never breaks an old client.

`SegmentDelta`, `SpeechDelta`, `SegmentComplete` and `PhotoTagged` come from `protocol`:
they are the reply parser's own, and inventing kernel copies of them would mean two
vocabularies for one reply.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from .protocol import PhotoTagged, SegmentComplete, SegmentDelta, SpeechDelta


@dataclass(frozen=True)
class UserSaid:
    """The user's words, as the turn took them in, before any aside or narration."""

    text: str
    type: Literal["user-said"] = field(default="user-said", init=False)


@dataclass(frozen=True)
class TurnDone:
    """The reply is stored. `message_id` is how a channel fetches it, e.g. for a photo."""

    message_id: str
    type: Literal["done"] = field(default="done", init=False)


@dataclass(frozen=True)
class TurnRejected:
    """The turn never started: nothing was sent to the model, nothing was stored.
    `reason` is `empty`, `too_long`, or whatever a gate plugin vetoed with; `retry_after`
    is seconds, when the answer is "not yet" rather than "never"."""

    reason: str
    retry_after: int | None = None
    type: Literal["rejected"] = field(default="rejected", init=False)


@dataclass(frozen=True)
class TurnFailed:
    """The turn started and did not finish: the model refused, or said nothing usable.
    Nothing is stored, so the user's words can be sent again."""

    reason: str
    type: Literal["failed"] = field(default="failed", init=False)


@dataclass(frozen=True)
class PluginEvent:
    """Something a plugin wants a client to know. `namespace` is the plugin's name; a
    client that does not know it must ignore the event rather than fail."""

    namespace: str
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    type: Literal["plugin"] = field(default="plugin", init=False)


KernelEvent = (
    UserSaid
    | SegmentDelta
    | SpeechDelta
    | SegmentComplete
    | PhotoTagged
    | TurnDone
    | TurnRejected
    | TurnFailed
    | PluginEvent
)
