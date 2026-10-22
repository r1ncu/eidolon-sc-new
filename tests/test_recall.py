"""The two shapes of her facts: pasted whole, or looked up by meaning."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from core.messages import Conversation
from core.plugin import TurnContext
from core.store import Store
from plugins.recall import EmbeddingError, OpenAIEmbeddings, Recall, read_passages

NOW = datetime(2026, 10, 23, 4, 0, tzinfo=timezone.utc)

FACTS = """\
# 櫻木真乃

## 好きなもの
真乃很喜欢鸟，家里养着一只叫ピーちゃん的鸽子，休息日会去公园看鸽子。

## 最近のこと
下个月有一次考试，她有点不安，正在和制作人一起复习。
"""

BIRDS = "鸽子"
EXAM = "考试"


def vector(text: str, size: int = 512) -> list[float]:
    """A bag of characters: what a real embedder does, only worse. Two texts about the
    same thing share characters, so the cosine ranks them together."""
    counts = [0.0] * size
    for character in text:
        if character.strip():
            counts[ord(character) % size] += 1.0
    return counts


class FakeEmbeddings:
    """Stands in for the service: records what it was asked to embed, and can fail."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("the embedding service is down")
        return [vector(text) for text in texts]

    def documents(self, since: int = 0) -> list[str]:
        """Every text embedded from call `since` on, except the last, which is the query."""
        out: list[str] = []
        for call in self.calls[since:-1]:
            out.extend(call)
        return out


def conversation() -> Conversation:
    return Conversation(
        id="c1", user_id="u1", character_id="mano", created_at="2026-10-23T04:00:00+00:00"
    )


def turn(facts: dict | None = None, text: str = "你喜欢什么鸟？") -> TurnContext:
    return TurnContext(conversation=conversation(), text=text, now=NOW, facts=dict(facts or {}))


class RecallTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        (self.root / "facts").mkdir()
        (self.root / "facts" / "mano.md").write_text(FACTS, encoding="utf-8")
        self.store = Store(self.root / "facts.db")
        self.embedder = FakeEmbeddings()
        # The fake embedder is a bag of characters: a short question against a long
        # passage scores lower than a real embedding would, so the tests that expect a
        # hit ask for a lower bar rather than borrowing the production one.
        self.recall = self.make("vector", threshold=0.2)

    def tearDown(self) -> None:
        self.store.close()

    def make(self, mode: str, **options) -> Recall:
        recall = Recall(
            self.store, self.root, mode=mode, embedder=self.embedder,
            model="fake-embedding", **options,
        )
        self.store.migrate(recall.migrations())
        return recall

    # ---- the data version

    def test_whole_mode_puts_the_file_beside_the_sheet(self):
        whole = self.make("whole")
        self.assertIn(BIRDS, whole.persona("mano"))
        self.assertEqual(self.make("off").persona("mano"), "")
        self.assertEqual(self.make("vector").persona("mano"), "")

    def test_without_a_facts_file_the_whole_version_adds_nothing(self):
        self.assertEqual(self.make("whole").persona("hiori"), "")

    # ---- the vector version

    def test_the_turn_gets_the_passage_about_what_was_asked(self):
        import asyncio

        sections = asyncio.run(self.recall.prompt_sections(turn()))
        self.assertEqual(len(sections), 1)
        self.assertIn(BIRDS, sections[0].text)
        self.assertNotIn(EXAM, sections[0].text)
        self.assertEqual(sections[0].title, "recall")

    def test_a_passage_below_the_threshold_is_left_out(self):
        import asyncio

        strict = self.make("vector", threshold=0.99)
        self.assertEqual(asyncio.run(strict.prompt_sections(turn())), ())

    def test_without_facts_the_turn_gets_nothing(self):
        import asyncio

        self.assertIn(BIRDS, asyncio.run(self.recall.prompt_sections(turn()))[0].text)
        other = TurnContext(
            conversation=Conversation(
                id="c2", user_id="u1", character_id="hiori", created_at="2026-10-23T04:00:00+00:00"
            ),
            text="你喜欢什么鸟？",
            now=NOW,
        )
        self.assertEqual(asyncio.run(self.recall.prompt_sections(other)), ())

    # ---- the index, and what it costs

    def test_the_passages_are_embedded_once_and_the_second_turn_only_embeds_the_query(self):
        import asyncio

        asyncio.run(self.recall.prompt_sections(turn()))
        first = len(self.embedder.calls)
        self.assertGreaterEqual(first, 1)
        self.assertTrue(all(len(call) > 1 for call in self.embedder.calls[:-1]))

        asyncio.run(self.recall.prompt_sections(turn(text="考试怎么样？")))
        self.assertEqual(len(self.embedder.calls), first + 1)
        self.assertEqual(len(self.embedder.calls[-1]), 1)  # the query alone

    def test_an_edited_file_costs_only_the_passage_that_changed(self):
        import asyncio
        import os

        asyncio.run(self.recall.prompt_sections(turn()))
        before = len(self.embedder.calls)

        path = self.root / "facts" / "mano.md"
        path.write_text(FACTS + "\n## 新的\n她开始学做便当了。\n", encoding="utf-8")
        os.utime(path, (1, 1))  # a stamp the cache has not seen
        asyncio.run(self.recall.prompt_sections(turn(text="便当呢？")))

        # One call with the one new passage, then one with the query: the other two
        # passages came back from the database.
        self.assertEqual(len(self.embedder.calls), before + 2)
        self.assertEqual(len(self.embedder.calls[before]), 1)
        self.assertIn("便当", self.embedder.calls[before][0])

    def test_a_different_model_embeds_the_file_again(self):
        import asyncio

        asyncio.run(self.recall.prompt_sections(turn()))
        before = len(self.embedder.calls)
        other = Recall(
            self.store, self.root, mode="vector", embedder=self.embedder, model="another-model"
        )
        asyncio.run(other.prompt_sections(turn()))
        self.assertEqual(len(self.embedder.calls), before + 2)
        self.assertIn(BIRDS, "".join(self.embedder.calls[before]))

    def test_vectors_survive_a_restart(self):
        import asyncio

        asyncio.run(self.recall.prompt_sections(turn()))
        restart = Recall(
            self.store, self.root, mode="vector", embedder=self.embedder, model="fake-embedding"
        )
        asyncio.run(restart.prompt_sections(turn()))
        # Only the query was embedded: the passages came back from the database.
        self.assertEqual(len(self.embedder.calls[-1]), 1)

    # ---- failing, and the gate

    def test_an_embedding_service_that_is_down_costs_the_passages_not_the_turn(self):
        import asyncio

        down = FakeEmbeddings(fail=True)
        recall = Recall(
            self.store, self.root, mode="vector", embedder=down, model="fake-embedding",
            threshold=0.2, status=_Status(),
        )
        self.assertEqual(asyncio.run(recall.prompt_sections(turn())), ())
        self.assertEqual(recall._status.calls, [("embedding", False)])

    def test_the_capability_says_which_version_is_on(self):
        self.assertEqual(self.make("off").capabilities()["mode"], "off")
        self.assertEqual(self.make("whole").capabilities()["mode"], "whole")
        vector_capability = self.make("vector").capabilities()
        self.assertEqual(vector_capability["mode"], "vector")
        self.assertEqual(vector_capability["model"], "fake-embedding")

    def test_the_gate_that_says_no_looks_nothing_up(self):
        import asyncio

        gated = self.make("vector", gate=True, threshold=0.2)
        self.assertEqual(
            [question.key for question in gated.judge_questions()], ["recall.needed"]
        )
        self.assertEqual(
            asyncio.run(gated.prompt_sections(turn({"recall.needed": False}))), ()
        )
        self.assertEqual(self.embedder.calls, [])  # nothing embedded at all

    def test_a_gate_that_fails_looks_anyway(self):
        import asyncio

        gated = self.make("vector", gate=True, threshold=0.2)
        sections = asyncio.run(gated.prompt_sections(turn({})))
        self.assertEqual(len(sections), 1)

    def test_the_gate_is_off_by_default(self):
        self.assertEqual(self.make("vector").judge_questions(), ())

    # ---- the endpoint, and the splitter

    def test_the_endpoint_counts_passages_without_embedding_anything(self):
        import asyncio

        routes = self.make("vector").routes()
        state = asyncio.run(routes[("GET", "/api/recall")]({"c": "mano"}, {}))
        self.assertGreater(state["passages"], 1)
        self.assertEqual(state["embedded"], 0)
        self.assertEqual(self.embedder.calls, [])
        self.assertEqual(state["mode"], "vector")


class SplitterTest(unittest.TestCase):
    def test_every_passage_keeps_its_heading(self):
        passages = read_passages(FACTS)
        self.assertEqual({passage.heading for passage in passages}, {"好きなもの", "最近のこと"})
        self.assertIn(BIRDS, passages[0].text)

    def test_a_long_section_is_cut_into_pieces_of_at_most_the_limit(self):
        long = "# 大段\n\n" + "\n\n".join(f"第{n}段" + "啊" * 300 for n in range(3))
        passages = read_passages(long, limit=400)
        self.assertGreater(len(passages), 1)
        self.assertTrue(all(len(passage.text) <= 400 for passage in passages))

    def test_blank_text_has_no_passages(self):
        self.assertEqual(read_passages("  \n\n"), [])


