"""原版网页的接口，答在我们的内核上。

原版前端（`eidolon-sc` 的 Next.js 那份）说的是 `/api/account`、`/api/characters`、
`/api/conversations/{id}/turns` 这一套；我们的内核说的是 `/api/state`、`/api/turns`。
两边**不是同一份接口**，所以这里做翻译：一个 HTTP 服务，原版的地址，我们的数据。

为什么不让内核直接改成原版的形状：原版的接口是给一个多用户产品写的（账号、会话、
管理台、免限额密钥），我们这台部署是**一个人的**。翻译层把「一个人的」那部分固定下来，
内核不必为此长出一套账号体系；哪天换回自己的前端，这层整个可以不要。

    python serve.py --compat-port 8101

抓到的契约是**跑出来的**，不是读代码猜的：拿原版前端逐页走一遍，记下它真的请求了
哪些地址、拿到了什么（见 `docs/原版接口-实测.md`）。
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from core.events import SegmentDelta, TurnDone, TurnFailed, TurnRejected
from plugins.packs import REQUIRED

MAX_BODY = 24 * 1024 * 1024
"""原版前端发来的图是 base64 的 data URL，24MiB 是一张 12MiB 的照片加余量。"""

SESSION_COOKIE = "eidolon_session"
"""原版前端在浏览器里找这个名字的 cookie 才知道「登录过」（`lib/backend.ts`）。

它自己不看 cookie 的值，只问后端 `/api/account` 通不通；所以这里给一个固定的占位值，
用户的身份由这台部署的 `user_id` 决定，不由 cookie 决定。
"""

TIERS = ("cold", "distant", "trust", "close", "intimate")

REFUSALS = {
    "empty": "没有收到要说的话。",
    "too_long": "这句话太长了。",
    "no_conversation": "这段对话已经不在了，回列表重开一个吧。",
}
"""内核拒绝一句时给的是**原因码**，不是给人读的句子——我们自己的页面上有一张表把它翻成中文。

