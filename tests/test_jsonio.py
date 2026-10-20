import os
import tempfile
import unittest
from pathlib import Path

from feeder import jsonio


class JsonioTests(unittest.TestCase):
    def test_dumps_is_key_ordered_lf_and_keeps_unicode(self):
        text = jsonio.dumps({"b": 1, "a": {"z": "\u00e9", "y": [1, 2]}})
        self.assertEqual(text, '{\n  "a": {\n    "y": [\n      1,\n      2\n    ],\n    "z": "\u00e9"\n  },\n  "b": 1\n}\n')
        self.assertNotIn("\r", text)

    def test_dump_json_writes_utf8_lf_atomically_and_round_trips(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "nested" / "out.json"
            jsonio.dump_json(target, {"k": "v\u2013w", "n": None})
            raw = target.read_bytes()
            self.assertEqual(raw, jsonio.dumps({"k": "v\u2013w", "n": None}).encode("utf-8"))
            self.assertNotIn(b"\r\n", raw)
            self.assertEqual(jsonio.load_json(target), {"k": "v\u2013w", "n": None})
            leftovers = [p for p in os.listdir(target.parent) if p.endswith(".tmp")]
            self.assertEqual(leftovers, [], "no temporary file is left behind")
            self.assertTrue(jsonio.is_canonical(target))

    def test_is_canonical_detects_unsorted_or_crlf(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.json"
            p.write_bytes(b'{"b": 1, "a": 2}\n')
            self.assertFalse(jsonio.is_canonical(p))
            p.write_bytes(jsonio.dumps({"a": 2, "b": 1}).replace("\n", "\r\n").encode("utf-8"))
            self.assertFalse(jsonio.is_canonical(p))
            p.write_bytes(b"not json")
            self.assertFalse(jsonio.is_canonical(p))


if __name__ == "__main__":
    unittest.main()
