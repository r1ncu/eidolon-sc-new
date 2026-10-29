"""QZone over HTTP: her own wall, and the people who answered it.

The channel publishes 说说 through NapCat's `send_qzone_msg`. Reading them back — and reading
the comments under them — is **not** part of NapCat's API: its only official QZone actions are
send and delete, and `get_qzone_msg_list` / `send_qzone_comment` exist only in third-party
extensions (this deployment's NapCat answers `retcode 1404 不支持的API` to all of them).

There is a shorter road. NapCat hands over the account's own QZone Cookie (`get_cookies`), and
that Cookie is the whole permission model of the web endpoints, so this module talks to QZone
directly:

- **`my_posts()`** — `emotion_cgi_msglist_v6`, the same list the 「我的说说」 page shows. JSON.
- **`comments()`** — `emotion_cgi_msgdetail_v6`: everything under one 说说, including who said
  it and that comment's own id (`tid`), which is what makes answering one person possible.
- **`reply()`** — `emotion_cgi_re_feeds` on the h5 host, `paramstr=2`, `commentId`/`commentUin`
  and a `@{uin:…,nick:…,auto:1}` marker in the content: QZone's 楼中楼.
- **`comment()`** — the same CGI with `paramstr=1`: a fresh comment on the post.

Three things follow from using an undocumented endpoint, and all three are deliberate:

- **It is fragile.** Tencent renames fields; the friends' stream (which this module no longer
  reads) already answered with JavaScript literals rather than JSON. Everything that parses a
  response is a pure function here, so a field change is one test to fix.
- **`topicId` is `<author uin>_<tid>__1`.** A bare tid is decoded as `t1_uin=0 / t1_tid=0`
  and answered with `-10004 参数错误`; dropping the `__1` still gets `code:0` but writes a
  plain comment. Found by trying, written here so nobody has to find it twice.
- **A reply that is not a 楼中楼 still answers `code:0`.** Getting it wrong looks like
  success, which is the worst kind of wrong; the only check is reading the thread back and
  looking at the `list_3` under the comment.
- **There is no delete counterpart.** `emotion_cgi_delcomment` answers HTTP 500 on this account
  (seven parameter shapes tried), so a comment sent from here can only be removed by hand.
  Every gate in `qzone_reply.py` sits in front of that.

The Cookie is cached for `COOKIE_TTL`: `get_cookies` is an action call on the socket that also
carries chat, and the value changes hourly at most.
"""

import asyncio
import html as htmllib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
"""What the web client sends. QZone answers `-10000` to some endpoints without it."""

COOKIE_TTL = 600.0
"""Seconds a Cookie is reused. Long enough to save the round-trip, short enough that a
re-login is picked up without a restart."""

POSTS_URL = (
    "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
)
COMMENTS_URL = (
    "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msgdetail_v6"
)
COMMENT_URL = (
    "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
)
REPLY_URL = (
    "https://h5.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
)
"""Replies go to the h5 host, comments to the main one. Same CGI, different front door — and
the reply only becomes a 楼中楼 through the h5 one."""

TIMEOUT = 25.0

LOGIN_CODES = (-10000, -3000, -4001)
"""What QZone answers when the Cookie is stale. Not an error to retry blindly: the Cookie has
to be fetched again, which `_get` does once before giving up."""


class WebError(RuntimeError):
    """`kind` matches `qzone.PublishError`: `network`, `login`, `limited`, `rejected`."""

    def __init__(self, kind: str, detail: str = "") -> None:
        self.kind = kind
        self.detail = detail
        super().__init__(f"{kind}: {detail}" if detail else kind)


def hash33(text: str) -> int:
    """`g_tk` — QZone's signature for a Cookie. Same function the web client uses."""
    value = 5381
    for char in text:
        value += (value << 5) + ord(char)
    return value & 0x7FFFFFFF


def gtk_of(cookie: str) -> int:
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        if name == "p_skey":
            return hash33(value)
    return 5381


