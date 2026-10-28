"""The scenes a sentence calls up: matched by topic, capped, and never fatal."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from core.messages import Conversation
from core.plugin import TurnContext
from plugins.stories import (
    MAX_CHARS,
    MAX_SCENES,
    Stories,
    _auto,
    _card_name,
    _clean,
    _cut,
    _parse_topics,
    _scan,
    _title_words,
)

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)

CORPUS = {
    "01-培育剧本/Morning.md": """\
# Morning

黛冬優子 · 培育剧本 ｜ 15 话 ｜ 共 120 条对话

---

## Morning①

*原始剧情 `morning_001.json` ｜ 译者 someone ｜ 8 条对话*

**プロデューサー**：おっと、何か落ちて──
これ、冬優子のスマホのストラップか？

**冬優子**：あっ、ありがと……
> 啊，谢谢……

---

## Morning③

*原始剧情 `morning_003.json` ｜ 译者 someone ｜ 8 条对话*

**冬優子**：スコーン、焼いたんだけど
食べる？
> 烤了司康饼，要吃吗？

**プロデューサー**：……甘くない？

**冬優子**：ふゆのスコーンは甘いに決まってるでしょ
> 冬优的司康饼当然是甜的啊
""",
    "03-S卡支援事件/【とあるふゆのひ】黛冬優子.md": """\
# 【とあるふゆのひ】黛冬優子

黛冬優子 · S卡支援事件（S_SSR） ｜ 实装日 2025-12-04 ｜ 1 话 ｜ 共 34 条对话

---

## 今日が今日であるうちに

*原始剧情 `produce_events/301802001.json` ｜ 暂无汉化（仅日文原文） ｜ 34 条对话*

**プロデューサー**：『今日は冬優子の誕生日だろ？』

**冬優子**：……覚えてたのね

**冬優子**：……言われなくたって、楽しみにしてるわよ
""",
}

TABLE = """\
# 话题词 → 出处

[[topic]]
name = "生日"
words = ["生日", "誕生日", "12月4日"]
from = [
  { file = "03-S卡支援事件/【とあるふゆのひ】黛冬優子.md" },
]

