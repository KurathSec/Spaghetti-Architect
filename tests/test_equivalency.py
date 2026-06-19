"""Cross-language run equivalency (blueprint §18.2).

Runs ``validate()`` for every example and asserts each language is PASS or SKIP
(never FAIL). Languages with a missing toolchain auto-SKIP, so this suite runs
unchanged locally and in CI. Python must always PASS (its "toolchain" — the
interpreter — is always present).
"""

import glob
import json
import os
import unittest

from src.engine import Engine

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXAMPLES = os.path.join(ROOT, "examples")
DB = os.path.join(ROOT, "config", "anti_patterns_db.json")


def _cases():
    for path in sorted(glob.glob(os.path.join(EXAMPLES, "*.json"))):
        name = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8") as f:
            yield name, json.load(f)


class EquivalencyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Engine(DB, "max")

    def test_no_language_fails(self):
        for name, raw in _cases():
            out = self.engine.transpile(raw)
            for lang, res in out["validation"].items():
                with self.subTest(case=name, lang=lang):
                    self.assertIn(
                        res.status, ("PASS", "SKIP"),
                        f"{lang} reported {res.status}: {res.detail}",
                    )

    def test_python_always_passes(self):
        for name, raw in _cases():
            out = self.engine.transpile(raw)
            res = out["validation"]["python"]
            with self.subTest(case=name):
                self.assertEqual(res.status, "PASS", res.detail)


if __name__ == "__main__":
    unittest.main()
