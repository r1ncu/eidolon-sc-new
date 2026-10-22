"""The prompt: four slots, in a fixed order, with the cache boundaries decided by the slot.

Upstream (ADR-0020) assembles it in one function, one f-string per part. Here a part says
which slot it belongs to and the builder puts it there — because that is the only thing a
plugin needs to be able to do to the prompt, and the only thing it must not get wrong.

    persona   system, one message, cached      the character sheet, plus the reply format
    profile   system, one message, cached      who the user is
    history   the stored turns, cached         cut back to `history_turns`, in batches
    turn      system, one message, NOT cached  what changes this turn
    user      the user's words, always last

The slot *is* the cache decision. A plugin cannot ask for a volatile part to be cached,
so it cannot quietly break the prompt prefix every turn and triple the bill. What changes
turn to turn goes in `turn`; what holds for a user goes in `profile`; what holds for
everyone goes in `persona`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from .llm import ChatMessage
from .messages import Message
from .protocol import format_instructions, format_reply


class Slot(str, Enum):
    """A place in the prompt. `str` as well as `Enum`, so a section may say "turn" and be
    understood — and so this runs on Python 3.11, which Debian 12 ships and `StrEnum`
    does not."""

    PERSONA = "persona"
    PROFILE = "profile"
    HISTORY = "history"
    TURN = "turn"


@dataclass(frozen=True)
class Section:
    """One part of the prompt, written by the kernel or by a plugin."""

    slot: Slot
    text: str
    order: int = 0
    """Where in its slot, for slots that hold more than one part; ties keep the order they
    were given in."""
    title: str = ""
    """Only for tracing: a prompt dump can say which plugin wrote which part."""


@dataclass(frozen=True)
class PromptBuilder:
    """The parts that do not change from turn to turn, and the assembly of the rest.

    `base` holds the `persona` and `profile` sections, in any order: each says which slot
    it belongs to, and splitting them here rather than at the call site keeps a caller
    from having to know that the two are stored apart.
    """

    base: tuple[Section, ...] = ()

    def __post_init__(self) -> None:
        for section in self.base:
            if section.slot not in (Slot.PERSONA, Slot.PROFILE):
                raise ValueError(
                    f"the base holds persona or profile only, not {section.slot}: "
                    f"{section.title or section.text[:20]}"
                )

    def build(
        self,
        *,
        text: str,
        history: Sequence[Message] = (),
        sections: Sequence[Section] = (),
        aside: str | None = None,
        voiced_by: str | None = None,
        parts: Sequence[dict] = (),
    ) -> list[ChatMessage]:
        """The prompt for this turn. `sections` are the turn's own, from plugins; `aside`
        is the narration of an event (a picture, a gift) that goes before the words.

        `parts` are opaque pieces the caller wants in the user's message ahead of the text
        — an image, a file, whatever a channel received. The builder does not look inside
        them: putting a part where it belongs is the whole of its job, and a vendor's shape
        in here would be a vendor in the prompt layer.

        A plugin's sections may be `turn` (this turn, never cached) or `profile` (what
        holds for this user, cached with the profile); anything else is an error.
        """
        _only(sections, "sections")
        persona = [*_texts(self.base, Slot.PERSONA), format_instructions(voiced_by)]
        prompt = [ChatMessage("system", "\n\n".join(persona), cache_breakpoint=True)]
        about_producer = [*_texts(self.base, Slot.PROFILE), *_texts(sections, Slot.PROFILE)]
        if any(about_producer):
            prompt.append(ChatMessage("system", "\n\n".join(about_producer)))
        for message in history:
            prompt.append(_as_chat(message))
        if history:  # where the next turn's prompt opens too, for the service to cache
            last = prompt[-1]
            prompt[-1] = ChatMessage(last.role, last.content, cache_breakpoint=True)
        if this_turn := _texts(sections, Slot.TURN):
            prompt.append(ChatMessage("system", "\n\n".join(this_turn)))
        said = f"{aside or ''}{text}"
        prompt.append(
            ChatMessage("user", [*parts, {"type": "text", "text": said}] if parts else said)
        )
        return prompt


def _only(sections: Sequence[Section], where: str) -> None:
    for section in sections:
        if section.slot not in (Slot.TURN, Slot.PROFILE):  # `!=`, not `is`: a plugin may hand a plain string
            raise ValueError(
                f"{where} holds turn or profile only, not {section.slot}: {section.title or section.text[:20]}"
            )


def _texts(sections: Sequence[Section], slot: Slot) -> list[str]:
    """The texts of `slot`, by `order`, ties in the order given."""
    wanted = [(i, s) for i, s in enumerate(sections) if s.slot == slot]
    numbered = sorted(wanted, key=lambda pair: (pair[1].order, pair[0]))
    return [section.text for _, section in numbered]


def _as_chat(message: Message) -> ChatMessage:
    if message.role == "user":
        return ChatMessage("user", message.text)
    return ChatMessage(
        "assistant", format_reply(list(message.segments), message.photo_tag, message.photo_failed)
    )


def latest_turns(messages: Sequence[Message], turns: int | None) -> list[Message]:
    """The turn `turns` back and all after it; all of them when `turns` is None."""
    if turns is None:
        return list(messages)
    seen = 0
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].role == "user":
            seen += 1
            if seen >= turns:
                return list(messages[i:])
    return list(messages)


def trim_history(messages: Sequence[Message], turns: int | None) -> list[Message]:
    """The latest turns the model reads: at least `turns` (all if None), cut back to that
    many each time they reach half as many again, rather than one at a time, so what it
    reads opens the same way from turn to turn in between — and the cached prefix holds.

    Ported from upstream `engine.chat_history`: with the default 20, the history is let go
    to 30 turns and then cut back to 20, which is the "1.5× and trim in a batch" rule.
    """
    total = sum(m.role == "user" for m in messages)
    if turns is None or total <= turns:
        return latest_turns(messages, None)
    step = max(1, turns // 2)
    return latest_turns(messages, total - step * ((total - turns) // step))
