"""QZone 的 Web 通道：签名、说说与评论的解析、回复某条评论、失败分类。

全部离线：HTTP 被换成一个假函数，样本是从真实回包里摘出来的结构（正文换成占位），
所以断言的是「这份文本会不会被读对」，而不是腾讯今天返回什么。
"""

import unittest

from channels import qzone_web as web
from channels.qzone_web import Web, WebError

POSTS = (
    '_preloadCallback({"code":0,"logininfo":{"name":"ふゆ"},"msglist":['
    '{"tid":"7a3636c664bbe06ae5550a00","content":"那句还是差一点。","created_time":1793112932,'
    '"cmtnum":2,"ugc_right":4},'
    '{"tid":"7a3636c69c2be36a067c0400","content":"<a href=\\"x\\">天黑得早</a>了。",'
    '"created_time":1793273682,"cmtnum":0}]})'
)

COMMENTS = (
    '_Callback({"code":0,"cmtnum":2,"commentlist":['
    '{"tid":2,"uin":"10002","name":"あさひ","content":"加油哦，明天再去。",'
    '"create_time":1793113000,"abledel":1},'
    '{"tid":3,"uin":"3325441658","name":"ふゆ","content":"嗯。","create_time":1793114000}]})'
)


class Client:
    """The two actions the Web channel needs, and a count of how often they were asked."""

    def __init__(self, cookie: str = "uin=o1; p_skey=abc; skey=x", uin: int = 555) -> None:
        self.cookie = cookie
        self.uin = uin
        self.calls: list[str] = []

    async def call(self, action, **params):
        self.calls.append(action)
        if action == "get_cookies":
            return {"cookies": self.cookie}
        if action == "get_login_info":
            return {"user_id": self.uin}
        return {}


class TestTheSignature(unittest.TestCase):
    def test_hash33_matches_the_web_client(self):
        self.assertEqual(web.hash33("abc"), 193485963)
        self.assertEqual(web.hash33(""), 5381)

    def test_g_tk_comes_from_p_skey(self):
        self.assertEqual(web.gtk_of("a=1; p_skey=abc; b=2"), web.hash33("abc"))
        self.assertEqual(web.gtk_of("skey=only"), 5381, "没有 p_skey 时用初值，不炸")


class TestWhatComesBack(unittest.TestCase):
    def test_her_posts_come_back_with_a_comment_count(self):
        rows = web.posts_of(POSTS)
        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["tid"], "7a3636c664bbe06ae5550a00")
        self.assertEqual(first["content"], "那句还是差一点。")
        self.assertEqual(first["cmtnum"], 2)
        self.assertEqual(first["at"], 1793112932)

    def test_markup_does_not_survive(self):
        rows = web.posts_of(POSTS)
        self.assertEqual(rows[1]["content"], "天黑得早了。")
        self.assertEqual(rows[1]["cmtnum"], 0, "没人回的说说，评论数是 0")

    def test_a_comment_keeps_its_own_id_and_author(self):
        rows = web.comments_of(COMMENTS)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["tid"], "2", "评论的 tid 才是回复时要用的那个")
        self.assertEqual(rows[0]["uin"], "10002")
        self.assertEqual(rows[0]["name"], "あさひ")
        self.assertEqual(rows[0]["content"], "加油哦，明天再去。")
        self.assertEqual(rows[0]["at"], 1793113000)

    def test_an_empty_answer_is_an_empty_list(self):
        self.assertEqual(web.posts_of(""), [])
        self.assertEqual(web.comments_of("{}"), [])
        self.assertEqual(web.posts_of('{"msglist":[{"content":"没有 tid"}]}'), [])


class TestTheAnswer(unittest.TestCase):
    def test_a_comment_that_worked_says_so(self):
        ok, _ = web.check_comment('{"code":0,"subcode":0,"commentId":"cid-1"}')
        self.assertTrue(ok)

    def test_a_refusal_keeps_the_reason(self):
        ok, detail = web.check_comment('{"code":-10052,"message":"该条内容已被删除"}')
        self.assertFalse(ok)
        self.assertIn("-10052", detail)

    def test_something_that_is_not_json_is_a_refusal(self):
        ok, detail = web.check_comment("<html>busy</html>")
        self.assertFalse(ok)
        self.assertIn("不是 JSON", detail)


