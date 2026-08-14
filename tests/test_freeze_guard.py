"""Freeze guards: the frozen dataset-2.0 artifact must be overwrite-protected.

``bench.dataset._write_guarded`` refuses to replace an existing file with
DIFFERENT bytes unless forced (identical bytes pass silently, which is what a
byte-faithful legacy re-freeze produces), and the versioned freeze writes the
2.1 split to its own paths only.
"""

import os
import tempfile
import unittest

from bench.dataset import DATASET_VERSION_V21, _write_guarded


class WriteGuard(unittest.TestCase):
    def test_identical_bytes_pass(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.json")
            _write_guarded(p, "same\n", force=False)
            _write_guarded(p, "same\n", force=False)  # no raise
            self.assertEqual(open(p).read(), "same\n")

    def test_differing_bytes_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.json")
            _write_guarded(p, "old\n", force=False)
            with self.assertRaises(SystemExit):
                _write_guarded(p, "new\n", force=False)
            self.assertEqual(open(p).read(), "old\n")

    def test_force_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.json")
            _write_guarded(p, "old\n", force=False)
            _write_guarded(p, "new\n", force=True)
            self.assertEqual(open(p).read(), "new\n")

    def test_v21_constant(self):
        self.assertEqual(DATASET_VERSION_V21, "2.1")


if __name__ == "__main__":
    unittest.main()
