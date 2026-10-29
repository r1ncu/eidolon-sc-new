"""The reply protocol: format, cues, and the parser that streams them.

The point of these tests is the thing a plugin architecture depends on: the kernel can be
verified with no network, no API key, no database, no browser.
"""

import unittest

from core.protocol import (
    DIRECTED_FORMAT_INSTRUCTIONS,
    FORMAT_INSTRUCTIONS,
    PhotoTagged,
    ReplyParser,
    Segment,
    SegmentComplete,
    SegmentDelta,
    SpeechDelta,
    format_instructions,
    format_reply,
    unquote,
    spoken_by,
    voiced,
    without_cues,
)


def run(chunks: list[str]) -> tuple[ReplyParser, list]:
    parser = ReplyParser()
    events: list = []
    for chunk in chunks:
        events += parser.feed(chunk)
    events += parser.finish()
    return parser, events


def shown(events: list) -> dict[tuple[int, str], str]:
    """What the browser would have put on screen, per segment and language."""
    out: dict[tuple[int, str], str] = {}
    for e in events:
        if isinstance(e, SegmentDelta):
            out[(e.segment, e.lang)] = out.get((e.segment, e.lang), "") + e.text
    return out


def said(events: list) -> dict[int, str]:
    out: dict[int, str] = {}
    for e in events:
        if isinstance(e, SpeechDelta):
            out[e.segment] = out.get(e.segment, "") + e.text
    return out


class TestOneChunk(unittest.TestCase):
    def test_two_segments(self):
        text = "<ja>おはよう</ja><zh>早上好</zh><ja>今日は早いね</ja><zh>今天真早啊</zh>"
        parser, events = run([text])
        self.assertEqual([s.ja for s in parser.segments], ["おはよう", "今日は早いね"])
        self.assertEqual([s.zh for s in parser.segments], ["早上好", "今天真早啊"])
        self.assertEqual(
            shown(events),
            {
                (0, "ja"): "おはよう",
                (0, "zh"): "早上好",
                (1, "ja"): "今日は早いね",
                (1, "zh"): "今天真早啊",
            },
        )
        self.assertEqual(said(events), {0: "おはよう", 1: "今日は早いね"})

    def test_complete_fires_once_per_segment(self):
        text = "<ja>あ</ja><zh>啊</zh><ja>い</ja><zh>一</zh>"
        _, events = run([text])
        completes = [e.segment for e in events if isinstance(e, SegmentComplete)]
        self.assertEqual(completes, [0, 1])

    def test_japanese_goes_out_before_the_subtitle(self):
        """The whole reason for streaming: voice can start before the line finishes."""
        parser = ReplyParser()
        events = parser.feed("<ja>こんにちは</ja>")
        self.assertIn(SpeechDelta(0, "こんにちは"), events)
        self.assertIn(SegmentDelta(0, "ja", "こんにちは"), events)
        self.assertEqual(parser.segments[0].zh, "")


class TestStreaming(unittest.TestCase):
    def test_char_by_char_matches_one_chunk(self):
        text = "<ja>えっと……</ja><zh>那个……</zh><ja>ありがとう</ja><zh>谢谢</zh>"
        one, one_events = run([text])
        many, many_events = run(list(text))
        self.assertEqual([(s.ja, s.zh) for s in one.segments], [(s.ja, s.zh) for s in many.segments])
        self.assertEqual(shown(one_events), shown(many_events))
        self.assertEqual(said(one_events), said(many_events))

    def test_tag_cut_across_chunks(self):
        chunks = ["<j", "a>こん", "にちは</j", "a><z", "h>你好</zh>"]
        parser, events = run(chunks)
        self.assertEqual(parser.segments, [Segment("こんにちは", "你好")])
        self.assertEqual(shown(events), {(0, "ja"): "こんにちは", (0, "zh"): "你好"})

    def test_no_tag_fragment_ever_reaches_the_screen(self):
        text = "<ja>おはようございます、プロデューサーさん！</ja><zh>早上好，制作人！</zh>"
        _, events = run(list(text))
        for text_shown in shown(events).values():
            self.assertNotIn("<", text_shown)
            self.assertNotIn(">", text_shown)

    def test_reply_ignoring_the_format_becomes_one_segment(self):
        parser, events = run(["こんにちは", "、プロデューサー。"])
        self.assertEqual(parser.segments, [Segment("こんにちは、プロデューサー。", "")])
        self.assertEqual(shown(events), {(0, "ja"): "こんにちは、プロデューサー。"})

    def test_half_written_segment_is_not_complete(self):
        parser = ReplyParser()
        parser.feed("<ja>まだ書きかけ</ja>")
        parser.feed("<zh>还没写完")
        self.assertEqual([s.ja for s in parser.complete], [])
        parser.finish()
        self.assertEqual([s.zh for s in parser.complete], ["还没写完"])


