"""The kernel, served: characters, a conversation, and the page that draws them.

No accounts, no sessions, no admin console — those are plugins, and this runs without
them. What is here is what a conversation needs: a roster, a turn that streams as it is
written, and the history it came out of.

The HTTP layer knows three things the kernel does not: how to answer a browser, where the
page's files are, and that plugins may bring endpoints of their own (`host.routes()`). A
plugin adds a feature without this file learning what the feature means.

    python serve.py                     # reads .env, then the environment
    python serve.py --port 8100 --fake   # no key: fixed lines, nothing spent
"""

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app import App
from core.events import PluginEvent, SegmentDelta, TurnDone, TurnFailed, TurnRejected
from core.llm import ScriptedLLM
from core.settings import Settings, load_env

HERE = Path(__file__).parent
PAGE = HERE / "web" / "index.html"
ASSETS = HERE / "web" / "assets"
MAX_BODY = 12 * 1024 * 1024
"""A picture is base64 in the body; 12MiB is one 6MiB photo with room around it."""

PAGE_ROUTES = ("/", "/index.html", "/conversations", "/settings", "/gifts")
"""The pages the front end draws. The front end does the second level itself: `/settings/<category>`
and `/conversation/<id>` are answered here too, so a refresh lands where the reader was."""

FAKE_REPLY = (
    "<ja>……おかえりなさい。いま、ちょっとだけ考えてたの。</ja><zh>……欢迎回来。刚才，我在想一点点事情。</zh>"
    "<ja>今日は、どんな一日だった？</ja><zh>今天，是怎样的日子？</zh>"
)

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".mp3": "audio/mpeg",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".json": "application/json; charset=utf-8",
}


def wire(event: object) -> dict | None:
    """What a client is told of an event. `None` for what stays on the server: the speech
    deltas and the segment boundaries are for a call, not a page."""
    if isinstance(event, SegmentDelta):
        return {"type": "delta", "segment": event.segment, "lang": event.lang, "text": event.text}
    if isinstance(event, TurnDone):
        return {"type": "done", "id": event.message_id}
    if isinstance(event, TurnRejected):
        return {"type": "rejected", "reason": event.reason, "retry_after": event.retry_after}
    if isinstance(event, TurnFailed):
        return {"type": "failed", "reason": event.reason}
    if isinstance(event, PluginEvent):
        return {
            "type": "plugin",
            "namespace": event.namespace,
            "name": event.name,
            "payload": event.payload,
        }
    return None


