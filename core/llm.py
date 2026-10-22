"""What the kernel says to a model, and what it gets back, one chunk at a time.

Two clients, one protocol. `OpenAICompat` talks to a real service over chat completions;
`ScriptedLLM` says what it was told to, which is how every test runs with no network and
no key, and how the kernel can be demonstrated without spending anything.

The kernel speaks only this protocol. The other two upstream supports (Responses,
Anthropic Messages) are adapters that belong behind the same `LLM` interface, not in the
turn loop — and `ChatMessage.content` stays a `str` until a plugin deliberately widens it.
"""

import asyncio
import json
import threading
import urllib.request
from collections.abc import AsyncIterator, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str | list[dict[str, Any]]
    """Text, or the OpenAI multimodal part list (`[{"type": "text"}, {"type":
    "image_url"}]`) when a plugin has widened it deliberately — the kernel writes `str`
    everywhere, so a picture only reaches the model through the plugin that added it."""
    cache_breakpoint: bool = field(default=False)
    """Ask the service to cache the prompt up to and including this message. Kept on the
    message rather than in the payload because what a service does with it differs: the
    OpenAI-compatible ones cache prefixes on their own, and an adapter that can say so
    (Anthropic's `cache_control`) reads this field."""


@runtime_checkable
class LLM(Protocol):
    """Streams a reply as text chunks, in whatever pieces the service sends.

    A client raises if the service refuses; the turn loop turns that into a `TurnFailed`
    rather than a crash, because a character that says nothing for a minute is worse than
    one that says "the line dropped".
    """

    def stream(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]: ...


class LLMError(RuntimeError):
    """The service refused, or the stream broke part-way. Carries no key: only what the
    service said about the request."""


class OpenAICompat:
    """A client for anything that speaks `/chat/completions` with `stream: true`.

    Reads the response on a thread and hands chunks to the event loop, because `urllib`
    blocks and the turn loop must not: a stalled model must stall one turn, not the
    server. No dependency beyond the standard library, so deploying the kernel is copying
    a directory and setting three environment variables.

    `open_stream` is the seam the tests use: given the payload, return the lines of the
    response. Everything above it stays the same whether the bytes come from a socket or
    from a string.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 120.0,
        temperature: float | None = None,
        open_stream: Any | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._temperature = temperature
        self._open = open_stream or self._post

    def payload(self, messages: Sequence[ChatMessage]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
        }
        if self._temperature is not None:
            body["temperature"] = self._temperature
        return body

    async def stream(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        def hand(kind: str, value: Any) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, (kind, value))

        def read() -> None:
            try:
                for piece in _read_stream(self._open(self.payload(messages))):
                    hand("chunk", piece)
                hand("end", None)
            except Exception as exc:  # told to the loop, raised where the turn can see it
                hand("error", exc)

        threading.Thread(target=read, daemon=True).start()
        while True:
            kind, value = await queue.get()
            if kind == "end":
                return
            if kind == "error":
                raise LLMError(f"{type(value).__name__}: {value}") from value
            yield value

    def _post(self, payload: dict[str, Any]) -> Iterable[bytes]:
        request = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "text/event-stream",
            },
            method="POST",
        )
        return urllib.request.urlopen(request, timeout=self._timeout)


def _read_stream(lines: Iterable[bytes | str]) -> Iterator[str]:
    """The text of a streamed chat completion, chunk by chunk.

    Skips what is not an event, stops at `[DONE]`, and ignores a `choices` entry with no
    content — the first event of a stream carries only the role, and a service that
    changes its mind mid-stream sends a finish reason with nothing in it.
    """
    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            return
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        for choice in event.get("choices") or ():
            delta = choice.get("delta") or {}
            text = delta.get("content")
            if text:
                yield text


class ScriptedLLM:
    """A model that says what it was told to, in chunks of `size` characters.

    Two uses: the tests, and running the kernel with no key at all. It streams in chunks
    rather than in one piece on purpose, so every test exercises the same path a real
    service does.
    """

    def __init__(self, reply: str, size: int = 7, fail_after: int | None = None) -> None:
        self.reply = reply
        self.size = size
        self.fail_after = fail_after
        self.seen: list[Sequence[ChatMessage]] = []

    async def stream(self, messages: Sequence[ChatMessage]) -> AsyncIterator[str]:
        self.seen.append(list(messages))
        for sent, start in enumerate(range(0, len(self.reply), self.size)):
            if self.fail_after is not None and sent >= self.fail_after:
                raise RuntimeError("the line dropped")
            yield self.reply[start : start + self.size]
