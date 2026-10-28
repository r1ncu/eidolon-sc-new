"""The two things a conversation is made of, as data. No SQL, no HTTP.

Kept apart from `store` so that anything that reads a conversation — the prompt builder,
a channel, a test — depends on the shape of a message and not on where it is kept.
"""

from dataclasses import dataclass, field
from typing import Literal

from .protocol import Segment

Role = Literal["user", "assistant"]


@dataclass(frozen=True)
class Conversation:
    id: str
    user_id: str
    character_id: str
    created_at: str
    title: str | None = None


@dataclass(frozen=True)
class Message:
    id: str
    conversation_id: str
    role: Role
    text: str = ""
    segments: tuple[Segment, ...] = ()
    photo_tag: str | None = None
    photo_failed: bool = False
    created_at: str = field(default="")

    @property
    def ja(self) -> str:
        return "".join(s.ja for s in self.segments)

    @property
    def zh(self) -> str:
        return "".join(s.zh for s in self.segments)