class Base(unittest.IsolatedAsyncioTestCase):
    def make(self, **kwargs):
        self.client = Client()
        self.sent: list[tuple[str, str, bytes | None]] = []
        return Web(self.client, self_id="555", **kwargs)

    async def fetch(self, url, cookie, *, referer, data=None, method="GET"):
        self.sent.append((url, cookie, data))
        return POSTS

    async def test_the_cookie_is_reused(self):
        bot = self.make()
        bot._fetch = self.fetch  # type: ignore[method-assign]
        await bot.my_posts()
        await bot.my_posts()
        self.assertEqual(self.client.calls.count("get_cookies"), 1, "十分钟内不重复要 Cookie")

    async def test_a_stale_cookie_is_fetched_again_once(self):
        bot = self.make()
        answers = ['{"code":-10000}', POSTS]
        seen = {"n": 0}

        async def fetch(url, cookie, *, referer, data=None, method="GET"):
            seen["n"] += 1
            return answers[min(seen["n"] - 1, 1)]

        bot._fetch = fetch  # type: ignore[method-assign]
        rows = await bot.my_posts()
        self.assertEqual(len(rows), 2)
        self.assertEqual(self.client.calls.count("get_cookies"), 2, "失效就重新取一次")

    async def test_a_cookie_that_never_works_is_a_login_error(self):
        bot = self.make()

        async def fetch(url, cookie, *, referer, data=None, method="GET"):
            return '{"code":-10000}'

        bot._fetch = fetch  # type: ignore[method-assign]
        with self.assertRaises(WebError) as caught:
            await bot.my_posts()
        self.assertEqual(caught.exception.kind, "login")

    async def test_a_reply_targets_one_comment(self):
        bot = self.make()

        async def fetch(url, cookie, *, referer, data=None, method="GET"):
            self.sent.append((url, cookie, data))
            return '{"code":0}'

        bot._fetch = fetch  # type: ignore[method-assign]
        await bot.reply("tid-1", "3325441658", "c-9", "10002", "早点睡。", name="まの")
        url, _cookie, data = self.sent[-1]
        text = (data or b"").decode("utf-8")
        # 楼中楼四件套：h5 域名、paramstr=2、commentId/commentUin、正文前缀标记。
        self.assertTrue(url.startswith(web.REPLY_URL), "回复要打 h5 域名")
        self.assertIn("topicId=3325441658_tid-1__1", text)
        self.assertIn("paramstr=2", text)
        self.assertIn("commentId=c-9", text, "回复的是这条评论，不是整个说说")
        self.assertIn("commentUin=10002", text)
        self.assertIn(
            "content=%40%7Buin%3A10002%2Cnick%3A%E3%81%BE%E3%81%AE%2Cauto%3A1%7D", text,
            "正文前缀是 QZone 画「回复 某某」用的",
        )
        self.assertIn(f"g_tk={web.hash33('abc')}", text)

    async def test_a_plain_comment_names_nobody(self):
        bot = self.make()

        async def fetch(url, cookie, *, referer, data=None, method="GET"):
            self.sent.append((url, cookie, data))
            return '{"code":0}'

        bot._fetch = fetch  # type: ignore[method-assign]
        await bot.comment("tid-1", "3325441658", "嗯。")
        url, _cookie, data = self.sent[-1]
        text = (data or b"").decode("utf-8")
        self.assertTrue(url.startswith(web.COMMENT_URL))
        self.assertIn("topicId=3325441658_tid-1__1", text)
        self.assertIn("paramstr=1", text)
        self.assertNotIn("commentId", text)
        self.assertNotIn("@%7Buin", text, "新评论不该带任何人的标记")

    async def test_a_refused_reply_is_the_account_s_problem(self):
        bot = self.make()

        async def fetch(url, cookie, *, referer, data=None, method="GET"):
            return '{"code":-3000,"message":"操作太频繁"}'

        bot._fetch = fetch  # type: ignore[method-assign]
        with self.assertRaises(WebError) as caught:
            await bot.comment("tid-1", "3325441658", "早点睡。")
        self.assertEqual(caught.exception.kind, "limited")

    async def test_a_comment_cannot_be_taken_back(self):
        """删除评论的端点在真机上一律 500，所以通道不提供「撤回」。"""
        bot = self.make()
        self.assertFalse(hasattr(bot, "delete_comment"))


if __name__ == "__main__":
    unittest.main()


class TestNestedReplies(unittest.TestCase):
    """楼中楼：`list_3` 里的回复要能被读出来，并且认得它是谁的楼。"""

    BODY = (
        '_Callback({"code":0,"commentlist":['
        '{"tid":3,"uin":10002,"name":"まの","content":"嗓子还好吗？","create_time":1793274516,'
        '"replyNum":1,"list_3":['
        '{"tid":1,"uin":3325441658,"name":"ふゆ",'
        '"content":"@{uin:10002,nick:まの,who:1,auto:1} 还撑得住呢。","create_time":1793275655}]},'
        '{"tid":4,"uin":10009,"name":"别人","content":"路过。","create_time":1793274700}'
        "]})"
    )

    def test_a_reply_is_read_with_its_thread(self):
        rows = web.comments_of(self.BODY)
        self.assertEqual([one["tid"] for one in rows], ["3", "1", "4"])
        nested = rows[1]
        self.assertEqual(nested["root"], "3")
        self.assertEqual(nested["root_uin"], "10002")
        self.assertEqual(nested["uin"], "3325441658")
        self.assertEqual(nested["reply_to"], "10002", "标记指向谁，就是回复谁")

    def test_the_marker_is_not_part_of_what_she_said(self):
        rows = web.comments_of(self.BODY)
        self.assertEqual(rows[1]["content"], "还撑得住呢。")

    def test_the_marker_survives_a_nickname_with_punctuation(self):
        marker = web.reply_marker("10002", "a,b{c}:d")
        self.assertEqual(marker, "@{uin:10002,nick:a%2Cb%7Bc%7D%3Ad,auto:1}")
