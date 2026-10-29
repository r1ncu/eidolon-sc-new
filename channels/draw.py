"""把她写在 `<photo>` 里的那句话变成一张图。

**为什么有这一层**：她早就「说要发图」——回复协议里有 `<photo>tag</photo>`，解析器只认
回复最前面那一个，还能标 `status="failed"`；但**从来没有谁去画那张图**（`plugins/photos.py`
开头写着「there is no image model configured」）。这里就是那个被留空的渲染器。

两种实现：

- **`FakePainter`**：不联网、返回一张 1×1 的 PNG、记下每次要画什么。先拿它把「她写了标记 →
  真的有一张图发到私聊里」这条链路测通，再接真的服务——真的那家要么要花钱，要么会
  把别人的图传出去，都不该拿来验自己的逻辑。
- **`OpenAIPainter`**：`POST {base}/images/generations`，OpenAI 兼容的表单。`b64_json` 优先，
  只有 url 的再拉一次。和 `core/llm.py` 一样只用标准库。

调用方（`channels/qq.py::_send_picture`）负责配额、只在私聊放行、以及失败后不要假装发过。
这里只管「给我一句话，还你一张图」。
"""

import asyncio
import base64
import hashlib
import json
import struct
import urllib.error
import urllib.request
import zlib
from typing import Any


def solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A PNG of one flat colour, written by hand.

    假画家也得给出**看得见**的东西：1×1 的透明像素发到 QQ 里是一条看不见的气泡，
    那就等于没法确认「图到底发出去了没有」。这一块纯色有 64×64，肉眼能看见，
    而且同一个提示词永远是同一个颜色——两次不同的描述能看出区别。
    """
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


class DrawError(Exception):
    """生成失败。`kind` 用来给日志分类，不用来判断要不要重试——发不出去就老实说不发。"""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


class FakePainter:
    """假画家。每张都是同一个 1×1 PNG，唯一的产品是「她被要求画过什么」。"""

    def __init__(self, fail: Exception | None = None) -> None:
        self.asked: list[str] = []
        self.fail = fail

    async def paint(self, prompt: str) -> bytes:
        self.asked.append(prompt)
        if self.fail is not None:
            raise self.fail
        # 同一句话同一块颜色：她换了描述，你能一眼看出来。
        digest = hashlib.sha256(prompt.encode("utf-8")).digest()
        return solid_png(64, 64, (digest[0], digest[1], digest[2]))


class OpenAIPainter:
    """OpenAI 兼容的 `/images/generations`。`model` 是必需项，不给就当没配。"""

    def __init__(self, base_url: str, api_key: str, model: str, *, timeout: float = 90.0) -> None:
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.model = model
        self.timeout = timeout

    async def paint(self, prompt: str) -> bytes:
        # 阻塞的 HTTP 放到线程里：一张图要几十秒，卡住主循环等于整个群都不回话。
        return await asyncio.to_thread(self._paint, prompt)

    def _paint(self, prompt: str) -> bytes:
        body = json.dumps(
            {
                "model": self.model,
                "prompt": prompt,
                "n": 1,
                "size": "1024x1024",
                "response_format": "b64_json",
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base}/images/generations",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                payload = json.loads(res.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raise DrawError("rejected", f"HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise DrawError("network", f"{type(exc).__name__}: {exc}") from exc
        if not isinstance(payload, dict):
            raise DrawError("empty", "回包不是一个对象")
        data = payload.get("data") or []
        first: Any = data[0] if data else {}
        if not isinstance(first, dict):
            raise DrawError("empty", "回包里 data[0] 不是一个对象")
        if first.get("b64_json"):
            try:
                return base64.b64decode(str(first["b64_json"]))
            except (ValueError, TypeError) as exc:
                raise DrawError("empty", "b64_json 不是合法的 base64") from exc
        if first.get("url"):
            try:
                with urllib.request.urlopen(str(first["url"]), timeout=self.timeout) as res:
                    return res.read()
            except (urllib.error.URLError, TimeoutError) as exc:
                raise DrawError("network", f"拉图失败：{exc}") from exc
        raise DrawError("empty", "回包里既没有 b64_json 也没有 url")


def make(kind: str, base_url: str, api_key: str, model: str) -> Any:
    """按配置造一个画家。没配齐就返回 None，调用方当作「这个功能没有」。"""
    name = (kind or "").strip().lower()
    if name == "fake":
        return FakePainter()
    if name in ("openai", "openai-compatible"):
        if base_url and api_key and model:
            return OpenAIPainter(base_url, api_key, model)
    return None
