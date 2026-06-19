"""Profile matrix (blueprint §18.4).

Each strength level (minimal / standard / max) must actually change the generated
output and still validate (Python PASS).
"""

import json
import os
import unittest

from src.engine import Engine

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB = os.path.join(ROOT, "config", "anti_patterns_db.json")
COMBINED = os.path.join(ROOT, "examples", "combined.json")
PROFILES = ("minimal", "standard", "max")


class ProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(COMBINED, encoding="utf-8") as f:
            cls.raw = json.load(f)

    def test_each_profile_validates(self):
        for profile in PROFILES:
            out = Engine(DB, profile).transpile(self.raw)
            with self.subTest(profile=profile):
                self.assertEqual(out["validation"]["python"].status, "PASS",
                                 out["validation"]["python"].detail)

    def test_profiles_produce_distinct_output(self):
        py = {}
        for profile in PROFILES:
            out = Engine(DB, profile).transpile(self.raw)
            py[profile] = out["sources"]["python"]
        self.assertNotEqual(py["minimal"], py["standard"], "minimal vs standard identical")
        self.assertNotEqual(py["standard"], py["max"], "standard vs max identical")

    def test_unknown_profile_rejected(self):
        with self.assertRaises(ValueError):
            Engine(DB, "ludicrous")


if __name__ == "__main__":
    unittest.main()
