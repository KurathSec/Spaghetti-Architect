"""RNG-stream stability: the frozen mints must stay byte-reproducible.

Three generator streams are frozen-artifact-binding and must never shift:
``build(extended=False)`` reproduces the committed ``eval/samples``;
``build(extended=True)`` reproduces the dev split and the Tier-A private
re-mint; ``build_heldout_tiers`` (the fixed v2.0 enumeration) reproduces the
private test structures the published test numbers bind to. Any new sampling
added to ``eval/gen_samples.py`` must draw from an independent derived stream
(or be RNG-free) so these hashes never move.

The tier hash uses a fixed TEST seed (not the private held-out seed), which
pins the structure/derivation logic without touching any secret.
"""

import hashlib
import json
import unittest

from eval.gen_samples import SEED, build, build_heldout_tiers

# sha256 over json.dumps(..., sort_keys=True), recorded at v0.2.5 (2026-08-14).
H_DEFAULT = "7efa5c1c1ac2ccc1d7990e1f3d2aff3f3fd9bf942d630164202861cffb887804"
H_EXTENDED = "0c7b20b2bdf673eb5b1c19bb0bf31829605894342fe6a96fa115f957e01e129e"
H_TIERS_SAMPLES = "21fd2caa86426a8dadfc405dd5bb3e1ea2a44283b37021f4f2916ee9206ec1cc"
H_TIERS_META = "e9649f6f8bc2e6cf94f65a6d9186db1feb5427019a64604768276c8ce2a423bc"
TIER_TEST_SEED = 1234


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


class StreamStability(unittest.TestCase):
    def test_default_stream_is_stable(self):
        samples, _ = build(SEED, extended=False)
        self.assertEqual(len(samples), 35)
        self.assertEqual(_h(samples), H_DEFAULT)

    def test_extended_stream_is_stable(self):
        samples, _ = build(SEED, extended=True)
        self.assertEqual(len(samples), 100)
        self.assertEqual(_h(samples), H_EXTENDED)

    def test_heldout_tier_stream_is_stable(self):
        samples, tier_of = build_heldout_tiers(TIER_TEST_SEED)
        self.assertEqual(len(samples), 24)
        self.assertEqual(_h(samples), H_TIERS_SAMPLES)
        self.assertEqual(_h(tier_of), H_TIERS_META)


if __name__ == "__main__":
    unittest.main()
