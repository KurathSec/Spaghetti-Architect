"""Frozen-dev byte-identity sweep: the safety tripwire for engine changes.

The committed ``bench/data/dev/*.json`` records freeze the rendered sources the
published benchmark numbers (and the committed raw model completions) bind to.
The DEFAULT engine must reproduce every one of those cells byte-for-byte: the
offline regrade paths rebuild ``spaghetti_src`` through the live engine, so any
drift here silently corrupts the grading of the committed completions.

100 records x 5 profiles x 5 languages = 2500 cells. Generation is in-process
and instant; this is cheap enough to run at every phase boundary.
"""

import json
import pathlib
import unittest

from src.engine import Engine

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = str(ROOT / "config" / "anti_patterns_db.json")
DEV = ROOT / "bench" / "data" / "dev"
PROFILES = ("minimal", "light", "standard", "heavy", "max")


class FrozenDevByteIdentity(unittest.TestCase):
    def test_default_engine_reproduces_frozen_dev_split(self):
        records = sorted(DEV.glob("*.json"))
        self.assertEqual(len(records), 100, "frozen dev split must have 100 records")
        engines = {p: Engine(DB, p) for p in PROFILES}
        checked = 0
        for path in records:
            rec = json.loads(path.read_text())
            for profile in PROFILES:
                rendered = engines[profile].generate(rec["ir"])["sources"]
                frozen = rec["sources"][profile]
                for lang, src in frozen.items():
                    with self.subTest(stem=rec["stem"], profile=profile, lang=lang):
                        self.assertEqual(
                            rendered[lang], src,
                            f"default-engine render drifted from the frozen dev "
                            f"artifact at ({rec['stem']}, {profile}, {lang})",
                        )
                        checked += 1
        self.assertEqual(checked, 2500)


if __name__ == "__main__":
    unittest.main()