[[topic]]
name = "甜点"
words = ["司康", "スコーン", "蛋糕"]
from = [
  { file = "01-培育剧本/Morning.md", scene = "Morning③" },
  { file = "01-培育剧本/Morning.md", scene = "这一话不存在" },
]
"""


def conversation() -> Conversation:
    return Conversation(
        id="c1", user_id="u1", character_id="fuyuko", created_at="2026-10-25T12:00:00+00:00"
    )


def turn(text: str, facts: dict | None = None) -> TurnContext:
    return TurnContext(conversation=conversation(), text=text, now=NOW, facts=dict(facts or {}))


class StoriesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        for name, text in CORPUS.items():
            path = self.root / "facts" / "fuyuko-stories" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (self.root / "world").mkdir()
        (self.root / "world" / "fuyuko-stories.toml").write_text(TABLE, encoding="utf-8")
        self.stories = Stories(self.root, on=True)

    # ---- the match itself

    def test_topic_word_pulls_its_scene(self) -> None:
        scenes = self.stories._match("fuyuko", "生日想要什么礼物好")
        self.assertEqual([scene.title for scene in scenes], ["今日が今日であるうちに"])
        self.assertIn("楽しみにしてるわよ", scenes[0].text)

    def test_scene_fragment_that_matches_nothing_is_dropped(self) -> None:
        # 甜点 lists a scene that does not exist: the one that does still arrives.
        scenes = self.stories._match("fuyuko", "司康饼好吃吗")
        self.assertEqual([scene.title for scene in scenes], ["Morning③"])

    def test_card_name_matches_without_a_topic(self) -> None:
        scenes = self.stories._match("fuyuko", "とあるふゆのひ 里发生了什么")
        self.assertEqual([scene.title for scene in scenes], ["今日が今日であるうちに"])

    def test_scene_title_matches_without_a_topic(self) -> None:
        scenes = self.stories._match("fuyuko", "「今日が今日であるうちに」是什么活动")
        self.assertEqual([scene.title for scene in scenes], ["今日が今日であるうちに"])

    def test_a_sentence_with_no_topic_carries_nothing(self) -> None:
        self.assertEqual(self.stories._match("fuyuko", "今天天气真好"), [])
        self.assertEqual(self.stories._match("fuyuko", ""), [])

    def test_character_without_a_corpus_carries_nothing(self) -> None:
        self.assertEqual(self.stories._match("mano", "生日"), [])

    def test_scenes_are_capped(self) -> None:
        # 生日（1 个出处）之后还能接两个自动命中，但总数不超过 MAX_SCENES。
        found = self.stories._match("fuyuko", "生日 Morning① Morning③ とあるふゆのひ")
        self.assertLessEqual(len(found), MAX_SCENES)

    def test_a_scene_arrives_once(self) -> None:
        scenes = self.stories._match("fuyuko", "生日 誕生日 12月4日 とあるふゆのひ")
        self.assertEqual(len(scenes), 1)

    # ---- the section

    def test_section_carries_the_note_and_the_scene(self) -> None:
        sections = self.sections("生日", facts={})
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].slot.value, "turn")
        self.assertEqual(sections[0].order, 25)
        text = sections[0].text
        self.assertIn("想起来的剧情", text)
        self.assertIn("当作自己想起来的那样", text)
        self.assertIn("とあるふゆのひ", text)
        self.assertIn("誕生日だろ", text)

    def test_judge_no_means_no_scenes(self) -> None:
        self.assertEqual(self.sections("生日", facts={"stories.needed": False}), ())

    def test_a_failed_judge_means_look_anyway(self) -> None:
        # 判断模型没答上来：宁可多带一段，不要漏掉她要用的东西。
        self.assertEqual(len(self.sections("生日", facts={})), 1)

    def test_nothing_to_say_means_no_section(self) -> None:
        self.assertEqual(self.sections("你好呀", facts={}), ())

    def test_off_means_off(self) -> None:
        quiet = Stories(self.root, on=False)
        self.assertEqual(quiet.judge_questions(), ())
        self.assertEqual(quiet.capabilities(), {"on": False})
        self.assertEqual(quiet._match("fuyuko", "生日"), [])

    def test_the_judge_question_is_asked_when_on(self) -> None:
        questions = self.stories.judge_questions()
        self.assertEqual([q.key for q in questions], ["stories.needed"])

    def test_the_section_stays_within_the_budget(self) -> None:
        text = self.sections("生日", facts={})[0].text
        self.assertLessEqual(len(text), MAX_CHARS + 400)

    # ---- edits on disk

    def test_an_edited_table_is_picked_up(self) -> None:
        self.assertEqual(self.stories._match("fuyuko", "下雨"), [])
        path = self.root / "world" / "fuyuko-stories.toml"
        path.write_text(
            path.read_text(encoding="utf-8")
            + '\n[[topic]]\nname = "下雨"\nwords = ["下雨"]\n'
            'from = [{ file = "01-培育剧本/Morning.md" }]\n',
            encoding="utf-8",
        )
        # Live 有 settle 窗口，强制刷一次就好，和 world.py 的测试一个路子。
        self.stories._maps["fuyuko"].refresh(force=True)
        found = self.stories._match("fuyuko", "外面下雨了")
        self.assertTrue(found)

    def test_an_unreadable_table_is_not_fatal(self) -> None:
        (self.root / "world" / "fuyuko-stories.toml").write_text("not = [toml", encoding="utf-8")
        self.assertEqual(self.stories._topics("fuyuko"), ())
        # 没有表，卡名照样能命中。
        self.assertTrue(self.stories._match("fuyuko", "とあるふゆのひ"))

    def test_a_changed_scene_file_is_picked_up(self) -> None:
        path = self.root / "facts" / "fuyuko-stories" / "03-S卡支援事件" / "【とあるふゆのひ】黛冬優子.md"
        text = path.read_text(encoding="utf-8").replace("楽しみにしてるわよ", "楽しみにしてるわよっ")
        path.write_text(text, encoding="utf-8")
        found = self.stories._match("fuyuko", "生日")
        self.assertIn("わよっ", found[0].text)

    # ---- the route

    def test_the_route_explains_itself(self) -> None:
        import asyncio

        handler = self.stories.routes()[("GET", "/api/stories")]
        answer = asyncio.run(handler({"c": "fuyuko", "q": "生日"}, {}))
        self.assertTrue(answer["on"])
        self.assertEqual(answer["files"], 2)
        self.assertIn("生日", answer["topics"])
        self.assertEqual(answer["matched"], ["【とあるふゆのひ / 今日が今日であるうちに】"])
        self.assertIn("想起来的剧情", answer["section"])
        self.assertEqual(asyncio.run(handler({}, {}))["error"], "需要 c（角色）")

    def sections(self, text: str, *, facts: dict) -> tuple:
        import asyncio

        return asyncio.run(self.stories.prompt_sections(turn(text, facts)))


class ReadingTest(unittest.TestCase):
    """The helpers that turn a corpus file into a scene."""

    def test_card_name_comes_from_the_brackets(self) -> None:
        self.assertEqual(_card_name("# 【オ♡フ♡レ♡コ】黛 冬優子"), "オ♡フ♡レ♡コ")
        self.assertEqual(_card_name("# Morning"), "Morning")
        self.assertEqual(_card_name("# Landing Point篇"), "Landing Point篇")

    def test_the_scaffolding_does_not_go_in(self) -> None:
        body = "*原始剧情 `x.json` ｜ 译者 y ｜ 8 条对话*\n\n**冬優子**：お疲れ様\n> 辛苦了\n---\n"
        self.assertEqual(_clean(body), "**冬優子**：お疲れ様\n> 辛苦了")

    def test_a_scene_is_cut_at_a_line(self) -> None:
        text = "\n".join(f"第{i}行台词" for i in range(1, 40))
        cut = _cut(text, 40)
        self.assertTrue(cut.endswith("……"))
        self.assertLessEqual(len(cut), 60)
        for line in cut.splitlines():
            if line != "……":
                self.assertIn(line, text)

    def test_title_words_split_at_the_slash(self) -> None:
        words = _title_words("2022情人节 / 【黛 冬優子】2022 バレンタイン準備")
        self.assertIn("情人节", words)  # 年份去掉，话说一半也找得到
        self.assertIn("2022情人节", words)
        self.assertIn("バレンタイン準備", words)

    def test_title_words_skip_noise(self) -> None:
        self.assertEqual(_title_words("produce_events_101800002"), set())
        self.assertEqual(_title_words("truth"), set())  # 纯英文短词：中文群里是噪音
        self.assertEqual(_title_words("選"), set())  # 单字：会命中一切

    def test_the_table_parses_or_gives_nothing(self) -> None:
        topics = _parse_topics(TABLE)
        self.assertEqual([topic.name for topic in topics], ["生日", "甜点"])
        self.assertEqual(topics[1].where[0], ("01-培育剧本/Morning.md", "Morning③"))
        self.assertEqual(_parse_topics("not = [toml"), ())
        self.assertEqual(_parse_topics(""), ())

    def test_the_corpus_scans_in_file_order(self) -> None:
        scenes = _scan(self.root_of(CORPUS))
        self.assertEqual([scene.file for scene in scenes][:1], ["01-培育剧本/Morning.md"])
        self.assertEqual(scenes[0].card, "Morning")
        self.assertEqual(scenes[-1].card, "とあるふゆのひ")
        self.assertNotIn("原始剧情", scenes[0].text)

    def test_the_readme_is_not_a_scene(self) -> None:
        corpus = dict(CORPUS)
        corpus["README.md"] = "# 剧情文档\n\n## 说明\n\n代码不读这个目录——这是给人看的。\n"
        scenes = _scan(self.root_of(corpus))
        self.assertEqual({scene.file for scene in scenes}, set(CORPUS))
        self.assertEqual(len(scenes), 3)  # Morning 两话 + とあるふゆのひ 一话

    def test_auto_keywords_carry_the_card_and_the_title(self) -> None:
        scenes = _scan(self.root_of(CORPUS))
        auto = dict(zip((s.key for s in scenes), (w for _, w in _auto(scenes))))
        # 卡名（带日文的）和话名都算；「Morning」这种纯英文名不算——中文群里是噪音。
        self.assertIn("とあるふゆのひ", auto[("03-S卡支援事件/【とあるふゆのひ】黛冬優子.md", "今日が今日であるうちに")])
        self.assertIn("Morning③", auto[("01-培育剧本/Morning.md", "Morning③")])
        self.assertNotIn("Morning", auto[("01-培育剧本/Morning.md", "Morning③")])
        # 文件名里的黛冬優子不算关键词：满屏都会命中。
        self.assertNotIn("黛冬優子", auto[("01-培育剧本/Morning.md", "Morning③")])

    @staticmethod
    def root_of(corpus: dict) -> Path:
        root = Path(tempfile.mkdtemp())
        for name, text in corpus.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return root


if __name__ == "__main__":
    unittest.main()
