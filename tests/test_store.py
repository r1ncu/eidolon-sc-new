"""The three tables: what is kept, and what a failed turn leaves behind (nothing)."""

import tempfile
import unittest
from pathlib import Path

from core.protocol import Segment
from core.store import Store


class TestStore(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "kernel.db")
        self.addCleanup(self.store.close)
        self.user = self.store.create_user("制作人")
        self.conversation = self.store.create_conversation(self.user, "mano")

    def test_a_conversation_is_found_again(self):
        found = self.store.conversation(self.conversation.id)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual((found.user_id, found.character_id), (self.user, "mano"))

    def test_a_missing_conversation_is_none(self):
        self.assertIsNone(self.store.conversation("nope"))

    def test_a_turn_is_two_messages(self):
        reply = self.store.add_turn(
            self.conversation.id, "おはよう", [Segment("おはよう", "早上好")]
        )
        messages = self.store.messages(self.conversation.id)
        self.assertEqual([m.role for m in messages], ["user", "assistant"])
        self.assertEqual(messages[0].text, "おはよう")
        self.assertEqual(messages[1].id, reply)
        self.assertEqual(messages[1].ja, "おはよう")
        self.assertEqual(messages[1].zh, "早上好")

    def test_a_reply_with_two_segments_keeps_both(self):
        self.store.add_turn(
            self.conversation.id,
            "在吗",
            [Segment("あのね", "那个"), Segment("今日は早いね", "今天真早啊")],
        )
        reply = self.store.messages(self.conversation.id)[1]
        self.assertEqual(len(reply.segments), 2)
        self.assertEqual(reply.ja, "あのね今日は早いね")

    def test_a_photo_tag_is_kept_with_the_reply(self):
        reply = self.store.add_turn(
            self.conversation.id, "发张照片", [Segment("はい", "好")], photo_tag="ラーメン屋の前"
        )
        found = self.store.message(reply)
        assert found is not None
        self.assertEqual(found.photo_tag, "ラーメン屋の前")
        self.assertFalse(found.photo_failed)

    def test_conversations_do_not_share_messages(self):
        other = self.store.create_conversation(self.user, "meguru")
        self.store.add_turn(self.conversation.id, "在吗", [Segment("うん", "嗯")])
        self.assertEqual(len(self.store.messages(other.id)), 0)

    def test_messages_survive_reopening_the_file(self):
        path = Path(self.tmp.name) / "kernel.db"
        self.store.add_turn(self.conversation.id, "在吗", [Segment("うん", "嗯")])
        self.store.close()
        with Store(path) as reopened:
            self.assertEqual(len(reopened.messages(self.conversation.id)), 2)


class TestCuesAreNotShown(unittest.TestCase):
    """语气提示：存的是模型写的原样，读出来的是人们看见的（没有提示）。
    原版 ADR-0007：只有语音合成听得到提示。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "kernel.db")
        self.addCleanup(self.store.close)
        self.user = self.store.create_user("制作人")
        self.conversation = self.store.create_conversation(self.user, "mano")

    def test_a_cue_is_stripped_on_the_way_out(self):
        reply = self.store.add_turn(
            self.conversation.id,
            "你好",
            [Segment("あはは [laughing]  Supervision", "呵呵 [laughing] 你在说什么呢")],
        )
        message = self.store.message(reply)
        self.assertNotIn("[laughing]", message.segments[0].ja)
        self.assertNotIn("[laughing]", message.segments[0].zh)
        self.assertIn(" Supervision", message.segments[0].ja)

    def test_the_stored_copy_keeps_the_cue(self):
        """存下来的还是模型写的原样（ADR-0007）：只有读出来的时候才剥掉。"""
        self.store.add_turn(
            self.conversation.id,
            "你好",
            [Segment("あはは [laughing] Supervision", "呵呵 [laughing] 你好")],
        )
        row = self.store.query(
            "SELECT segments FROM messages WHERE role = 'assistant' ORDER BY seq DESC LIMIT 1"
        )
        self.assertIn("[laughing]", str(row[0]["segments"]))


class TestEmptyStore(unittest.TestCase):
    def test_opening_twice_is_harmless(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "k.db"
            Store(path).close()
            with Store(path) as store:
                self.assertEqual(store.messages("nothing"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
