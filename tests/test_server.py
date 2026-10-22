"""The server over a real socket: the page, the state, and a turn that streams.

The model is `ScriptedLLM`, so this proves the wiring and not the service — which is the
right split: a test that needs a key is a test that fails when the key expires.
"""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from core.llm import ScriptedLLM
from core.settings import Settings
from serve import App, make_handler

REPLY = "<ja>おはよう</ja><zh>早上好</zh><ja>今日は早いね</ja><zh>今天真早啊</zh>"
SHEET = "# 人设：测试角色\n\n你是测试角色，说话简短。\n"
PAGE = "<html><title>test</title></html>"


class TestServer(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "characters").mkdir()
        (root / "characters" / "test.md").write_text(SHEET, encoding="utf-8")
        settings = Settings(
            base_url="http://unused",
            api_key="unused",
            model="fake",
            db=str(root / "kernel.db"),
            characters=str(root / "characters"),
            turns_per_hour=0,
        )
        self.app = App(settings, ScriptedLLM(REPLY, size=4))
        self.addCleanup(self.app.close)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app, PAGE.encode()))
        self.addCleanup(self.server.server_close)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def get(self, path: str) -> tuple[int, str]:
        with urllib.request.urlopen(self.base + path, timeout=10) as response:
            return response.status, response.read().decode("utf-8")

    def post(self, path: str, payload: dict) -> tuple[int, str]:
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read().decode("utf-8")

    def events(self, body: str) -> list[dict]:
        return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]

    def test_the_page_is_served(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertEqual(body, PAGE)

    def test_state_names_the_character_and_starts_empty(self):
        state = json.loads(self.get("/api/state")[1])
        self.assertEqual(state["character"]["name"], "测试角色")
        self.assertEqual(state["messages"], [])
        self.assertIn("quota", state["capabilities"])

    def test_a_turn_streams_deltas_and_ends_with_done(self):
        _, body = self.post("/api/turns", {"text": "おはよう"})
        events = self.events(body)
        self.assertEqual(events[0]["type"], "delta")
        self.assertEqual(events[-1]["type"], "done")
        japanese = "".join(
            e["text"] for e in events if e["type"] == "delta" and e["lang"] == "ja"
        )
        self.assertEqual(japanese, "おはよう今日は早いね")

    def test_the_turn_is_still_there_afterwards(self):
        self.post("/api/turns", {"text": "おはよう"})
        state = json.loads(self.get("/api/state")[1])
        self.assertEqual([m["role"] for m in state["messages"]], ["user", "assistant"])
        self.assertEqual(state["messages"][0]["text"], "おはよう")
        self.assertEqual(state["messages"][1]["segments"][0], ["おはよう", "早上好"])

    def test_an_empty_turn_never_reaches_the_model(self):
        _, body = self.post("/api/turns", {"text": "   "})
        events = self.events(body)
        self.assertEqual([e["type"] for e in events], ["rejected"])
        self.assertEqual(events[0]["reason"], "empty")
        self.assertEqual(json.loads(self.get("/api/state")[1])["messages"], [])

    def test_an_unknown_address_is_a_404(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/api/nothing")
        self.assertEqual(caught.exception.code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
