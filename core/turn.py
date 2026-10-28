"""One turn: the only place the kernel does anything in order.

Everything a feature could want to change happens at one of four points, and this file is
where they are — which is what makes the plugins in `plugins/` possible without any of
them knowing about each other:

    before   the words are checked, then the gates are asked (a `Veto` stops the turn)
    build    the judgement, then the prompt: persona, profile, history, this turn's parts
    stream   chunks in, parser events out
    after    the turn is stored, then the observers run, then a done event says where

Nothing here imports a channel, a vendor, or a feature. If the gates and observers were
all removed, this file would still make sense — that is the boundary test.
"""

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .events import KernelEvent, TurnDone, TurnFailed, TurnRejected, UserSaid
from .host import Host
from .llm import LLM
from .plugin import TurnContext
from .prompt import PromptBuilder, Section, trim_history
from .protocol import PhotoTagged, ReplyParser
from .store import Store


@dataclass
class Engine:
    store: Store
    llm: LLM
    base: tuple[Section, ...] = ()
    """What every turn with this character opens with: the sheet, the world, who the user
    is. Each section says whether it is `persona` (cached for everyone) or `profile`
    (cached per user); the reply format is added by the builder, not by whoever writes
    the sheet."""
    host: Host | None = None
    """The plugins. `None` is a plain character with no gameplay at all — and the whole
    point of the split: everything in `host` can be switched off."""
    history_turns: int | None = 20
    max_chars: int = 2000
    voiced_by: str | None = None
    """The voice model, when speech is on: it only changes which format instructions the
    prompt carries (`protocol.format_instructions`)."""
    _builder: PromptBuilder = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._builder = PromptBuilder(self.base)

    async def turn(
        self,
        conversation_id: str,
        text: str,
        *,
        aside: str | None = None,
        sections: Sequence[Section] = (),
        parts: Sequence[dict] = (),
        facts: Mapping[str, Any] | None = None,
        surface: str = "page",
    ) -> AsyncIterator[KernelEvent]:
        """One turn, as events. `sections` are the caller's own, for a turn that has
        something to say before the plugins do; `aside` is the narration of an event that
        goes before the words; `parts` go into the user's message ahead of them; `facts`
        are what the caller already knows about this turn (which pictures came with it),
        handed over before the gates so a gate can count them."""
        words = text.strip()
        if not words and not parts:
            yield TurnRejected("empty")
            return
        if len(words) > self.max_chars:
            yield TurnRejected("too_long")
            return
        conversation = self.store.conversation(conversation_id)
        if conversation is None:
            yield TurnRejected("no_conversation")
            return

        yield UserSaid(words)
        turn = TurnContext(
            conversation=conversation,
            text=words,
            now=datetime.now(timezone.utc),
            surface=surface,
            facts=dict(facts or {}),
        )

        if self.host is not None:
            veto = await self.host.gate(turn)
            for event in self.host.drain(turn):
                yield event
            if veto is not None:
                yield TurnRejected(veto.reason, veto.retry_after)
                return
            await self.host.judge(turn)
            sections = [*sections, *await self.host.enrich(turn)]
            for event in self.host.drain(turn):
                yield event

        prompt = self._builder.build(
            text=words,
            history=trim_history(self.store.messages(conversation_id), self.history_turns),
            sections=sections,
            aside=aside,
            voiced_by=self.voiced_by,
            parts=parts,
        )

        parser = ReplyParser()
        photo_tag: str | None = None
        try:
            async for chunk in self.llm.stream(prompt):
                for event in parser.feed(chunk):
                    if isinstance(event, PhotoTagged):
                        photo_tag = event.tag
                    yield event
            for event in parser.finish():
                if isinstance(event, PhotoTagged):
                    photo_tag = event.tag
                yield event
        except Exception as exc:  # a failed turn must not take the server down with it
            yield TurnFailed(str(exc) or type(exc).__name__)
            return

        if not any(segment.ja or segment.zh for segment in parser.segments):
            yield TurnFailed("empty_reply")
            return

        reply_id = self.store.add_turn(conversation_id, words, parser.segments, photo_tag)
        if self.host is not None:
            turn.reply = self.store.message(reply_id)
            await self.host.observe(turn)
            for event in self.host.drain(turn):
                yield event
        yield TurnDone(reply_id)
