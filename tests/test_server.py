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


class TestTheWorldIsData(unittest.TestCase):
    """页面要知道「这份人设是从哪读的」：作品是共享的，包是每人一份的。"""

    def test_state_carries_the_work_and_the_pack(self):
        tmp = tempfile.TemporaryDirectory()
        # 注册顺序是 LIFO：先注册目录清理、再注册 app.close，关库才会发生在删目录之前。
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "characters" / "sheets").mkdir(parents=True)
        (root / "characters" / "characters.toml").write_text(
            '[[characters]]\nid = "test"\nname = "テスト"\ncolor = "#8ab4f8"\n',
            encoding="utf-8",
        )
        (root / "characters" / "sheets" / "test.md").write_text("# 人设\n", encoding="utf-8")
        (root / "characters" / "work.toml").write_text(
            '[work]\ntitle_zh = "作品"\n[aliases]\nwords = ["生放送"]\n', encoding="utf-8"
        )
        settings = Settings(
            base_url="http://unused", api_key="unused", model="fake",
            db=str(root / "db.sqlite"), characters=str(root / "characters"),
            media=str(root / "media"), turns_per_hour=0,
        )
        app = App(settings, ScriptedLLM("{}"))
        self.addCleanup(app.close)
        state = app.state()
        self.assertTrue(state["work"]["named"])
        self.assertEqual(state["work"]["title_zh"], "作品")
        self.assertIn("pack", state["characters"][0])
        self.assertIn("missing", state["characters"][0]["pack"])


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

    def test_no_voice_model_means_no_phone_key(self):
        """没配语音合成就没有通话，电话键不该画。

        按了只会说「这一版还没有开放」的键看起来就是坏的；页面和她的嘴都拿
        `capabilities.call` 这一个答案，避免「页面说没有、她却在约电话」。
        """
        call = json.loads(self.get("/api/state")[1])["capabilities"]["call"]
        self.assertFalse(call["enabled"])
        self.assertIn("语音", call["why"])

    def test_a_voice_model_brings_the_phone_key_back(self):
        """配上语音以后不用改代码——`auto` 跟着语音插件走。"""
        from dataclasses import replace
        from plugins.voice import Voice

        self.app.settings = replace(self.app.settings, call="auto", fish_key="test-key")
        self.app.voice = Voice("test-key")
        call = json.loads(self.get("/api/state")[1])["capabilities"]["call"]
        self.assertTrue(call["enabled"])
        self.assertEqual(call["why"], "")

    def test_the_manual_switch_still_wins_over_a_voice_model(self):
        from dataclasses import replace
        from plugins.voice import Voice

        self.app.settings = replace(self.app.settings, call="off", fish_key="test-key")
        self.app.voice = Voice("test-key")
        call = json.loads(self.get("/api/state")[1])["capabilities"]["call"]
        self.assertFalse(call["enabled"], "SCCORE_CALL=off 是手动的闸")
        self.assertIn("off", call["why"])

    def test_the_three_values_and_a_typo(self):
        from core.settings import Settings

        base = {"CHAT_LLM_BASE_URL": "u", "CHAT_LLM_API_KEY": "k", "CHAT_LLM_MODEL": "m"}

        def read(**extra):
            return Settings.from_env({**base, **extra}, require_credentials=False)

        self.assertEqual(read().call, "auto", "默认跟着语音走")
        for value in ("off", "on", "auto"):
            self.assertEqual(read(SCCORE_CALL=value).call, value)
        with self.assertRaises(RuntimeError):
            read(SCCORE_CALL="yes")

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


