"""原版网页要的接口：地址对不对、形状对不对、说错话时给的是不是人话。

这里测的是**翻译**，不是内核：内核那些行为在 `test_server.py` 和 `test_channels.py` 里。
所以模型用 `ScriptedLLM`，不花一分钱，也不会因为 key 过期就红。
"""

import http.client
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
from serve import App
from web_compat import character_json, make_compat_handler, message_json

REPLY = "<ja>おはよう</ja><zh>早上好</zh><ja>今日は早いね</ja><zh>今天真早啊</zh>"
SHEET = "# 人设：测试角色\n\n你是测试角色，说话简短。\n"


class TestTheShapes(unittest.TestCase):
    """两条消息、一个角色，翻译成原版前端认的样子。"""

    def test_a_user_message_keeps_its_words_and_has_no_japanese(self):
        got = message_json(
            {"id": "m1", "role": "user", "text": "こんばんは", "segments": [], "at": "2026-11-01T12:00:00+00:00"}
        )
        self.assertEqual(got["role"], "user")
        self.assertEqual(got["text"], "こんばんは")
        self.assertIsNone(got["ja"], "原版把日语原文和中文字幕分开存，用户说的没有这两样")
        self.assertIsNone(got["zh"])

    def test_a_reply_splits_into_japanese_and_chinese(self):
        got = message_json(
            {
                "id": "m2",
                "role": "assistant",
                "text": None,
                "segments": [["おはよう", "早上好"], ["今日は早いね", "今天真早啊"]],
                "at": "2026-11-01T12:00:10+00:00",
            }
        )
        self.assertEqual(got["role"], "character", "原版叫 character，不叫 assistant")
        self.assertIsNone(got["text"])
        self.assertEqual(got["ja"].split("\n"), ["おはよう", "今日は早いね"])
        self.assertEqual(got["zh"].split("\n"), ["早上好", "今天真早啊"])
        self.assertEqual(got["segments"][1], {"ja": "今日は早いね", "zh": "今天真早啊"})

    def test_a_character_without_pictures_says_null_not_a_broken_url(self):
        class Bare:
            id = "x"
            name = "テスト"
            intro = ""
            color = "#ffffff"
            name_zh = ""
            name_kana = ""
            name_romaji = ""
            unit = None
            unit_reading = None
            unit_order = None
            avatar = None
            scsp_avatar = None
            background = None
            call_background = None

        got = character_json(Bare())
        self.assertIsNone(got["avatar_url"])
        self.assertIsNone(got["background_url"])
        self.assertIsNone(got["stand"], "立绘、招呼、体素动画这台部署没有：null 前端就不画")
        self.assertIsNone(got["greeting"])