def _flat(raw: str) -> str:
    """HTML → text a person would read: no tags, no `&nbsp;`, no runs of whitespace."""
    text = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = htmllib.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _load(body: str) -> dict[str, Any]:
    """The object out of a response, wrapper and all.

    Two shapes have to survive this: `_preloadCallback({…})` (the read endpoints) and the
    iframe shell the write endpoints answer with — that one is a `<script>` full of
    `try{…}catch(e){…}` braces *before* the payload, so grabbing the first `{` and the last
    `}` is not parsing, it is guessing. Each `{` is tried in turn and the first one that
    decodes as an object wins.
    """
    decoder = json.JSONDecoder()
    for index, char in enumerate(body):
        if char != "{":
            continue
        try:
            data, _ = decoder.raw_decode(body[index:])
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def _stamp(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def posts_of(body: str) -> list[dict[str, Any]]:
    """Her own 说说, as `{tid, content, at, cmtnum}`.

    `cmtnum` is the whole point: a post nobody answered should cost no second request.
    """
    rows: list[dict[str, Any]] = []
    for item in _load(body).get("msglist") or []:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("tid") or "").strip()
        if not tid:
            continue
        rows.append(
            {
                "tid": tid,
                "content": _flat(str(item.get("content") or "")),
                "at": _stamp(item.get("created_time") or item.get("createTime2")),
                "cmtnum": _stamp(item.get("cmtnum")),
            }
        )
    return rows


NESTED = ("list_3", "replyList", "replylist", "replies")
"""Where the replies under a comment hide. `list_3` is what this deployment returns; the other
three are the names other builds have used, because this is an undocumented endpoint."""

REPLY_MARKER = re.compile(r"^@\{uin:(\d+),nick:[^,{}:]*(?:,who:1)?,auto:1\}\s*")
"""What QZone puts in front of a 楼中楼 reply. It is how the client draws 「回复 某某」, and it
is part of the stored text — so reading a comment back means taking it off first."""


def reply_marker(uin: str, name: str) -> str:
    """The prefix that makes QZone hang a comment under someone instead of beside them.

    Same escaping the web client uses: a nickname with `,` `{` `}` or `:` inside would
    otherwise end the marker early and the reply would land in the wrong place.
    """
    nick = str(name or "")
    for raw, encoded in (("%", "%25"), (",", "%2C"), ("}", "%7D"), ("{", "%7B"), (":", "%3A")):
        nick = nick.replace(raw, encoded)
    return f"@{{uin:{uin},nick:{nick},auto:1}}"


def unmark(text: str) -> str:
    return REPLY_MARKER.sub("", text).strip()


def marker_target(text: str) -> str:
    """The uin the marker points at — QZone's way of writing 「回复 某某」.

    Empty when there is no marker, which means the line was not written *at* anybody: a
    top-level comment, or a reply from a client that does not set one.
    """
    match = REPLY_MARKER.match(text)
    return match.group(1) if match else ""


def comments_of(body: str) -> list[dict[str, Any]]:
    """Everything under one 说说 — the comments and the replies under them.

    A row is `{tid, uin, name, content, at, root, root_uin}`. `tid` is the **comment's** own
    id; `root` is the top-level comment a reply hangs under (empty for a top-level comment).
    Both matter when answering: QZone anchors a reply at the root of the thread, and spells
    the person being answered in the content marker — a reply to a reply is still written into
    the same thread.
    """
    rows: list[dict[str, Any]] = []
    for item in _load(body).get("commentlist") or []:
        if not isinstance(item, dict):
            continue
        root = _comment_row(item)
        if root is None:
            continue
        rows.append(root)
        for key in NESTED:
            for sub in item.get(key) or []:
                if not isinstance(sub, dict):
                    continue
                row = _comment_row(sub, root=root["tid"], root_uin=root["uin"])
                if row is not None:
                    rows.append(row)
    return rows


def _comment_row(
    item: dict[str, Any], *, root: str = "", root_uin: str = ""
) -> dict[str, Any] | None:
    tid = str(item.get("tid") or item.get("commentid") or item.get("commentId") or "").strip()
    if not tid:
        return None
    raw = _flat(str(item.get("content") or ""))
    return {
        "tid": tid,
        "uin": str(item.get("uin") or "").strip(),
        "name": str(item.get("name") or "").strip(),
        "content": unmark(raw),
        "at": _stamp(item.get("create_time") or item.get("createTime2")),
        "root": root,
        "root_uin": root_uin,
        # 楼里这一句是「回复谁」：空 = 没人（一级评论）。别人之间的对话靠它分辨。
        "reply_to": marker_target(raw),
    }


def check_comment(body: str) -> tuple[bool, str]:
    """Whether the request succeeded, and what it said. QZone answers JSON with a `code`."""
    data = _load(body)
    if not data:
        return False, f"不是 JSON（{body[:60]}）"
    code = data.get("code", data.get("ret", 0))
    if code in (0, None):
        return True, str(data.get("commentId") or data.get("commentid") or "")
    sub = data.get("subcode") or ""
    message = data.get("message") or data.get("msg") or ""
    return False, f"code={code} subcode={sub} {message}".strip()