原版前端把这句话直接显示在输入框上面，所以这里也得翻，不然用户看到的是「empty」。"""

CONVERSATION = re.compile(r"^/api/conversations/([^/]+)$")
MESSAGES_ = re.compile(r"^/api/conversations/([^/]+)/messages$")
TURNS_ = re.compile(r"^/api/conversations/([^/]+)/turns$")
CHARACTER_IMAGE = re.compile(r"^/api/characters/([^/]+)/(avatar|scsp-avatar|background)$")
CHARACTER_AFFINITY = re.compile(r"^/api/characters/([^/]+)/affinity$")
CHARACTER_GIFTS = re.compile(r"^/api/characters/([^/]+)/gifts$")
MEMORY = re.compile(r"^/api/memories/(\d+)$")
IMAGE = re.compile(r"^/api/images/([^/]+)$")
PHOTO = re.compile(r"^/api/photos/([^/]+)$")
PHOTO_IMAGE = re.compile(r"^/api/photos/([^/]+)/image$")


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _joined(segments: list, index: int) -> str:
    """一段一段拼回整句：`index` 0 是日语，1 是中文。"""
    out = []
    for pair in segments or []:
        if isinstance(pair, (list, tuple)) and len(pair) > index:
            out.append(str(pair[index] or ""))
    return "\n".join(part for part in out if part)


def message_json(message: dict) -> dict:
    """我们的一条消息，原版的一条 `Message`。

    原版把日语原文和它的中文字幕分开存（`ja` / `zh`），还留着成对的 `segments`；
    我们存的就是成对的段，所以 `ja` / `zh` 是拼出来的，`text` 只给用户说的那句。
    """
    segments = message.get("segments") or []
    hers = message.get("role") != "user"
    return {
        "id": message.get("id"),
        "role": "character" if hers else "user",
        "text": None if hers else message.get("text"),
        "ja": _joined(segments, 0) if hers else None,
        "zh": _joined(segments, 1) if hers else None,
        "segments": [{"ja": str(pair[0] or ""), "zh": str(pair[1] or "")} for pair in segments if len(pair) > 1],
        "created_at": message.get("at"),
        "affinity": None,
        "images": [],
    }


def world_character_json(one: dict) -> dict:
    """「世界观」页里的一个人：她的包在哪、二十项文件里有哪些、缺的拆成两半。

    必填的三份（人设/群/私聊）缺了就演不了，所以单独列；其余缺了只是退回中性写法，
    混在一起看等于二十七行红字。
    """
    pack = one["pack"]
    return {
        "id": one["id"],
        "name": one["name"],
        "home": pack["home"],
        "ready": pack["ready"],
        "present": pack["present"],
        "missing": [kind for kind in pack["missing"] if kind in REQUIRED],
        "optional": [kind for kind in pack["missing"] if kind not in REQUIRED],
        "fallback": pack["fallback"],
    }


def character_json(character: Any) -> dict:
    """原版的一个角色。她的包、她读到哪份文件，这里一概不说——那是我们自己页面的东西。

    `stand` / `greeting` / `voxels_url` 这些是原版有的、这台部署没有的：一律 null，
    前端见到 null 就不画那一块（立绘、招呼、体素动画），不会留一个空框。
    """
    return {
        "id": character.id,
        "name": character.name,
        "intro": character.intro,
        "avatar_url": f"/api/characters/{character.id}/avatar" if character.avatar else None,
        "scsp_avatar_url": f"/api/characters/{character.id}/scsp-avatar" if character.scsp_avatar else None,
        "color": character.color,
        "background_url": f"/api/characters/{character.id}/background" if character.background else None,
        "stand": None,
        "idol_id": None,
        "name_zh": character.name_zh or None,
        "name_kana": character.name_kana or None,
        "name_romaji": character.name_romaji or None,
        "unit": character.unit,
        "unit_reading": character.unit_reading,
        "unit_order": character.unit_order,
        "greeting": None,
        "call_opener": None,
        "call_poster_url": None,
        "voxels_url": None,
    }


def make_compat_handler(app: Any) -> type[BaseHTTPRequestHandler]:
    """The original frontend's addresses, answered from this kernel."""

    store = app.store
    characters = app.characters
    user_id = app.user_id

    def character_of(conversation_id: str) -> str | None:
        rows = store.query(
            "SELECT character_id FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
        return str(rows[0]["character_id"]) if rows else None

    def qq_channel() -> Any | None:
        """The QQ channel, when this checkout has one. No channel, no plugin to show."""
        return getattr(getattr(app, "qq", None), "channel", None)

    def plugin_json() -> dict:
        """「插件」那一页要的东西：一个插件、几个开关。

        开关的人话不在后端：它是界面文案，写在文案表里。这里只说 id 和当前状态，
        加一个开关就改一处（`channels/qq.py` 的 `SWITCHES`）加一行文案。
        """
        channel = qq_channel()
        if channel is None:
            return {"id": "qq", "available": False, "switches": []}
        return {"id": "qq", "available": True, "switches": channel.switches()}

    def conversation_row(conversation_id: str) -> dict | None:
        rows = store.query(
            "SELECT id, user_id, character_id, created_at FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
        return dict(rows[0]) if rows else None

    def last_message(conversation_id: str) -> dict | None:
        rows = store.query(
            "SELECT role, text, segments, created_at FROM messages"
            " WHERE conversation_id = ? ORDER BY seq DESC LIMIT 1",
            (conversation_id,),
        )
        if not rows:
            return None
        row = rows[0]
        if row["role"] == "user":
            return {
                "role": "user",
                "text": row["text"],
                "ja": None,
                "zh": None,
                "created_at": str(row["created_at"]),
            }
        segments = json.loads(row["segments"] or "[]")
        pair = segments[0] if segments else ["", ""]
        return {
            "role": "character",
            "text": None,
            "ja": str(pair[0] or ""),
            "zh": str(pair[1] or pair[0] or ""),
            "created_at": str(row["created_at"]),
        }

    def latest_conversations() -> list[dict]:
        """每个角色最近的那段对话。列表页要的是「这一位最近说过什么」，一位一行。"""
        rows = store.query(
            "SELECT id, character_id, created_at FROM conversations"
            " WHERE user_id = ? ORDER BY rowid DESC",
            (user_id,),
        )
        seen: dict[str, dict] = {}
        for row in rows:
            seen.setdefault(str(row["character_id"]), dict(row))
        return [
            {
                "character_id": character.id,
                "conversation_id": seen[character.id]["id"],
                "created_at": _iso(seen[character.id]["created_at"]),
                "last_message": last_message(seen[character.id]["id"]),
                "unseen_reach_out": False,
            }
            for character in characters.roster
            if character.id in seen
        ]

    def affinity_json(character_id: str) -> dict:
        standing = app.affinity.read(user_id, character_id)
        return {"tier": standing.tier, "cooldown_until": _iso(standing.cooldown_until)}

    class Handler(BaseHTTPRequestHandler):
        server_version = "scbot-compat"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            # 原版前端一秒能发十几个请求（列表、头像、统计），全打出来会把日志淹掉。
            pass

        # ---- 应答

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, payload: Any, status: int = 200, extra: dict | None = None) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8", extra)

        def _empty(self, status: int = 204, extra: dict | None = None) -> None:
            self.send_response(status)
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _file(self, path: Path) -> None:
            try:
                body = path.read_bytes()
            except OSError:
                self._json({"error": "读不到这个文件"}, status=404)
                return
            kind = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".webp": "image/webp",
                ".mp3": "audio/mpeg",
            }.get(path.suffix.lower(), "application/octet-stream")
            self._send(200, body, kind, {"Cache-Control": "public, max-age=86400"})

        def _raw_body(self) -> bytes:
            """请求体，不管它以什么形式过来。

            Next 的 `/api/[...path]` 是**流着转发**请求体的，所以到了这里往往没有
            `Content-Length`，只有 `Transfer-Encoding: chunked`。只按长度读的话一个字节都
            读不到，用户那句话就成了「空的」——第一次接上原版前端就是这么错的：她回
            「empty 这次没有计入限额」。
            """
            if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                raw = bytearray()
                while True:
                    line = self.rfile.readline(64).strip()
                    if not line:
                        break
                    try:
                        size = int(line.split(b";")[0] or b"0", 16)
                    except ValueError:
                        break
                    if size == 0:
                        self.rfile.readline(64)
                        break
                    raw += self.rfile.read(size)
                    self.rfile.read(2)
                    if len(raw) > MAX_BODY:
                        break
                return bytes(raw)
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return b""
            return self.rfile.read(length)

        def _package(self) -> None:
            """把我们那份 JSON 装进一个 zip，原版的「导出」按钮才下得了东西。

            manifest 里说的是这个包**有哪几类**；原版导入时先读它再决定拿什么。我们这份
            导出是 conversations（含 messages）+ memories + profile，正好对得上那三类。
            """
            package = app.export()
            manifest = {
                "version": 1,
                "categories": ["conversations", "memories", "profile"],
                "exported_at": datetime.now(timezone.utc).isoformat(),
            }
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
                archive.writestr("data.json", json.dumps(package, ensure_ascii=False, indent=1))
            body = buffer.getvalue()
            self._send(
                200,
                body,
                "application/zip",
                {
                    "Content-Disposition": 'attachment; filename="scbot-data.zip"',
                    "Cache-Control": "no-store",
                },
            )

        def _body(self) -> dict:
            raw = self._raw_body()
            if not raw:
                return {}
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return {}
            return data if isinstance(data, dict) else {}

        # ---- 分发

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的名字
            parsed = urlparse(self.path)
            path = parsed.path
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            try:
                self._get(path, query)
            except Exception as exc:  # 适配层出错的代价不能是一页白屏
                self._json({"error": f"{type(exc).__name__}: {exc}"}, status=500)

        do_HEAD = do_GET

        def _get(self, path: str, query: dict) -> None:
            if path == "/api/health":
                self._json({"status": "ok"})
                return
            if path == "/api/auth/providers":
                # 这台部署没有第三方登录；`dev_login` 开着，登录页才给那个「直接进来」的入口。
                self._json({"providers": [], "dev_login": True, "contact": None})
                return
            if path == "/api/account":
                self._json(
                    {
                        "id": user_id,
                        "name": app.profile.get(user_id).get("address") or "producer",
                        "sign_in_methods": [],
                        "is_admin": False,
                        "exempting_keys": [],
                        "reach_outs": None,
                        "sponsor_version": None,
                    }
                )
                return
            if path == "/api/profile":
                profile = app.profile.get(user_id)
                month, day = profile.get("birth_month"), profile.get("birth_day")
                self._json(
                    {
                        "form_of_address": profile.get("address") or None,
                        "gender": profile.get("gender") if profile.get("gender") in ("male", "female") else None,
                        "timezone": profile.get("timezone") or None,
                        "birthday": {"month": int(month), "day": int(day)} if month and day else None,
                    }
                )
                return
            if path == "/api/characters":
                self._json([character_json(one) for one in characters.roster])
                return
            if CHARACTER_IMAGE.match(path):
                found = CHARACTER_IMAGE.match(path)
                kind = found.group(2)
                if kind == "background":
                    image = characters.background(found.group(1))
                else:
                    image = characters.avatar(found.group(1), scsp=kind == "scsp-avatar")
                if image is None:
                    self._json({"error": "没有这张图"}, status=404)
                    return
                self._file(image)
                return
            if CHARACTER_AFFINITY.match(path):
                found = CHARACTER_AFFINITY.match(path)
                self._json(affinity_json(found.group(1)))
                return
            if path == "/api/conversations/latest":
                self._json(latest_conversations())
                return
            if path == "/api/conversations":
                character_id = query.get("character_id") or ""
                if character_id not in {one.id for one in characters.roster}:
                    self._json({"error": "没有这个角色"}, status=404)
                    return
                self._json(
                    [
                        {
                            "id": row["id"],
                            "user_id": user_id,
                            "character_id": character_id,
                            "created_at": _iso(row["created_at"]),
                            "title": None,
                            "last_message": last_message(str(row["id"])),
                            "ended": False,
                        }
                        for row in app.conversations(character_id)
                    ]
                )
                return
            if MESSAGES_.match(path):
                conversation_id = MESSAGES_.match(path).group(1)
                character_id = character_of(conversation_id)
                if character_id is None:
                    self._json({"error": "没有这段对话"}, status=404)
                    return
                self._json([message_json(m) for m in app.messages(character_id, conversation_id)])
                return
            if path == "/api/memories":
                self._json(
                    [
                        {
                            "id": memory["id"],
                            "text": memory["text"],
                            "source_character_id": memory["source_character_id"],
                            "source_character_name": memory["source_character_name"],
                            "created_at": _iso(memory["created_at"]),
                            "superseded": memory["superseded"],
                        }
                        for memory in app.memory.everything(user_id)
                    ]
                )
                return
            if path == "/api/stats":
                self._json(app.stats(query.get("range") or "all", query.get("timezone") or None))
                return
            if path == "/api/gifts":
                if app.gifts is None:
                    self._json({"open": False, "draws": 0, "max_draws": 0, "backpack": [], "reset_cards": 0, "next_draw_at": None, "resettable": [], "characters": []})
                    return
                self._json(app.gifts.state(user_id))
                return
            if path == "/api/gifts/catalog":
                if app.gifts is None:
                    self._json([])
                    return
                self._json([gift.described() for gift in app.gifts.catalog])
                return
            if path == "/api/announcements":
                self._json([])
                return
            if path == "/api/service-status":
                self._json({"window_minutes": 30, "services": [{"service": "chat_llm", "status": "ok"}]})
                return
            if path == "/api/settings/world":
                # 「世界观」那一页：这个部署演哪些作品，每个作品下的人各有几份文件。
                # 这原本是我们自己那份前端的独有页面（`/settings/world`），搬过来以后它就不必存在了。
                #
                # 结构是**作品 → 它的人**，而且作品是**平级**的：`characters/works/<id>.toml`，
                # 没有一个「主作品」给别的作品当父节点。人一律全列，有包没包由页面折叠着说。
                self._json(
                    {
                        "works": [
                            {
                                **group,
                                "characters": [
                                    world_character_json(one) for one in group["characters"]
                                ],
                            }
                            for group in characters.work_groups()
                        ],
                    }
                )
                return
            if path == "/api/settings/plugins":
                self._json({"plugins": [plugin_json()]})
                return
            if path == "/api/account/keys/exemption":
                self._json({"turns": False, "calls": False})
                return
            if path == "/api/account/data-package":
                self._package()
                return
            if path == "/api/account/data-package/export-check":
                self._empty(204)
                return
            if path.startswith("/api/account/keys/") and path.endswith("/check"):
                # 这台部署只有部署者的 key：给一个说得清的拒绝，不是 404 让页面报「出错」。
                self._json({"error": "这台部署不支持自带密钥"}, status=400)
                return
            if path == "/api/landing":
                self._json({"memory_still_url": None})
                return
            if path in ("/api/admin", "/api/ice-servers", "/api/offer"):
                # 这台部署没有管理台、没有通话：前端见到 404 就不画入口。
                self._json({"error": "这个部署没有开放"}, status=404)
                return
            if IMAGE.match(path):
                image_id = IMAGE.match(path).group(1)
                found = app.photos.path(image_id)
                if found is None:
                    self._json({"error": "没有这张图"}, status=404)
                    return
                self._file(found)
                return
            if PHOTO.match(path):
                self._json({"status": "failed", "reason": None}, status=404)
                return
            if PHOTO_IMAGE.match(path):
                self._json({"error": "没有这张照片"}, status=404)
                return
            self._json({"error": "没有这个地址"}, status=404)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            body = self._body()
            try:
                self._post(path, query, body)
            except Exception as exc:
                self._json({"error": f"{type(exc).__name__}: {exc}"}, status=500)

        def _post(self, path: str, query: dict, body: dict) -> None:
            if path.startswith("/api/account/keys/") and path.endswith("/check"):
                # 原版是 POST 去试连通性的（GET 那条没人用，但一起答了）。
                self._json({"error": "这台部署不支持自带密钥"}, status=400)
                return
            if path == "/api/settings/plugins/qq":
                channel = qq_channel()
                if channel is None:
                    self._json({"error": "这台部署没有 QQ 通道"}, status=404)
                    return
                asked = body.get("switches") or {}
                if not isinstance(asked, dict):
                    self._json({"error": "switches 要是对象"}, status=422)
                    return
                unknown = [name for name in asked if not channel.set_switch(str(name), bool(asked[name]))]
                if unknown:
                    self._json({"error": f"没有这些开关：{'、'.join(unknown)}"}, status=422)
                    return
                self._json(plugin_json())
                return
            if path == "/api/auth/dev-login":
                # 原版前端靠这个 cookie 判断「登录过」。值是什么它不看，但必须有。
                self._empty(
                    204,
                    {
                        "Set-Cookie": f"{SESSION_COOKIE}=local; Path=/; HttpOnly; SameSite=Lax",
                    },
                )
                return
            if path == "/api/auth/logout":
                self._empty(204, {"Set-Cookie": f"{SESSION_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax"})
                return
            if path == "/api/conversations":
                character_id = str(body.get("character_id") or "")
                if character_id not in {one.id for one in characters.roster}:
                    self._json({"error": "没有这个角色"}, status=404)
                    return
                conversation = app.new_conversation(character_id)
                self._json(
                    {
                        "id": conversation.id,
                        "user_id": user_id,
                        "character_id": character_id,
                        "created_at": _iso(conversation.created_at),
                        "title": None,
                    },
                    status=201,
                )
                return
            if TURNS_.match(path):
                self._turn(TURNS_.match(path).group(1), body)
                return
            if path == "/api/images":
                try:
                    saved = app.photos.save(str(body.get("data") or ""))
                except ValueError:
                    self._json({"error": "不是图片"}, status=422)
                    return
                self._json({"id": saved["id"]}, status=201)
                return
            if CHARACTER_GIFTS.match(path):
                character_id = CHARACTER_GIFTS.match(path).group(1)
                if app.gifts is None:
                    self._json({"error": "礼物没有开放"}, status=404)
                    return
                result = app.gifts.give(user_id, character_id, str(body.get("gift_id") or ""))
                if "error" in result:
                    self._json(result, status=400)
                    return
                self._json(result)
                return
            if path == "/api/gifts/draws":
                if app.gifts is None:
                    self._json({"error": "礼物没有开放"}, status=404)
                    return
                self._json(app.gifts.draw(user_id, all=bool(body.get("all"))))
                return
            if path == "/api/gifts/resets":
                if app.gifts is None:
                    self._json({"error": "礼物没有开放"}, status=404)
                    return
                self._json(app.gifts.use_reset_card(user_id))
                return
            self._json({"error": "没有这个地址"}, status=404)

        def do_PUT(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            body = self._body()
            if parsed.path == "/api/profile":
                month = (body.get("birthday") or {}).get("month") if isinstance(body.get("birthday"), dict) else None
                day = (body.get("birthday") or {}).get("day") if isinstance(body.get("birthday"), dict) else None
                app.profile.set(
                    user_id,
                    address=str(body.get("form_of_address") or ""),
                    gender=str(body.get("gender") or "default"),
                    month=int(month) if month else None,
                    day=int(day) if day else None,
                    timezone_name=str(body.get("timezone") or ""),
                )
                self._json(
                    {
                        "form_of_address": body.get("form_of_address"),
                        "gender": body.get("gender"),
                        "timezone": body.get("timezone"),
                        "birthday": body.get("birthday"),
                    }
                )
                return
            self._json({"error": "没有这个地址"}, status=404)

        def do_DELETE(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            if CONVERSATION.match(path):
                gone = app.delete_conversation(CONVERSATION.match(path).group(1))
                self._empty(204) if gone else self._json({"error": "没有这段对话"}, status=404)
                return
            if MEMORY.match(path):
                memory_id = int(MEMORY.match(path).group(1))
                app.memory.delete(user_id, memory_id)
                self._empty(204)
                return
            if path == "/api/memories":
                app.memory.clear(user_id)
                self._empty(204)
                return
            self._json({"error": "没有这个地址"}, status=404)

        # ---- 一个回合

        def _turn(self, conversation_id: str, body: dict) -> None:
            character_id = character_of(conversation_id)
            if character_id is None:
                self._json({"error": "没有这段对话"}, status=404)
                return
            text = str(body.get("text") or "")
            images = body.get("images") or []
            photos = [str(one) for one in images][:4] if isinstance(images, list) else []
            own_keys = body.get("own_keys") if isinstance(body.get("own_keys"), dict) else None

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            def emit(payload: dict) -> None:
                self.wfile.write(b"data: " + json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n\n")
                self.wfile.flush()

            # 用户那句先回声：原版的日志在发出时就画上了，它靠这个事件确认「后端收下了」。
            emit({"type": "user", "text": text})

            async def run() -> None:
                async for event in app.turn(character_id, text, photos, conversation_id, own_keys):
                    if isinstance(event, SegmentDelta):
                        emit({"type": "delta", "segment": event.segment, "lang": event.lang, "text": event.text})
                    elif isinstance(event, TurnDone):
                        message = store.message(event.message_id)
                        if message is None:
                            emit({"type": "error", "message": "回复没有存下来"})
                            continue
                        emit(
                            {
                                "type": "done",
                                "message": message_json(
                                    {
                                        "id": message.id,
                                        "role": message.role,
                                        "text": message.text,
                                        "segments": [[s.ja, s.zh] for s in message.segments],
                                        "at": message.created_at,
                                    }
                                ),
                                "affinity": affinity_json(character_id),
                                "ended": None,
                            }
                        )
                    elif isinstance(event, TurnRejected):
                        emit(
                            {
                                "type": "error",
                                "message": REFUSALS.get(event.reason, f"这句话没能发出去（{event.reason}）"),
                            }
                        )
                    elif isinstance(event, TurnFailed):
                        emit({"type": "error", "message": "角色暂时无法回复，请稍后再试。"})

            try:
                asyncio.run(run())
            except BrokenPipeError:
                # 页面走开了：这一轮照常记在库里，只是没人听。
                pass
            except Exception:
                try:
                    emit({"type": "error", "message": "回复中断了，请再试一次"})
                except Exception:
                    pass

    return Handler


def serve_compat(app: Any, host: str, port: int) -> ThreadingHTTPServer:
    """Start the compat server on its own thread; the caller keeps the handle to shut down."""
    server = ThreadingHTTPServer((host, port), make_compat_handler(app))
    server.daemon_threads = True
    return server
