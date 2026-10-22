"""The real client, minus the network: what it sends, and what it makes of a stream.

The one thing these tests cannot prove is that a service accepts the request; that is what
`serve.py` on a real key is for. Everything below stays true either way.
"""

import json
import unittest

from core.llm import ChatMessage, LLMError, OpenAICompat, _read_stream


def event(delta: dict) -> bytes:
    return ("data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n").encode()


class TestReading(unittest.TestCase):
    def test_pieces_come_out_in_order(self):
        lines = [
            event({"role": "assistant"}),
            event({"content": "こん"}),
            event({"content": "にちは"}),
            b"data: [DONE]\n",
        ]
        self.assertEqual("".join(_read_stream(lines)), "こんにちは")

    def test_the_role_only_first_event_is_not_a_piece(self):
        self.assertEqual(list(_read_stream([event({"role": "assistant"})])), [])

    def test_a_finish_with_no_content_is_not_a_piece(self):
        self.assertEqual(list(_read_stream([event({"content": ""}), event({})])), [])

    def test_done_ends_the_stream(self):
        lines = [event({"content": "あ"}), b"data: [DONE]\n", event({"content": "い"})]
        self.assertEqual("".join(_read_stream(lines)), "あ")

    def test_noise_is_skipped(self):
        lines = [b"\n", b": keep-alive\n", b"data: not json\n", event({"content": "あ"})]
        self.assertEqual("".join(_read_stream(lines)), "あ")

    def test_bytes_and_text_lines_both_work(self):
        self.assertEqual("".join(_read_stream([event({"content": "あ"}).decode()])), "あ")


class TestWhatItSends(unittest.TestCase):
    def test_the_payload_is_a_chat_completion(self):
        client = OpenAICompat(base_url="http://x/v1/", api_key="k", model="m")
        payload = client.payload([ChatMessage("system", "s"), ChatMessage("user", "u")])
        self.assertEqual(payload["model"], "m")
        self.assertTrue(payload["stream"])
        self.assertEqual(
            payload["messages"],
            [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        )
        self.assertNotIn("temperature", payload)

    def test_temperature_is_sent_only_when_asked_for(self):
        client = OpenAICompat(base_url="http://x/v1", api_key="k", model="m", temperature=0.8)
        self.assertEqual(client.payload([ChatMessage("user", "u")])["temperature"], 0.8)

    def test_the_trailing_slash_of_a_base_url_does_not_double(self):
        client = OpenAICompat(base_url="http://x/v1/", api_key="k", model="m")
        self.assertEqual(client._base_url, "http://x/v1")


class TestStreaming(unittest.IsolatedAsyncioTestCase):
    async def test_chunks_arrive_in_order(self):
        def opener(payload):
            self.assertTrue(payload["stream"])
            return [
                event({"role": "assistant"}),
                event({"content": "おは"}),
                event({"content": "よう"}),
                b"data: [DONE]\n",
            ]

        client = OpenAICompat(base_url="http://x/v1", api_key="k", model="m", open_stream=opener)
        pieces = [piece async for piece in client.stream([ChatMessage("user", "hi")])]
        self.assertEqual("".join(pieces), "おはよう")

    async def test_a_refused_request_raises_an_llm_error(self):
        def opener(payload):
            raise OSError("HTTP Error 401: Unauthorized")

        client = OpenAICompat(base_url="http://x/v1", api_key="k", model="m", open_stream=opener)
        with self.assertRaises(LLMError):
            [piece async for piece in client.stream([ChatMessage("user", "hi")])]

    async def test_a_stream_that_breaks_midway_gives_what_arrived_then_raises(self):
        def opener(payload):
            yield event({"content": "あ"})
            raise OSError("connection reset")

        client = OpenAICompat(base_url="http://x/v1", api_key="k", model="m", open_stream=opener)
        seen: list[str] = []
        with self.assertRaises(LLMError):
            async for piece in client.stream([ChatMessage("user", "hi")]):
                seen.append(piece)
        self.assertEqual(seen, ["あ"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