def make_handler(app: App, page: bytes) -> type[BaseHTTPRequestHandler]:
    """`page` is the shell the browser gets. Its asset links carry a stamp of the
    assets' own mtimes, so a browser that remembers yesterday's script is told to
    fetch the one that is there now."""
    routes = app.host.routes()
    media_root = Path(app.settings.media).resolve()
    characters = app.characters

    def _stamp() -> str:
        """A number that changes whenever any asset changes: the newest mtime under
        `web/assets`, in seconds."""
        newest = 0.0
        for path in ASSETS.rglob("*"):
            if path.is_file():
                newest = max(newest, path.stat().st_mtime)
        return str(int(newest))

    def _page_html() -> bytes:
        """The shell, with the asset links stamped so a browser never runs a stale script."""
        try:
            raw = page.decode("utf-8")
        except UnicodeDecodeError:
            return page
        stamp = _stamp()
        raw = re.sub(r'(/(?:assets|media)/[^"\')\s]+)', lambda m: m.group(1) + "?v=" + stamp, raw)
        return raw.encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        server_version = "scbot-new"

        def log_message(self, fmt: str, *args: object) -> None:  # quiet: one line per turn
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

        # ---- GET

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            query = _flat(parsed.query)
            if path in PAGE_ROUTES or path.startswith("/conversation/") or path.startswith("/settings/"):
                self._send(200, _page_html(), "text/html; charset=utf-8")
                return
            if path.startswith("/assets/"):
                self._asset(path[len("/assets/") :].split("?")[0])
                return
            if path.startswith("/media/avatar/"):
                self._avatar(path[len("/media/avatar/") :].split("?")[0], scsp="set=scsp" in (parsed.query or ""))
                return
            if path.startswith("/media/"):
                self._photo(path[len("/media/") :])
                return
            if path == "/api/state":
                try:
                    self._json(app.state(query.get("c") or None, query.get("conv") or None))
                except KeyError:
                    self._json({"error": "没有这个角色"}, status=404)
                return
            if path == "/api/history":
                try:
                    self._json(
                        {"messages": app.messages(query.get("c") or None, query.get("conv") or None)}
                    )
                except KeyError:
                    self._json({"error": "没有这个角色"}, status=404)
                return
            if path == "/api/conversations":
                try:
                    self._json({"conversations": app.conversations(query.get("c") or "")})
                except KeyError:
                    self._json({"error": "没有这个角色"}, status=404)
                return
            if path == "/api/roster":
                self._json({"roster": app.roster()})
                return
            if path == "/api/export":
                self._download("scbot-data.json", app.export())
                return
            if path == "/api/stats":
                self._json(
                    app.stats(
                        query.get("range") or "all",
                        self.headers.get("X-Timezone"),
                    )
                )
                return
            handler = routes.get(("GET", path))
            if handler is not None:
                self._plugin(handler, query, {})
                return
            self._json({"error": "没有这个地址"}, status=404)

        # ---- POST

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            query = _flat(parsed.query)
            body = self._body()
            if path == "/api/turns":
                self._stream(query.get("c") or None, body, query.get("conv") or None)
                return
            if path == "/api/conversations":
                try:
                    conversation = app.new_conversation(query.get("c") or "")
                except KeyError:
                    self._json({"error": "没有这个角色"}, status=404)
                    return
                self._json({"conversation_id": conversation.id})
                return
            if path == "/api/conversations/delete":
                gone = app.delete_conversation(str(body.get("id") or ""))
                self._json({"deleted": gone})
                return
            if path == "/api/import":
                try:
                    self._json(app.import_package(body))
                except ValueError as exc:
                    self._json({"error": str(exc)}, status=400)
                return
            handler = routes.get(("POST", path))
            if handler is not None:
                self._plugin(handler, query, body)
                return
            self._json({"error": "没有这个地址"}, status=404)

        # ---- the turn

        def _stream(self, character_id: str | None, body: dict, conversation_id: str | None = None) -> None:
            text = str(body.get("text") or "")
            photos = body.get("photos") or []
            photos = [str(item) for item in photos][:4] if isinstance(photos, list) else []
            own_keys = body.get("own_keys") if isinstance(body.get("own_keys"), dict) else None
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            async def run() -> None:
                async for event in app.turn(character_id, text, photos, conversation_id, own_keys):
                    payload = wire(event)
                    if payload is None:
                        continue
                    # 服务状态：模型这一轮成了没有（失败的那次内核不会走到 observe，在这里记）。
                    kind = payload.get("type")
                    if kind in ("done", "failed"):
                        app.status.record("chat_llm", ok=kind == "done")
                    self._event(payload)

            try:
                asyncio.run(run())
            except KeyError:
                app.status.record("chat_llm", ok=False)
                self._event({"type": "failed", "reason": "no_character"})
            except Exception as exc:  # the turn loop catches its own; this is the last net
                app.status.record("chat_llm", ok=False)
                self._event({"type": "failed", "reason": f"{type(exc).__name__}: {exc}"})

        def _plugin(self, handler, query, body) -> None:  # noqa: ANN001 - plugin.Route
            try:
                answer = asyncio.run(handler(query, body))
            except Exception as exc:
                self._json({"error": f"{type(exc).__name__}: {exc}"}, status=500)
                return
            self._json(answer if answer is not None else {})

        # ---- files

        def _avatar(self, character_id: str, scsp: bool = False) -> None:
            path = characters.avatar(character_id, scsp=scsp)
            if path is None:
                self._json({"error": "没有头像"}, status=404)
                return
            self._file(path, cache="public, max-age=86400")

        def _photo(self, name: str) -> None:
            path = app.photos.path(name)
            if path is None:
                self._json({"error": "没有这张图"}, status=404)
                return
            if media_root not in path.resolve().parents:
                self._json({"error": "没有这张图"}, status=404)
                return
            self._file(path, cache="private, max-age=600")

        def _asset(self, name: str) -> None:
            """The page's own files: the compiled stylesheet, the fonts, the scripts. The
            name is taken as a path under `web/assets`, and nothing outside it is served."""
            target = (ASSETS / name).resolve()
            if ASSETS.resolve() not in target.parents:
                self._json({"error": "没有这个文件"}, status=404)
                return
            if not target.is_file():
                self._json({"error": "没有这个文件"}, status=404)
                return
            kind = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
            # Stylesheets and scripts change while we iterate on the look; the browser
            # must not keep serving yesterday's build out of its cache.
            cache = "no-store" if target.suffix.lower() in (".js", ".css", ".html") else "public, max-age=86400"
            self._file(target, cache=cache, kind=kind)

        def _file(self, path: Path, *, cache: str, kind: str | None = None) -> None:
            try:
                data = path.read_bytes()
            except OSError:
                self._json({"error": "读不到文件"}, status=404)
                return
            if kind is None:
                kind = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(data)

        # ---- plumbing

        def _event(self, payload: dict) -> None:
            line = "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
            self.wfile.write(line.encode("utf-8"))
            self.wfile.flush()

        def _body(self) -> dict:
            size = int(self.headers.get("Content-Length") or 0)
            if not size or size > MAX_BODY:
                return {}
            try:
                parsed = json.loads(self.rfile.read(size).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {}
            return parsed if isinstance(parsed, dict) else {}

        def _json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _download(self, name: str, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.end_headers()
            self.wfile.write(body)

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _flat(query: str) -> dict[str, str]:
    """The first value of each parameter: a browser sends one, and a plugin that needs
    lists can parse its own body."""
    return {name: values[0] for name, values in parse_qs(query).items() if values}


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve scbot-new.")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--env", default=str(HERE / ".env"))
    parser.add_argument("--fake", action="store_true", help="用假模型跑：不需要 key，只说一段固定台词")
    options = parser.parse_args()

    load_env(options.env)
    fake = options.fake or os.environ.get("SCCORE_FAKE") == "1"
    settings = Settings.from_env(require_credentials=not fake)
    if options.port is not None or options.host is not None:
        settings = replace(
            settings,
            port=options.port or settings.port,
            host=options.host or settings.host,
        )
    if not PAGE.exists():
        raise RuntimeError(f"缺少页面：{PAGE}")

    app = App(settings, ScriptedLLM(FAKE_REPLY) if fake else None)
    server = ThreadingHTTPServer((settings.host, settings.port), make_handler(app, PAGE.read_bytes()))
    server.daemon_threads = True
    print(
        f"scbot-new  ←  http://{settings.host}:{settings.port}  "
        f"（{len(app.characters.roster)} 位角色 / {', '.join(app.host.names)} / {settings.model}）"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
        app.close()


if __name__ == "__main__":
    main()