class TestTheChatWallpaper(unittest.TestCase):
    """`background` 这个字段以前是死的：表里写着、代码不读、`backgrounds/` 从没建过。

    现在是「设置 → 外观 → SCSP 聊天背景」那张图：按角色取，和头像同一条路。
    """

    def build(self, *, with_file: bool) -> "TestTheChatWallpaper":
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "characters").mkdir()
        (root / "characters" / "test.md").write_text(SHEET, encoding="utf-8")
        (root / "characters" / "characters.toml").write_text(
            '[[characters]]\nid = "test"\nname = "测试角色"\n'
            'background = "backgrounds/test.jpg"\n',
            encoding="utf-8",
        )
        if with_file:
            (root / "characters" / "backgrounds").mkdir()
            (root / "characters" / "backgrounds" / "test.jpg").write_bytes(b"jpeg-bytes")
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
        return self

    def test_the_state_points_the_page_at_it(self):
        self.build(with_file=True)
        state = self.app.state()
        self.assertEqual(state["character"]["background_url"], "/media/background/test")
        self.assertTrue(state["characters"][0]["background"])

    def test_the_list_entry_carries_the_urls_too(self):
        """对话页是从**列表项**里取出这一位来画的（`contentArea()`），所以列表项也得有 url。

        少了这几个键，对话框里没有头像、壁纸也铺不上——而 `character` 那个单数对象一直是好的，
        于是只补它看起来「测试都过」，页面却是空的。
        """
        self.build(with_file=True)
        entry = self.app.state()["characters"][0]
        self.assertIn("avatar_url", entry, "键要在，值可以是空（这个夹具没有头像）")
        self.assertEqual(entry["background_url"], "/media/background/test")


    def test_a_character_without_one_says_so(self):
        self.build(with_file=False)
        state = self.app.state()
        self.assertEqual(state["character"]["background_url"], "")
        self.assertFalse(state["characters"][0]["background"])

    def test_the_file_is_served_over_its_own_route(self):
        self.build(with_file=True)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app, b"<html></html>"))
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_address[1]}/media/background/test"
        with urllib.request.urlopen(url, timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Content-Type"], "image/jpeg")
            self.assertEqual(response.read(), b"jpeg-bytes")

    def test_no_file_means_404_not_a_crash(self):
        self.build(with_file=False)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app, b"<html></html>"))
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_address[1]}/media/background/test"
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(url, timeout=10)
        self.assertEqual(caught.exception.code, 404)


class TestThePageCanLiveSomewhereElse(unittest.TestCase):
    """两份前端合成一份以后，旧地址只当门房：把人和他正在看的那一页原样送过去。

    配 `SCCORE_WEB_REDIRECT` 才有这一层；不配就还是老样子，自己画——一个 checkout 不该
    因为别人合并了前端就打不开自己的页面。
    """

    def setUp(self) -> None:
        import os
        from unittest import mock

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "characters").mkdir()
        (root / "characters" / "test.md").write_text(SHEET, encoding="utf-8")
        settings = Settings(
            base_url="http://unused", api_key="unused", model="fake",
            db=str(root / "kernel.db"), characters=str(root / "characters"), turns_per_hour=0,
        )
        self.app = App(settings, ScriptedLLM(REPLY, size=4))
        self.addCleanup(self.app.close)
        self.redirect = mock.patch.dict(os.environ, {"SCCORE_WEB_REDIRECT": "http://web.example:3000"})
        self.redirect.start()
        self.addCleanup(self.redirect.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app, PAGE.encode()))
        self.addCleanup(self.server.server_close)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def ask(self, path: str) -> tuple[int, str]:
        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", path)
        response = connection.getresponse()
        location = response.getheader("Location") or ""
        response.read()
        connection.close()
        return response.status, location

    def test_a_page_route_goes_to_the_one_frontend_with_its_path(self):
        for path in ("/conversations", "/settings/world", "/conversation/fuyuko"):
            status, location = self.ask(path)
            self.assertEqual(status, 301, path)
            self.assertEqual(location, f"http://web.example:3000{path}", path)

    def test_the_api_still_answers_here(self):
        """门房只转页面：接口还得在这边答，否则健康检查和脚本全断。"""
        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", "/api/roster")
        response = connection.getresponse()
        body = response.read()
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertIn(b"roster", body)
