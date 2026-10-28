"""A WebSocket client built out of `asyncio` and nothing else.

The kernel is proud of having no dependencies, and a channel that needs `websockets`
installed would end that. The protocol this file implements is the part a client actually
uses: the opening handshake, one text frame in, one text frame out, ping/pong, and a close
that does not hang.

Deliberately missing: extensions (`permessage-deflate`), subprotocol negotiation, and
fragmented *sends*. Nothing on the other side of a OneBot connection asks for them, and a
feature nobody uses is a place for bugs to live. Fragmented *receives* are handled, because
a server is free to split a message and NapCat does so on long events.

    conn = await connect("ws://127.0.0.1:3001", headers={"Authorization": "Bearer x"})
    await conn.send('{"action": "get_login_info"}')
    print(await conn.recv())
    await conn.close()
"""

import asyncio
import base64
import hashlib
import os
import ssl
import struct
from urllib.parse import urlsplit

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
"""The constant every WebSocket handshake hashes the client key with (RFC 6455 §1.3)."""

MAX_PAYLOAD = 8 * 1024 * 1024
"""A frame larger than this is treated as a protocol error rather than a memory problem."""

OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class WebSocketError(RuntimeError):
    """The handshake was refused, or the stream stopped being WebSocket-shaped."""


class WebSocketClosed(WebSocketError):
    """The other side closed the connection. The caller reconnects; this is not a bug."""


class WebSocket:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._write_lock = asyncio.Lock()

    async def send(self, text: str) -> None:
        """One text frame. A lock keeps two tasks' frames from interleaving on the wire."""
        async with self._write_lock:
            await self._frame(OP_TEXT, text.encode("utf-8"))

    async def recv(self) -> str:
        """The next text message, with continuations joined and control frames answered."""
        pieces: list[bytes] = []
        while True:
            final, opcode, payload = await self._read_frame()
            if opcode == OP_CLOSE:
                with_code = payload[:2]
                await self._frame(OP_CLOSE, with_code)
                raise WebSocketClosed("peer closed the connection")
            if opcode == OP_PING:
                await self._frame(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue
            if opcode in (OP_TEXT, OP_BINARY):
                pieces = [payload]
            elif opcode == OP_CONT:
                if not pieces:
                    raise WebSocketError("continuation without a message")
                pieces.append(payload)
            else:
                raise WebSocketError(f"unknown opcode {opcode}")
            if final:
                return b"".join(pieces).decode("utf-8", "replace")

    async def close(self) -> None:
        try:
            await self._frame(OP_CLOSE, b"\x03\xe8")  # 1000: normal closure
        except Exception:
            pass
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except Exception:
            pass

    async def _frame(self, opcode: int, payload: bytes) -> None:
        size = len(payload)
        header = bytearray([0x80 | opcode])  # FIN, no RSV bits: no extensions
        if size < 126:
            header.append(0x80 | size)
        elif size < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", size)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", size)
        mask = os.urandom(4)  # a client must mask; a server may not
        header += mask
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self._writer.write(bytes(header) + masked)
        await self._writer.drain()

    async def _read_frame(self) -> tuple[bool, int, bytes]:
        head = await self._reader.readexactly(2)
        final = bool(head[0] & 0x80)
        opcode = head[0] & 0x0F
        masked = bool(head[1] & 0x80)
        size = head[1] & 0x7F
        if size == 126:
            size = struct.unpack("!H", await self._reader.readexactly(2))[0]
        elif size == 127:
            size = struct.unpack("!Q", await self._reader.readexactly(8))[0]
        if size > MAX_PAYLOAD:
            raise WebSocketError(f"frame too large: {size}")
        mask = await self._reader.readexactly(4) if masked else b""
        payload = await self._reader.readexactly(size) if size else b""
        if masked:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return final, opcode, payload


async def connect(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> WebSocket:
    """Open one connection. Raises `WebSocketError` when the handshake is refused.

    The headers are for the things a OneBot server wants before it trusts you — in practice
    `Authorization`, which is why the caller passes a token rather than this file reading
    an environment variable.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("ws", "wss"):
        raise WebSocketError(f"not a websocket url: {url}")
    secure = parts.scheme == "wss"
    host = parts.hostname or "127.0.0.1"
    port = parts.port or (443 if secure else 80)
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")

    context = ssl.create_default_context() if secure else None
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=context), timeout
    )

    key = base64.b64encode(os.urandom(16)).decode("ascii")
    lines = [
        f"GET {target} HTTP/1.1",
        f"Host: {host}:{port}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    for name, value in (headers or {}).items():
        lines.append(f"{name}: {value}")
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8"))
    await writer.drain()

    try:
        status = await asyncio.wait_for(reader.readline(), timeout)
        parts_status = status.split(b" ", 2)
        if len(parts_status) < 2 or parts_status[1] != b"101":
            tail = await asyncio.wait_for(reader.read(256), 1.0)
            raise WebSocketError(f"handshake refused: {status.strip()!r} {tail[:120]!r}")
        answer: dict[str, str] = {}
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout)
            if line in (b"\r\n", b"\n", b""):
                break
            name, _, value = line.decode("latin-1").partition(":")
            answer[name.strip().lower()] = value.strip()
        expected = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if answer.get("sec-websocket-accept") != expected:
            raise WebSocketError("handshake answered with the wrong accept key")
    except Exception:
        writer.close()
        raise
    return WebSocket(reader, writer)
