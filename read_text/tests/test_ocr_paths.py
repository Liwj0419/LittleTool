import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from read_text.ocr import Ocr, OcrError, _merge_english_paths


def word(text, x, w, y=30):
    return {"text": text, "x": x, "y": y, "w": w, "h": 12}


class OcrPathsTest(unittest.TestCase):
    def test_repairs_aligned_path_and_preserves_mixed_text(self):
        chinese_line = [word("目录", 20, 30, y=5)]
        before = word("读取", 0, 18)
        after = word("完成", 290, 24)
        primary = [
            chinese_line,
            [
                before, word("assets/hero", 25, 70), word("card", 107, 26),
                word("s", 145, 6), word("a", 164, 10), word("011.tga.meta", 180, 96), after,
            ],
        ]
        path = [word("assets/hero_card_s_attr_11", 25, 180), word(".tga.meta", 210, 66)]

        _merge_english_paths(primary, [path])

        self.assertEqual(primary, [chinese_line, [before, *path, after]])
        self.assertEqual(path[0]["join_left"], 70)
        self.assertEqual(path[1]["join_right"], 96)
        self.assertTrue(path[1]["continues_path"])

    def test_preserves_chinese_or_partially_covered_text(self):
        for original in (
            [word("图片", 5, 24), word("ab", 30, 20)],
            [word("filename ab", 0, 70)],
        ):
            with self.subTest(original=original):
                primary = [original.copy()]
                _merge_english_paths(primary, [[word("img_ab", 18, 52)]])
                self.assertEqual(primary, [original])

    def test_keeps_original_when_english_has_no_extra_path_characters(self):
        original = [word("img_11.png", 20, 70)]
        primary = [original.copy()]
        _merge_english_paths(primary, [[word("img_ll.png", 20, 70)]])
        self.assertEqual(primary, [original])

    def test_interrupted_english_response_keeps_completed_primary_result(self):
        ocr = Ocr()
        ocr._proc = SimpleNamespace(stdin=io.StringIO())
        replies = ["ok", "0\t20\t30\t70\t12\timg11.png", "english",
                   "0\t20\t30\t70\t12\timg_11.png", OcrError("timeout")]
        with patch.object(ocr, "_next", side_effect=replies), patch("read_text.ocr.cfgmod.log"):
            self.assertEqual(ocr._call("sample.png"), [[word("img11.png", 20, 70)]])


if __name__ == "__main__":
    unittest.main()
