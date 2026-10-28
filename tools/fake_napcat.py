"""A stand-in for NapCat, for developing the channel without a QQ account.

It is a real WebSocket server (handshake, frames, masking) that speaks just enough OneBot to
be useful:

    python tools/fake_napcat.py --port 3001 --group 100 --user 42 --self 999

On every connection it waits a second, then sends one group message that @s the bot. Anything
the bot sends back is printed with the segments it used. That is the whole loop — trigger,
merge, turn, segments, outbox — exercised over a socket, with no QQ and no model key
(`serve.py --fake` gives it a scripted reply).

Not a test double in the strict sense: tests use a fake client object, because they should not
need a port. This is for looking at the thing.
"""

import argparse
import asyncio
import base64
import hashlib
import json

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


async def handshake(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> bool:
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except Exception:
        return False
    key = ""
    for line in head.decode("latin-1").splitlines():
        if line.lower().startswith("sec-websocket-key:"):
            key = line.split(":", 1)[1].strip()
    if not key:
        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        return False
    accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
    writer.write(
        b"HTTP/1.1 101 Switching Protocols\r\n"
        b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
        b"Sec-WebSocket-Accept: " + accept.encode() + b"\r\n\r\n"
    )
    await writer.drain()
    return True


async def read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    head = await reader.readexactly(2)
    opcode = head[0] & 0x0F
    size = head[1] & 0x7F
    if size == 126:
        size = int.from_bytes(await reader.readexactly(2), "big")
    elif size == 127:
        size = int.from_bytes(await reader.readexactly(8), "big")
    mask = await reader.readexactly(4) if head[1] & 0x80 else b""
    payload = await reader.readexactly(size) if size else b""
    if mask:
        payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    return opcode, payload


def write_frame(opcode: int, payload: bytes) -> bytes:
    header = bytearray([0x80 | opcode])
    size = len(payload)
    if size < 126:
        header.append(size)
    elif size < 65536:
        header.append(126)
        header += size.to_bytes(2, "big")
    else:
        header.append(127)
        header += size.to_bytes(8, "big")
    return bytes(header) + payload


async def serve(options: argparse.Namespace) -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if not await handshake(reader, writer):
            writer.close()
            return
        print(f"fake-napcat: bot connected from {writer.get_extra_info('peername')}", flush=True)

        async def push() -> None:
            await asyncio.sleep(1.0)
            if options.private:
                event = {
                    "post_type": "message",
                    "message_type": "private",
                    "sub_type": "friend",
                    "user_id": int(options.user),
                    "message_id": "fake-1",
                    "sender": {"nickname": options.card},
                    "message": [{"type": "text", "data": {"text": options.say}}],
                }
                words = f'fake-napcat: {options.user} says "{options.say}"'
            else:
                event = {
                    "post_type": "message",
                    "message_type": "group",
                    "group_id": int(options.group),
                    "user_id": int(options.user),
                    "message_id": "fake-1",
                    "sender": {"card": options.card, "nickname": options.card},
                    "message": [
                        {"type": "at", "data": {"qq": str(options.self)}},
                        {"type": "text", "data": {"text": options.say}},
                    ],
                }
                words = f'fake-napcat: @bot "{options.say}"'
            writer.write(write_frame(0x1, json.dumps(event, ensure_ascii=False).encode()))
            await writer.drain()
            print(words, flush=True)

        pusher = asyncio.create_task(push())
        try:
            while True:
                opcode, payload = await read_frame(reader)
                if opcode == 0x8:
                    break
                if opcode != 0x1:
                    continue
                action = json.loads(payload)
                name = action.get("action")
                params = action.get("params") or {}
                if name in ("send_group_msg", "send_private_msg"):
                    target = params.get("group_id") or params.get("user_id")
                    said = "".join(
                        str(part.get("data", {}).get("text", ""))
                        for part in params.get("message", [])
                        if part.get("type") == "text"
                    ).strip()
                    kinds = [part.get("type") for part in params.get("message", [])]
                    print(f"fake-napcat: bot said to {target} {kinds} {said!r}", flush=True)
                writer.write(
                    write_frame(
                        0x1,
                        json.dumps(
                            {"status": "ok", "retcode": 0, "echo": action.get("echo"), "data": {}},
                            ensure_ascii=False,
                        ).encode(),
                    )
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            pusher.cancel()
            writer.close()
            print("fake-napcat: bot disconnected", flush=True)

    server = await asyncio.start_server(handle, options.host, options.port)
    print(
        f"fake-napcat: ws://{options.host}:{options.port}"
        f"  {'dm ' + options.user if options.private else 'group ' + options.group} self={options.self}",
        flush=True,
    )
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="A NapCat stand-in for local development.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3001)
    parser.add_argument("--group", default="100")
    parser.add_argument("--user", default="42")
    parser.add_argument("--self", default="999", dest="self")
    parser.add_argument("--card", default="制作人")
    parser.add_argument("--say", default="こんばんは、ふゆ")
    parser.add_argument("--private", action="store_true", help="send a private message instead of a group @")
    options = parser.parse_args()
    try:
        asyncio.run(serve(options))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
