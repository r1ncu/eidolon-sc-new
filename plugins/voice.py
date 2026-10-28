"""Her voice, when somebody has paid for one.

Upstream voices every segment through Fish Audio while the reply streams, which is what a
call needs. This file does the page's version: a reply, on demand, as one mp3 — the shape
a browser can play without a call layer.

The important part is what happens without a key: the plugin reports itself switched off
with a reason, the page hides the button, and nothing else changes. A voice that cannot be
bought must never be a server that cannot start.
"""

import base64
import json
import urllib.request
from typing import Any

from core.plugin import Plugin, Route

DEFAULT_BASE = "https://api.fish.audio"
DEFAULT_MODEL = "s1"
TIMEOUT = 60.0


class Voice(Plugin):
    name = "voice"

    def __init__(
        self,
        api_key: str = "",
        *,
        base_url: str = DEFAULT_BASE,
        model: str = DEFAULT_MODEL,
        open_post: Any | None = None,
    ) -> None:
        self._key = (api_key or "").strip()
        self._base = (base_url or DEFAULT_BASE).rstrip("/")
        self._model = model or DEFAULT_MODEL
        self._post = open_post or self._request

    @property
    def ready(self) -> bool:
        return bool(self._key)

    def capabilities(self) -> dict[str, object]:
        if not self.ready:
            return {"enabled": False, "why": "needs FISH_AUDIO_API_KEY"}
        return {"enabled": True, "model": self._model, "format": "mp3"}

    def speak(self, text: str, voice_id: str) -> bytes:
        """One mp3 for one line. `voice_id` comes from the character's configuration —
        the kernel never learns that voices exist."""
        if not self.ready:
            raise RuntimeError("no_key")
        payload = {
            "text": text.strip(),
            "format": "mp3",
            "mp3_bitrate": 128,
            "latency": "normal",
            "reference_id": voice_id,
        }
        return self._post(payload)

    def _request(self, payload: dict) -> bytes:
        request = urllib.request.Request(
            f"{self._base}/v1/tts",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._key}",
                "model": self._model,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read()

    def routes(self) -> dict[tuple[str, str], Route]:
        async def tts(query, body):  # noqa: ANN001 - plugin.Route
            if not self.ready:
                return {"error": "voice.off"}
            text = str(body.get("text") or "").strip()
            voice_id = str(body.get("voice_id") or "").strip()
            if not text or not voice_id:
                return {"error": "voice.needs_text_and_voice"}
            try:
                audio = self.speak(text[:600], voice_id)
            except Exception as exc:  # the page says "no voice this time", the turn stands
                return {"error": f"{type(exc).__name__}: {exc}"}
            return {"audio": "data:audio/mpeg;base64," + base64.b64encode(audio).decode("ascii")}

        return {("POST", "/api/voice"): tts}
