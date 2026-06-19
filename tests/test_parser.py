"""Parser negative tests (blueprint §18.3).

Invalid IR (missing fields, wrong types, undeclared references, result_var
collisions, type mismatches) must raise IRValidationError at the parser stage so
nothing bad ever reaches a generator.
"""

import unittest

from src.ir_models import IRValidationError, KeyValueLookup, MembershipCheck
from src.nodes.parser import parse


def _valid():
    return {
        "version": "1.0",
        "module_name": "demo",
        "inputs": {
            "data_list": [10, 20, 30, 40],
            "search_val": 30,
            "config_db": {"dev": "localhost", "prod": "10.0.0.1"},
            "input_key": "dev",
        },
        "operations": [
            {
                "operation": "MEMBERSHIP_CHECK",
                "collection_name": "data_list",
                "target_var": "search_val",
                "result_var": "is_found",
            },
            {
                "operation": "KEY_VALUE_LOOKUP",
                "map_name": "config_db",
                "key_var": "input_key",
                "result_var": "out_val",
                "pairs": {"dev": "localhost", "prod": "10.0.0.1"},
                "default_value": "127.0.0.1",
            },
        ],
    }


class ParserPositiveTest(unittest.TestCase):
    def test_valid_program_parses(self):
        program = parse(_valid())
        self.assertEqual(program.module_name, "demo")
        self.assertEqual(len(program.operations), 2)
        self.assertIsInstance(program.operations[0], MembershipCheck)
        self.assertIsInstance(program.operations[1], KeyValueLookup)

    def test_defaults_applied(self):
        raw = {
            "inputs": {"xs": [1, 2], "a": 1},
            "operations": [
                {"operation": "MEMBERSHIP_CHECK", "collection_name": "xs",
                 "target_var": "a", "result_var": "r"},
            ],
        }
        program = parse(raw)
        self.assertEqual(program.version, "1.0")        # default
        self.assertEqual(program.module_name, "generated")  # default


class ParserNegativeTest(unittest.TestCase):
    def _assert_invalid(self, mutate):
        raw = _valid()
        mutate(raw)
        with self.assertRaises(IRValidationError):
            parse(raw)

    def test_root_not_object(self):
        with self.assertRaises(IRValidationError):
            parse([])

    def test_unsupported_version(self):
        self._assert_invalid(lambda r: r.update(version="9.9"))

    def test_bad_module_name(self):
        self._assert_invalid(lambda r: r.update(module_name="1bad"))

    def test_missing_operations(self):
        self._assert_invalid(lambda r: r.pop("operations"))

    def test_empty_operations(self):
        self._assert_invalid(lambda r: r.update(operations=[]))

    def test_unknown_operation(self):
        self._assert_invalid(lambda r: r["operations"].append({"operation": "NOPE"}))

    def test_bad_input_identifier(self):
        self._assert_invalid(lambda r: r["inputs"].__setitem__("not ok", 1))

    def test_non_homogeneous_array(self):
        self._assert_invalid(lambda r: r["inputs"].__setitem__("data_list", [1, "x"]))

    def test_collection_not_array(self):
        self._assert_invalid(lambda r: r["operations"][0].__setitem__("collection_name", "search_val"))

    def test_target_not_in_inputs(self):
        self._assert_invalid(lambda r: r["operations"][0].__setitem__("target_var", "ghost"))

    def test_target_type_mismatch(self):
        # search_val int vs data_list of ints is fine; make target a string instead
        def mutate(r):
            r["inputs"]["search_val"] = "thirty"
        self._assert_invalid(mutate)

    def test_result_var_collision(self):
        self._assert_invalid(lambda r: r["operations"][1].__setitem__("result_var", "is_found"))

    def test_result_var_collides_with_input(self):
        self._assert_invalid(lambda r: r["operations"][0].__setitem__("result_var", "search_val"))

    def test_key_var_not_string(self):
        def mutate(r):
            r["inputs"]["input_key"] = 5
        self._assert_invalid(mutate)

    def test_empty_pairs(self):
        self._assert_invalid(lambda r: r["operations"][1].__setitem__("pairs", {}))

    def test_default_type_mismatch(self):
        self._assert_invalid(lambda r: r["operations"][1].__setitem__("default_value", 999))

    def test_map_name_not_object(self):
        self._assert_invalid(lambda r: r["operations"][1].__setitem__("map_name", "input_key"))


if __name__ == "__main__":
    unittest.main()