class EmbeddingClientTest(unittest.TestCase):
    def client(self, answer):
        calls: list[dict] = []

        def opener(body):
            calls.append(body)
            return answer

        client = OpenAIEmbeddings(
            base_url="http://embedding.test/v1", api_key="k", model="m", opener=opener
        )
        return client, calls

    def test_the_vectors_come_back_in_the_order_asked(self):
        client, calls = self.client(
            {"data": [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1.0, 0.0]}]}
        )
        self.assertEqual(client.embed(["a", "b"]), [[1.0, 0.0], [0.0, 1.0]])
        self.assertEqual(calls[0]["input"], ["a", "b"])
        self.assertEqual(calls[0]["model"], "m")

    def test_a_short_answer_is_an_error_not_a_guess(self):
        client, _ = self.client({"data": [{"index": 0, "embedding": [1.0]}]})
        with self.assertRaises(EmbeddingError):
            client.embed(["a", "b"])

    def test_an_answer_without_data_is_an_error(self):
        client, _ = self.client({"error": "no"})
        with self.assertRaises(EmbeddingError):
            client.embed(["a"])


class _Status:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def record(self, service: str, *, ok: bool) -> None:
        self.calls.append((service, ok))


class SettingsTest(unittest.TestCase):
    """The switch has to be reachable from a `.env`, and a typo has to say so."""

    base = {"CHAT_LLM_BASE_URL": "u", "CHAT_LLM_API_KEY": "k", "CHAT_LLM_MODEL": "m"}

    def settings(self, **extra):
        from core.settings import Settings

        return Settings.from_env({**self.base, **extra}, require_credentials=False)

    def test_the_default_is_the_data_version_with_nothing_extra(self):
        settings = self.settings()
        self.assertEqual(settings.recall, "off")
        self.assertEqual(settings.recall_top, 4)
        self.assertAlmostEqual(settings.recall_min, 0.35)
        self.assertEqual(settings.recall_gate, "off")
        self.assertEqual(settings.embedding_model, "Qwen/Qwen3-Embedding-0.6B")

    def test_the_three_modes_and_the_gate_are_read(self):
        settings = self.settings(
            SCCORE_RECALL="vector", SCCORE_RECALL_GATE="judge", SCCORE_RECALL_TOP="6",
            SCCORE_RECALL_MIN="0.5", SCCORE_EMBEDDING_BASE_URL="http://embedding.test/v1",
            SCCORE_EMBEDDING_API_KEY="k2", SCCORE_EMBEDDING_MODEL="m2",
        )
        self.assertEqual(settings.recall, "vector")
        self.assertEqual(settings.recall_gate, "judge")
        self.assertEqual(settings.recall_top, 6)
        self.assertAlmostEqual(settings.recall_min, 0.5)
        self.assertEqual(settings.embedding_model, "m2")

    def test_a_misspelled_mode_is_an_error_not_a_silent_off(self):
        with self.assertRaises(RuntimeError):
            self.settings(SCCORE_RECALL="vectors")
        with self.assertRaises(RuntimeError):
            self.settings(SCCORE_RECALL_GATE="always")

    def test_a_judge_on_another_service_keeps_the_chat_where_it_is(self):
        settings = self.settings(
            SCCORE_JUDGE_BASE_URL="http://judge.test/v1", SCCORE_JUDGE_API_KEY="jk",
            SCCORE_JUDGE_MODEL="judge-model",
        )
        self.assertEqual(settings.base_url, "u")
        self.assertEqual(settings.judge_base_url, "http://judge.test/v1")
        self.assertEqual(settings.judge_api_key, "jk")


class WiringTest(unittest.TestCase):
    def test_the_whole_version_rides_beside_the_sheet_in_the_persona_slot(self):
        from app import _base_sections
        from plugins.characters import Character

        root = Path(tempfile.mkdtemp())
        (root / "facts").mkdir()
        (root / "facts" / "mano.md").write_text(FACTS, encoding="utf-8")
        store = Store(root / "wire.db")
        try:
            whole = Recall(store, root, mode="whole")
            character = Character(
                id="mano", name="真乃", name_zh="真乃", intro="", color="#8ab4f8", sheet="# 人设\n……"
            )
            sections = _base_sections(character, whole)
            self.assertEqual([section.slot.value for section in sections], ["persona", "persona"])
            self.assertIn(BIRDS, sections[1].text)

            off = Recall(store, root, mode="off")
            self.assertEqual(len(_base_sections(character, off)), 1)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