class TestCues(unittest.TestCase):
    def test_square_bracket_cue_is_voiced_not_shown(self):
        parser, events = run(["<ja>[shyly] えへへ</ja><zh>嘿嘿</zh>"])
        self.assertEqual(shown(events)[(0, "ja")], "えへへ")
        # 行首的空格被 CueFilter 一起去掉（上游行为，移植时保持一致）
        self.assertEqual(said(events)[0], "[shyly]えへへ")
        self.assertEqual(parser.segments[0].ja, "[shyly] えへへ")

    def test_space_mid_line_is_kept_for_the_voice(self):
        """Removing a cue leaves the spaces on both of its sides, as upstream does: the
        shown line then has two spaces, the voiced one keeps the cue between them."""
        _, events = run(["<ja>えへへ [shyly] ありがとう</ja><zh>嘿嘿，谢谢</zh>"])
        self.assertEqual(shown(events)[(0, "ja")], "えへへ  ありがとう")
        self.assertEqual(said(events)[0], "えへへ [shyly] ありがとう")

    def test_line_start_whitespace_is_dropped_whatever_the_chunking(self):
        """The space after a cue at the front of a line goes, shown and voiced alike, and
        the chunk boundaries make no difference to that."""
        for chunks in (
            ["<ja>[shyly] えへへ</ja><zh>a</zh>"],
            ["<ja>[shyly]", " えへへ</ja>", "<zh>a</zh>"],
            ["<ja>[shyly]", " ", "えへへ</ja>", "<zh>a</zh>"],
        ):
            with self.subTest(chunks=chunks):
                _, events = run(chunks)
                self.assertEqual(shown(events)[(0, "ja")], "えへへ")
                self.assertEqual(said(events)[0], "[shyly]えへへ")

    def test_two_cues_at_the_front_end_up_adjacent(self):
        _, events = run(["<ja>[shyly] [gently] えへへ</ja><zh>a</zh>"])
        self.assertEqual(shown(events)[(0, "ja")], "えへへ")
        self.assertEqual(said(events)[0], "[shyly][gently]えへへ")

    def test_malformed_cue_is_dropped_for_everyone(self):
        for written in ("(happy)", "（小声で）", "【嬉しそうに】"):
            with self.subTest(written=written):
                self.assertEqual(without_cues(f"{written}えへへ"), "えへへ")
                self.assertEqual(voiced(f"{written}えへへ"), "えへへ")

    def test_paired_tags_only_for_models_that_read_them(self):
        line = "[shyly] <whisper>内緒</whisper>だよ"
        self.assertEqual(spoken_by(line, "s2.1-pro"), "[shyly] 内緒だよ")
        self.assertEqual(spoken_by(line, "drama-3"), line)

    def test_format_switches_with_the_voice_model(self):
        self.assertIs(format_instructions("drama-3"), DIRECTED_FORMAT_INSTRUCTIONS)
        self.assertIs(format_instructions("s2.1-pro"), FORMAT_INSTRUCTIONS)
        self.assertIs(format_instructions(None), FORMAT_INSTRUCTIONS)


class TestPhoto(unittest.TestCase):
    def test_photo_tag_at_the_front_is_handed_over(self):
        text = "<photo>ラーメン屋の前</photo><ja>見て！</ja><zh>你看！</zh>"
        parser, events = run([text])
        self.assertIn(PhotoTagged("ラーメン屋の前"), events)
        self.assertEqual(parser.segments, [Segment("見て！", "你看！")])
        self.assertEqual(shown(events), {(0, "ja"): "見て！", (0, "zh"): "你看！"})

    def test_photo_tag_in_the_middle_is_dropped(self):
        parser, events = run(["<ja>あ<photo>x</photo>い</ja><zh>啊</zh>"])
        self.assertFalse([e for e in events if isinstance(e, PhotoTagged)])
        self.assertEqual(parser.segments[0].ja, "あい")

    def test_photo_tag_after_the_first_segment_is_dropped(self):
        parser, events = run(["<ja>あ</ja><zh>啊</zh><photo>x</photo>"])
        self.assertFalse([e for e in events if isinstance(e, PhotoTagged)])
        self.assertEqual(parser.segments, [Segment("あ", "啊")])

    def test_photo_tag_left_open_is_left_out(self):
        parser, events = run(["<photo>書きかけ"])
        self.assertFalse([e for e in events if isinstance(e, PhotoTagged)])
        self.assertEqual(parser.segments, [])


class TestFormatReply(unittest.TestCase):
    def test_round_trip_through_the_parser(self):
        segments = [Segment("おはよう", "早上好"), Segment("今日は早いね", "今天真早啊")]
        written = format_reply(segments)
        parser, _ = run([written])
        self.assertEqual([(s.ja, s.zh) for s in parser.segments], [(s.ja, s.zh) for s in segments])

    def test_photo_tag_marked_failed(self):
        written = format_reply([Segment("あ", "啊")], photo_tag="x", photo_failed=True)
        self.assertTrue(written.startswith('<photo status="failed">x</photo>'))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestUnquote(unittest.TestCase):
    """整句被引号包住时剥掉；句子里引别人的话不碰。"""

    def test_a_wrapped_line_loses_its_quotes(self):
        self.assertEqual(unquote("「はい」"), "はい")
        self.assertEqual(unquote("『はい』"), "はい")
        self.assertEqual(unquote("「「はい」」"), "はい")

    def test_an_unmatched_mark_is_left_alone(self):
        self.assertEqual(unquote("「はい"), "「はい")
        self.assertEqual(unquote("はい」"), "はい」")

    def test_a_quote_inside_the_sentence_survives(self):
        self.assertEqual(unquote("「はぁ？」って言った"), "「はぁ？」って言った")
