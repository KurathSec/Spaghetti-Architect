"""Golden snapshot regression (blueprint §18.1).

For each ``examples/*.json`` x 5 languages, assert the generated source is
byte-for-byte equal to ``tests/golden/<lang>/<case>.<ext>``. Relies on the
determinism guarantee (blueprint §2, principle 5).

Refresh baselines after an intended generator change with::

    UPDATE_GOLDEN=1 python -m pytest tests/test_golden.py
"""

import glob
import json
import os
import unittest

from src.generators import REGISTRY
from src.nodes.parser import parse
from src.nodes.planner import Planner

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXAMPLES = os.path.join(ROOT, "examples")
GOLDEN = os.path.join(HERE, "golden")
DB = os.path.join(ROOT, "config", "anti_patterns_db.json")
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"
PROFILE = "max"


def _cases():
    for path in sorted(glob.glob(os.path.join(EXAMPLES, "*.json"))):
        name = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8") as f:
            yield name, json.load(f)


def _generate(raw):
    program = parse(raw)
    plan = Planner(DB, PROFILE).plan(program)
    return {lang: gen.generate(program, plan) for lang, gen in REGISTRY.items()}


class GoldenTest(unittest.TestCase):
    def test_golden_snapshots(self):
        for name, raw in _cases():
            srcs = _generate(raw)
            for lang, gen in REGISTRY.items():
                path = os.path.join(GOLDEN, lang, f"{name}{gen.extension}")
                src = srcs[lang]
                if UPDATE:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(src)
                    continue
                with self.subTest(case=name, lang=lang):
                    self.assertTrue(
                        os.path.exists(path),
                        f"missing golden {path}; run UPDATE_GOLDEN=1 to create it",
                    )
                    with open(path, encoding="utf-8") as f:
                        expected = f.read()
                    self.assertEqual(src, expected, f"golden drift in {name}/{lang}")


if __name__ == "__main__":
    unittest.main()
