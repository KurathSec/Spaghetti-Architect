"""Sidecar annotation mode (v0.3.0) contract tests.

Sidecar mode keeps the module header in-source (the dual-use friction) and
diverts every other comment into a line-aligned structure. Two identities pin
it: re-inserting the sidecar entries in order at ``line + ordinal`` must
reproduce the FULL render byte-identically (any off-by-one in the emitter
breaks this), and the sidecar render minus its two header lines must equal the
``none`` (unannotated) render. Full-mode byte-stability is separately pinned
by tests/test_frozen_dev.py.
"""

import json
import pathlib
import unittest

from src.engine import Engine

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = str(ROOT / "config" / "anti_patterns_db.json")
IR = json.load(open(str(ROOT / "examples" / "combined.json")))
LANGS = ("python", "javascript", "go", "java", "cpp")


def _reinsert(sidecar_src: str, entries) -> str:
    lines = sidecar_src.split("\n")
    for i, ent in enumerate(entries):
        lines.insert(ent["line"] + i, ent["source_line"])
    return "\n".join(lines)


class SidecarRoundTrip(unittest.TestCase):
    def _renders(self, profile):
        full = Engine(DB, profile).generate(IR)
        none = Engine(DB, profile, annotate=False).generate(IR)
        side = Engine(DB, profile, annotations="sidecar").generate(IR)
        return full, none, side

    def test_reinsertion_reproduces_full_render(self):
        for profile in ("minimal", "max"):
            full, _none, side = self._renders(profile)
            self.assertIn("sidecars", side)
            for lang in LANGS:
                with self.subTest(profile=profile, lang=lang):
                    sc = side["sidecars"][lang]
                    self.assertEqual(sc["format"], "spaghetti-sidecar/1")
                    rebuilt = _reinsert(side["sources"][lang], sc["entries"])
                    self.assertEqual(rebuilt, full["sources"][lang])

    def test_sidecar_minus_header_is_none_render(self):
        _full, none, side = self._renders("max")
        for lang in LANGS:
            with self.subTest(lang=lang):
                side_lines = side["sources"][lang].split("\n")
                none_lines = none["sources"][lang].split("\n")
                # the header is exactly the first two (comment) lines
                marker = "# " if lang == "python" else "// "
                self.assertTrue(side_lines[0].startswith(marker))
                self.assertTrue(side_lines[1].startswith(marker))
                self.assertEqual(side_lines[2:], none_lines)

    def test_entries_never_carry_header_kind(self):
        _full, _none, side = self._renders("max")
        for lang in LANGS:
            for ent in side["sidecars"][lang]["entries"]:
                self.assertNotEqual(ent["kind"], "header")

    def test_full_and_none_have_no_sidecars_key(self):
        self.assertNotIn("sidecars", Engine(DB, "max").generate(IR))
        self.assertNotIn("sidecars",
                         Engine(DB, "max", annotate=False).generate(IR))


if __name__ == "__main__":
    unittest.main()