class Web:
    """One account's QZone, over HTTP, with the Cookie NapCat already has."""

    def __init__(
        self,
        client: Any,
        *,
        self_id: str = "",
        timeout: float = TIMEOUT,
        cookie_ttl: float = COOKIE_TTL,
        now: Any = None,
        log: Any = None,
    ) -> None:
        self._client = client
        self.self_id = str(self_id or "")
        self.timeout = timeout
        self.cookie_ttl = cookie_ttl
        self._now = now or time.monotonic
        self._log = log or (lambda line: None)
        self._cookie = ""
        self._uin = 0
        self._gtk = 0
        self._at = 0.0

    # ---- auth

    async def _auth(self, *, fresh: bool = False) -> tuple[str, int, int]:
        """`(cookie, uin, g_tk)`. One action call per ten minutes, cached in between."""
        if not fresh and self._cookie and (self._now() - self._at) < self.cookie_ttl:
            return self._cookie, self._uin, self._gtk
        try:
            data = await self._client.call("get_cookies", domain="user.qzone.qq.com")
            cookie = str((data or {}).get("cookies") or "").strip()
            if not cookie:
                raise WebError("login", "get_cookies 回了空")
            info = await self._client.call("get_login_info")
            uin = int((info or {}).get("user_id") or self.self_id or 0)
        except WebError:
            raise
        except Exception as exc:
            raise WebError("network", f"{type(exc).__name__}: {exc}") from exc
        self._cookie, self._uin, self._gtk = cookie, uin, gtk_of(cookie)
        self._at = self._now()
        return self._cookie, self._uin, self._gtk

    # ---- reading

    async def my_posts(self, count: int = 3) -> list[dict[str, Any]]:
        """Her own wall, newest first."""
        payload = await self._get(POSTS_URL, self._posts_query(count))
        return posts_of(payload)

    def _posts_query(self, count: int) -> Any:
        def build(uin: int, gtk: int) -> str:
            return "?" + urllib.parse.urlencode(
                {
                    "uin": uin,
                    "ftype": 0,
                    "sort": 0,
                    "pos": 0,
                    "num": max(int(count), 1),
                    "replynum": 0,
                    "g_tk": gtk,
                    "callback": "_preloadCallback",
                    "code_version": 1,
                    "format": "jsonp",
                    "need_private_comment": 1,
                }
            )

        return build

    async def comments(self, tid: str, count: int = 30) -> list[dict[str, Any]]:
        """Everything under one 说说."""
        payload = await self._get(COMMENTS_URL, self._comments_query(tid, count))
        return comments_of(payload)

    def _comments_query(self, tid: str, count: int) -> Any:
        def build(uin: int, gtk: int) -> str:
            return "?" + urllib.parse.urlencode(
                {
                    "uin": uin,
                    "tid": tid,
                    "num": max(int(count), 1),
                    "g_tk": gtk,
                    "format": "json",
                    "inCharset": "utf-8",
                    "outCharset": "utf-8",
                }
            )

        return build

    # ---- writing

    async def reply(
        self,
        tid: str,
        uin: str,
        comment_tid: str,
        comment_uin: str,
        text: str,
        *,
        name: str = "",
        root_tid: str = "",
        root_uin: str = "",
    ) -> str:
        """Answer one person, **inside** the thread — QZone's 楼中楼.

        Four things make it land under that comment rather than beside it, and all four are
        required: the h5 host, `paramstr=2`, `commentId` / `commentUin` naming the thread root,
        and a `@{uin:…,nick:…,auto:1}` marker in front of the words (that marker is what the
        client draws as 「回复 某某」). Send anything less and QZone answers `code:0` while
        quietly creating a plain comment — the failure mode is a success response, which is
        why this was wrong for a while without anything in the log.

        `comment_tid`/`comment_uin` are the person being answered; `root_*` is the top-level
        comment the thread hangs under (equal to them for a top-level comment).
        """
        form = self._comment_form(tid, uin, text, paramstr=2)
        form["commentId"] = str(root_tid or comment_tid)
        form["commentUin"] = str(root_uin or comment_uin)
        form["content"] = f"{reply_marker(comment_uin, name or comment_uin)} {text}"
        return await self._send(form, REPLY_URL, tid=tid, uin=uin)

    async def comment(self, tid: str, uin: str, text: str) -> str:
        """One fresh comment on the post (here: her own), not an answer to anyone."""
        return await self._send(
            self._comment_form(tid, uin, text, paramstr=1), COMMENT_URL, tid=tid, uin=uin
        )

    def _comment_form(self, tid: str, uin: str, text: str, *, paramstr: int) -> dict[str, Any]:
        # `topicId` 是「作者的 uin + 下划线 + tid + `__1`」——不是裸 tid（会被当成
        # `t1_uin=0`，回 `-10004 参数错误`），也不是少了 `__1` 的 `uin_tid`（能过，但写出去的
        # 评论只到说说下面，不进楼）。`paramstr` 是 1 还是 2，就是「新评论」和「回复」的区别。
        return {
            "topicId": f"{uin}_{tid}__1",
            "uin": str(uin or ""),
            "hostUin": str(uin or ""),
            "feedsType": 100,
            "platformid": 50,
            "content": text,
            "plat": "qzone",
            "source": "ic",
            "ref": "feeds",
            "format": "fs",
            "private": 0,
            "paramstr": paramstr,
            "isSignIn": 0,
            "richval": "",
            "richtype": "",
            "appid": 311,
            "busi_param": "{}",
            "qzreferrer": f"https://user.qzone.qq.com/{uin or self.self_id}",
            "inCharset": "utf-8",
            "outCharset": "utf-8",
        }

    async def _send(self, form: dict[str, Any], base: str, *, tid: str, uin: str) -> str:
        body = await self._post(base, form, tid=tid, uin=uin)
        ok, detail = check_comment(body)
        if not ok:
            raise WebError(_kind_of(detail), detail)
        return detail

    # ---- the socket of it

    async def _get(self, base: str, query: Any) -> str:
        cookie, uin, gtk = await self._auth()
        body = await self._fetch(
            base + query(uin, gtk), cookie, referer=f"https://user.qzone.qq.com/{uin}"
        )
        if _needs_login(body):
            # A stale Cookie is the one failure worth retrying once, because the fix is a new
            # Cookie and NapCat can produce one immediately.
            cookie, uin, gtk = await self._auth(fresh=True)
            body = await self._fetch(
                base + query(uin, gtk), cookie, referer=f"https://user.qzone.qq.com/{uin}"
            )
        if _needs_login(body):
            raise WebError("login", "Cookie 失效，重新取过还是不行")
        return body

    async def _post(self, base: str, form: dict[str, Any], *, tid: str, uin: str) -> str:
        cookie, me, gtk = await self._auth()
        # `g_tk` 必须在 **query** 里：只放在表单里，写接口会直接回 HTTP 500（包体还是空的）。
        # 读接口两种都认，所以这个差别只在「发评论」上露出来，而且看起来像「被限流」。
        sep = "&" if "?" in base else "?"
        url = f"{base}{sep}g_tk={gtk}"
        referer = f"https://user.qzone.qq.com/{uin or me}/mood/{tid}"
        data = urllib.parse.urlencode({**form, "g_tk": gtk}).encode("utf-8")
        return await self._fetch(url, cookie, referer=referer, data=data, method="POST")

    async def _fetch(
        self,
        url: str,
        cookie: str,
        *,
        referer: str,
        data: bytes | None = None,
        method: str = "GET",
    ) -> str:
        def work() -> str:
            request = urllib.request.Request(
                url,
                data=data,
                method=method,
                headers={
                    "Cookie": cookie,
                    "Referer": referer,
                    # 网页端会带它。写接口的 500 与它无关（那是 `g_tk` 的位置问题，见 `_post`），
                    # 但少一个「和浏览器一样」的头只会让风控更早注意到你。
                    "Origin": "https://user.qzone.qq.com",
                    "User-Agent": UA,
                    "Accept": "*/*",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read(800_000)
            except urllib.error.HTTPError as exc:  # 403/500 都带包体，读出来再判
                raw = exc.read(4000) or b""
                # 带上包体：500 的正文里通常有一句「为什么」（`-10052 该条内容已被删除`
                # 这种），只留一个状态码，事后就只能猜。
                said = raw.decode("utf-8", "replace")[:180].replace("\n", " ")
                if exc.code in (401, 403):
                    raise WebError("login", f"HTTP {exc.code} {said}") from exc
                raise WebError("rejected", f"HTTP {exc.code} {said}") from exc
            except Exception as exc:
                raise WebError("network", f"{type(exc).__name__}: {exc}") from exc
            return raw.decode("utf-8", "replace")

        # urllib is blocking; the channel's event loop also carries chat.
        return await asyncio.to_thread(work)


def _needs_login(body: str) -> bool:
    if not body:
        return True
    for code in LOGIN_CODES:
        if f'"code":{code}' in body or f'"code": {code}' in body or f'"ret":{code}' in body:
            return True
    return any(word in body for word in ("请先登录", "登录超时", "login required", "ptlogin2"))


def _kind_of(detail: str) -> str:
    if any(word in detail for word in ("频繁", "风控", "限制", "验证", "too often")):
        return "limited"
    if any(word in detail for word in ("登录", "login", "-10000", "skey")):
        return "login"
    return "rejected"
