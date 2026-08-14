"""Dual-version engine (rendering spec 2.0 vs 2.1) contract tests.

Spec 2.0 is the published rendering: the frozen dev split, the committed model
completions, and every published number bind to it, and
``tests/test_frozen_dev.py`` pins it against the frozen artifact. This file
pins the OTHER half of the contract:

- the default engine IS spec 2.0 (and unknown specs are rejected);
- ``minimal``/``light`` render byte-identically across specs (neither
  SPAGH_005 nor SPAGH_007 is in those profiles, and the 2.1 transforms are
  additive);
- ``standard``/``heavy``/``max`` render distinctly under 2.1, and the
  published ``light`` == ``standard`` tie is resolved (the whole point of the
  2.1 fix);
- spec-2.1 output still passes the compile-run oracle;
- the deep 256-key cascade respects the nested-group cap (CPython MAXINDENT);
- the exact 2.1 renders are hash-pinned (the golden equivalent for the opt-in
  spec; regenerate the table below with the loop in its comment after any
  INTENTIONAL 2.1 rendering change).
"""

import glob
import hashlib
import json
import pathlib
import unittest

from src.engine import Engine
from src.nodes.validator import validate

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = str(ROOT / "config" / "anti_patterns_db.json")
EXAMPLES = sorted(glob.glob(str(ROOT / "examples" / "*.json")))
PROFILES = ("minimal", "light", "standard", "heavy", "max")
LANGS = ("python", "javascript", "go", "java", "cpp")

# sha256[:16] of every (example, language) MAX-profile spec-2.1 render.
# Regenerate after an intentional 2.1 change:
#   python3 -c "import glob,hashlib,json;from src.engine import Engine; \
#     e=Engine('config/anti_patterns_db.json','max',spec='2.1'); \
#     [print(x.split('/')[-1][:-5],L,hashlib.sha256(e.generate(json.load(open(x)))['sources'][L].encode()).hexdigest()[:16]) \
#      for x in sorted(glob.glob('examples/*.json')) for L in ('python','javascript','go','java','cpp')]"
HASHES = {
    ("aggregate", "python"): "114a0c2b95e5c6f6",
    ("aggregate", "javascript"): "84eda7a29b343751",
    ("aggregate", "go"): "61635a5df9e0679b",
    ("aggregate", "java"): "65c730001cdbe86d",
    ("aggregate", "cpp"): "b994eb73d8fac7ea",
    ("analytics", "python"): "0dc854d89726d0dd",
    ("analytics", "javascript"): "b942ccdf7ccd8ece",
    ("analytics", "go"): "0327cf1ed12fd2ed",
    ("analytics", "java"): "4224777ede161cee",
    ("analytics", "cpp"): "dd39ad9f375c7283",
    ("combined", "python"): "3fe43c901e559a4c",
    ("combined", "javascript"): "f57a1144ed98a748",
    ("combined", "go"): "3c9fb1f91e35e35f",
    ("combined", "java"): "0cc10d14d0a30e00",
    ("combined", "cpp"): "9cc5be62da84da7c",
    ("conditional_select", "python"): "1ca50e9600175d5f",
    ("conditional_select", "javascript"): "9e88ae870b9729e9",
    ("conditional_select", "go"): "8b0d81003e8c16cb",
    ("conditional_select", "java"): "df87ea905ca3a6fd",
    ("conditional_select", "cpp"): "0a41d58d01413c90",
    ("key_value_lookup", "python"): "761f4c513d7c3d9f",
    ("key_value_lookup", "javascript"): "032c709dfeee89b6",
    ("key_value_lookup", "go"): "64e6f800d4f6af7f",
    ("key_value_lookup", "java"): "30e270f2a6be813b",
    ("key_value_lookup", "cpp"): "fda2e27e63cd4fc5",
    ("membership_check", "python"): "13cb0e0ac8093ac0",
    ("membership_check", "javascript"): "8c8d3a92f73f48aa",
    ("membership_check", "go"): "42ce924396137372",
    ("membership_check", "java"): "76dfe2961b5b2990",
    ("membership_check", "cpp"): "95134f95fbf1df45",
}


def _sources(profile, spec, ir):
    return Engine(DB, profile, spec=spec).generate(ir)["sources"]


class SpecMechanism(unittest.TestCase):
    def test_default_engine_is_spec_20(self):
        ir = json.load(open(EXAMPLES[0]))
        for p in PROFILES:
            self.assertEqual(Engine(DB, p).generate(ir)["sources"],
                             _sources(p, "2.0", ir))
        self.assertEqual(Engine(DB, "max").spec, "2.0")

    def test_unknown_spec_rejected(self):
        with self.assertRaises(ValueError):
            Engine(DB, "max", spec="9.9")


class SpecRenderContract(unittest.TestCase):
    def test_identity_and_distinctness_matrix(self):
        for ex in EXAMPLES:
            ir = json.load(open(ex))
            for p in PROFILES:
                s20, s21 = _sources(p, "2.0", ir), _sources(p, "2.1", ir)
                for lang in LANGS:
                    with self.subTest(example=ex, profile=p, lang=lang):
                        if p in ("minimal", "light"):
                            self.assertEqual(s20[lang], s21[lang])
                        else:
                            self.assertNotEqual(s20[lang], s21[lang])

    def test_light_standard_tie_resolved_under_21(self):
        for ex in EXAMPLES:
            ir = json.load(open(ex))
            l21, s21 = _sources("light", "2.1", ir), _sources("standard", "2.1", ir)
            for lang in LANGS:
                with self.subTest(example=ex, lang=lang):
                    self.assertNotEqual(l21[lang], s21[lang])

    def test_max_21_hash_pins(self):
        eng = Engine(DB, "max", spec="2.1")
        for ex in EXAMPLES:
            name = pathlib.Path(ex).stem
            srcs = eng.generate(json.load(open(ex)))["sources"]
            for lang in LANGS:
                with self.subTest(example=name, lang=lang):
                    h = hashlib.sha256(srcs[lang].encode()).hexdigest()[:16]
                    self.assertEqual(h, HASHES[(name, lang)])

    def test_21_markers_present(self):
        ir = json.load(open(str(ROOT / "examples" / "combined.json")))
        for lang in LANGS:
            src = _sources("standard", "2.1", ir)[lang]
            with self.subTest(lang=lang):
                self.assertIn("SPAGH_007", src)


class Spec21Semantics(unittest.TestCase):
    def test_oracle_passes_under_21(self):
        ir = json.load(open(str(ROOT / "examples" / "combined.json")))
        for p in ("standard", "max"):
            out = Engine(DB, p, spec="2.1").transpile(ir)
            for lang, r in out["validation"].items():
                with self.subTest(profile=p, lang=lang):
                    self.assertIn(r.status, ("PASS", "SKIP"))
            self.assertEqual(out["validation"]["python"].status, "PASS")

    def test_deep_cascade_respects_nest_cap(self):
        from eval.gen_samples import _config_resolver
        ir = _config_resolver(256)[0]
        out = Engine(DB, "max", spec="2.1").generate(ir)
        src = out["sources"]["python"]
        depth = max((len(l) - len(l.lstrip())) // 4
                    for l in src.splitlines() if l.strip())
        self.assertLess(depth, 30)
        self.assertEqual(validate("python", src, out["program"]).status, "PASS")


if __name__ == "__main__":
    unittest.main()