class TestTheServer(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "characters").mkdir()
        (root / "characters" / "characters.toml").write_text(
            '[[characters]]\nid = "test"\nname = "テスト"\ncolor = "#8ab4f8"\n', encoding="utf-8"
        )
        (root / "characters" / "sheets").mkdir()
        (root / "characters" / "sheets" / "test.md").write_text(SHEET, encoding="utf-8")
        settings = Settings(
            base_url="http://unused",
            api_key="unused",
            model="fake",
            db=str(root / "db.sqlite"),
            characters=str(root / "characters"),
            media=str(root / "media"),
            turns_per_hour=0,
        )
        self.app = App(settings, ScriptedLLM(REPLY, size=2))
        self.addCleanup(self.app.close)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_compat_handler(self.app))
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def get(self, path: str) -> tuple[int, object]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8") or "null")

    def post(self, path: str, body: dict) -> tuple[int, object]:
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read().decode("utf-8")
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8")
            return error.code, json.loads(raw) if raw else None

    # ---- 读

    def test_the_roster_comes_with_the_addresses_the_page_asks_for(self):
        status, rows = self.get("/api/characters")
        self.assertEqual(status, 200)
        self.assertEqual([row["id"] for row in rows], ["test"])
        self.assertIn("avatar_url", rows[0])

    def test_account_is_one_person_with_no_admin_console(self):
        status, account = self.get("/api/account")
        self.assertEqual(status, 200)
        self.assertFalse(account["is_admin"], "这台部署没有管理台；有它首页才会出现那个入口")
        self.assertIsNone(account["reach_outs"], "没有搭话，设置里就没有那一类")

    def test_service_status_is_always_ok(self):
        """没有健康检查的实现：报 ok，不假装在测。"""
        _status, body = self.get("/api/service-status")
        self.assertEqual(body["services"][0]["service"], "chat_llm")

    def test_unknown_addresses_are_404_not_a_crash(self):
        status, body = self.get("/api/nonsense")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    # ---- 写

    def test_profile_round_trip(self):
        request = urllib.request.Request(
            self.base + "/api/profile",
            data=json.dumps(
                {"form_of_address": "制作人", "gender": "male", "timezone": None, "birthday": {"month": 1, "day": 6}}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 200)
        _status, profile = self.get("/api/profile")
        self.assertEqual(profile["form_of_address"], "制作人")
        self.assertEqual(profile["birthday"], {"month": 1, "day": 6})

    # ---- 回合

    def conversation(self) -> str:
        status, made = self.post("/api/conversations", {"character_id": "test"})
        self.assertEqual(status, 201)
        return str(made["id"])

    def stream(self, conversation_id: str, text: str) -> list[dict]:
        request = urllib.request.Request(
            self.base + f"/api/conversations/{conversation_id}/turns",
            data=json.dumps({"text": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
        return [json.loads(line[6:]) for line in raw.split("\n\n") if line.startswith("data: ")]

    async def test_a_turn_streams_then_ends_with_the_recorded_reply(self):
        conversation_id = self.conversation()
        events = self.stream(conversation_id, "こんばんは")
        kinds = [event["type"] for event in events]
        self.assertEqual(kinds[0], "user", "原版靠这个事件确认后端收下了那句话")
        self.assertIn("delta", kinds)
        self.assertEqual(kinds[-1], "done")
        done = events[-1]
        self.assertEqual(done["message"]["role"], "character")
        self.assertIn("おはよう", done["message"]["ja"])
        self.assertIn(done["affinity"]["tier"], ("cold", "distant", "trust", "close", "intimate"))

    async def test_the_body_may_arrive_chunked(self):
        """Next 是流着转发请求体的：没有 Content-Length，只有 chunked。

        只按长度读的话一个字也读不到，用户那句话就变成「空的」——原版前端第一次接上
        就是这么错的，输入框上面顶着「empty 这次没有计入限额」。
        """
        conversation_id = self.conversation()
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=20)
        payload = json.dumps({"text": "こんばんは"}).encode("utf-8")
        connection.putrequest("POST", f"/api/conversations/{conversation_id}/turns")
        connection.putheader("Transfer-Encoding", "chunked")
        connection.putheader("Content-Type", "application/json")
        connection.endheaders()
        connection.send(b"%x\r\n" % len(payload) + payload + b"\r\n0\r\n\r\n")
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertIn('"type": "done"', raw)
        self.assertIn("おはよう", raw)

    async def test_a_refusal_is_written_in_words(self):
        """内核拒绝时给的是原因码（`empty`），原版前端直接把它显示给人看。"""
        conversation_id = self.conversation()
        events = self.stream(conversation_id, "")
        last = events[-1]
        self.assertEqual(last["type"], "error")
        self.assertEqual(last["message"], "没有收到要说的话。")


if __name__ == "__main__":
    unittest.main()


class TestTheDataPackage(unittest.IsolatedAsyncioTestCase):
    """原版的「数据包」页是点一下下载一个 zip：里面得有它读得懂的 manifest。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "characters").mkdir()
        (root / "characters" / "characters.toml").write_text(
            '[[characters]]\nid = "test"\nname = "テスト"\ncolor = "#8ab4f8"\n', encoding="utf-8"
        )
        (root / "characters" / "sheets").mkdir()
        (root / "characters" / "sheets" / "test.md").write_text(SHEET, encoding="utf-8")
        settings = Settings(
            base_url="http://unused", api_key="unused", model="fake",
            db=str(root / "db.sqlite"), characters=str(root / "characters"),
            media=str(root / "media"), turns_per_hour=0,
        )
        self.app = App(settings, ScriptedLLM(REPLY, size=2))
        self.addCleanup(self.app.close)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_compat_handler(self.app))
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    async def test_it_is_a_zip_with_a_manifest_and_the_data(self):
        import io
        import zipfile

        with urllib.request.urlopen(self.base + "/api/account/data-package?include=conversations,memories", timeout=15) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Content-Type"], "application/zip")
            raw = response.read()
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            self.assertEqual(sorted(names), ["data.json", "manifest.json"])
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            self.assertEqual(manifest["categories"], ["conversations", "memories", "profile"])
            self.assertIn("conversations", json.loads(archive.read("data.json").decode("utf-8")))

    async def test_an_own_key_check_says_why_it_cannot(self):
        """自带密钥那页会试连通性；这台部署没有自己的额度可走，得给一句人话。"""
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(self.base + "/api/account/keys/llm/check", timeout=10)
        self.assertEqual(caught.exception.code, 400)


class TestThePluginsPage(unittest.IsolatedAsyncioTestCase):
    """设置「插件 → QQ 聊天插件」：网页只认 id 和 on/off，人话在文案表里。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "characters").mkdir()
        (root / "characters" / "characters.toml").write_text(
            '[[characters]]\nid = "test"\nname = "テスト"\ncolor = "#8ab4f8"\n', encoding="utf-8"
        )
        (root / "characters" / "sheets").mkdir()
        (root / "characters" / "sheets" / "test.md").write_text(SHEET, encoding="utf-8")
        settings = Settings(
            base_url="http://unused", api_key="unused", model="fake",
            db=str(root / "db.sqlite"), characters=str(root / "characters"),
            media=str(root / "media"), turns_per_hour=0,
        )
        self.app = App(settings, ScriptedLLM(REPLY, size=2))
        self.addCleanup(self.app.close)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_compat_handler(self.app))
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def call(self, path: str, body: dict | None = None) -> tuple[int, object]:
        request = urllib.request.Request(
            self.base + path,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"} if body is not None else {},
            method="GET" if body is None else "POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read().decode("utf-8")
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8")
            return error.code, json.loads(raw) if raw else None

    async def test_the_page_gets_one_plugin_from_our_own_table(self):
        status, body = self.call("/api/settings/plugins")
        self.assertEqual(status, 200)
        self.assertEqual([one["id"] for one in body["plugins"]], ["qq"])
        plugin = body["plugins"][0]
        if plugin["available"]:
            # 这张表在 `channels/qq.py` 里：网页按它画行，通道按它铺配置，两边同一份。
            from channels.qq import SWITCHES

            self.assertEqual([one["id"] for one in plugin["switches"]], [name for name, _ in SWITCHES])

    async def test_a_switch_named_wrong_is_refused(self):
        status, body = self.call("/api/settings/plugins")
        if not body["plugins"][0]["available"]:
            self.skipTest("这个 checkout 没有 QQ 通道")
        code, answer = self.call("/api/settings/plugins/qq", {"switches": {"nonsense": True}})
        self.assertEqual(code, 422)
        self.assertIn("nonsense", answer["error"])

    async def test_flipping_one_is_answered_with_the_whole_plugin(self):
        status, body = self.call("/api/settings/plugins")
        if not body["plugins"][0]["available"]:
            self.skipTest("这个 checkout 没有 QQ 通道")
        first = body["plugins"][0]["switches"][0]["id"]
        code, saved = self.call("/api/settings/plugins/qq", {"switches": {first: False}})
        self.assertEqual(code, 200)
        self.assertFalse(next(one for one in saved["switches"] if one["id"] == first)["on"])
        self.assertEqual(saved["id"], "qq")


class TestTheWorldPage(unittest.IsolatedAsyncioTestCase):
    """「世界观」：作品在上层，人挂在它下面 —— 不是一张把所有人堆在一起的表。

    这台部署现在只有一个作品，所以看不出区别；但接口的形状决定了以后：加了别的作品的
    人，会落在自己那一组，不会和这边的二十八个混在一起。
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "characters" / "sheets").mkdir(parents=True)
        (root / "characters" / "characters.toml").write_text(
            '[[characters]]\nid = "test"\nname = "テスト"\ncolor = "#8ab4f8"\n', encoding="utf-8"
        )
        (root / "characters" / "sheets" / "test.md").write_text(SHEET, encoding="utf-8")
        (root / "characters" / "work.toml").write_text(
            '[work]\nid = "one"\ntitle = "作品一"\n', encoding="utf-8"
        )
        settings = Settings(
            base_url="http://unused", api_key="unused", model="fake",
            db=str(root / "db.sqlite"), characters=str(root / "characters"),
            media=str(root / "media"), turns_per_hour=0,
        )
        self.app = App(settings, ScriptedLLM(REPLY, size=2))
        self.addCleanup(self.app.close)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_compat_handler(self.app))
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def call(self, path: str) -> tuple[int, object]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8") or "null")

    async def test_people_hang_under_their_work(self):
        status, body = self.call("/api/settings/world")
        self.assertEqual(status, 200)
        self.assertEqual([one["id"] for one in body["works"]], ["one"])
        group = body["works"][0]
        self.assertEqual(group["title"], "作品一")
        self.assertEqual([one["id"] for one in group["characters"]], ["test"])

    async def test_each_person_says_what_she_has_and_what_she_is_missing(self):
        status, body = self.call("/api/settings/world")
        self.assertEqual(status, 200)
        one = body["works"][0]["characters"][0]
        # 人设走的是 `sheets/test.md`，所以「人设」在 present 里、不在 missing 里。
        self.assertIn("persona", one["present"])
        self.assertIn("qq", one["missing"])
        self.assertIn("facts", one["optional"])
        self.assertEqual(one["home"], "")
