import unittest

from feeder.schema_validate import SchemaError, is_valid, validate


class ValidatorTests(unittest.TestCase):
    def test_type_single_and_list(self):
        self.assertEqual(validate("x", {"type": "string"}), [])
        self.assertEqual(validate(None, {"type": ["string", "null"]}), [])
        self.assertTrue(validate(3, {"type": "string"}))
        self.assertTrue(validate(True, {"type": "integer"}), "a bool is not an integer")
        self.assertEqual(validate(3, {"type": "number"}), [])
        self.assertEqual(validate(3.5, {"type": "number"}), [])

    def test_enum_const_pattern_lengths(self):
        self.assertEqual(validate("a", {"enum": ["a", "b"]}), [])
        self.assertTrue(validate("c", {"enum": ["a", "b"]}))
        self.assertTrue(validate("c", {"const": "a"}))
        self.assertEqual(validate("abc-1", {"pattern": "^[a-z]+-\\d$"}), [])
        self.assertTrue(validate("ABC", {"pattern": "^[a-z]+$"}))
        self.assertTrue(validate("", {"minLength": 1}))
        self.assertTrue(validate("abcd", {"maxLength": 3}))

    def test_required_properties_additional(self):
        schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}},
                  "additionalProperties": False}
        self.assertEqual(validate({"a": 1}, schema), [])
        self.assertIn("$: missing required property 'a'", validate({}, schema))
        self.assertIn("$: unexpected property 'b'", validate({"a": 1, "b": 2}, schema))
        self.assertIn("$.a: expected type integer, got str", validate({"a": "x"}, schema))
        typed_extra = {"type": "object", "additionalProperties": {"type": "string"}}
        self.assertEqual(validate({"x": "y"}, typed_extra), [])
        self.assertTrue(validate({"x": 1}, typed_extra))

    def test_items_min_max_and_numeric_bounds(self):
        schema = {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 10}, "minItems": 1, "maxItems": 3}
        self.assertEqual(validate([1, 2], schema), [])
        self.assertTrue(validate([], schema))
        self.assertTrue(validate([1, 2, 3, 4], schema))
        self.assertIn("$[0]: -1 is below minimum 0", validate([-1], schema))
        self.assertTrue(validate([11], schema))

    def test_if_then_else(self):
        schema = {"type": "object", "properties": {"auth": {"type": "object"}, "runs_on": {"type": "array"}},
                  "if": {"required": ["auth"]},
                  "then": {"required": ["runs_on"], "properties": {"runs_on": {"items": {"const": "local"}}}},
                  "else": {"properties": {"runs_on": {"maxItems": 2}}}}
        self.assertEqual(validate({"auth": {}, "runs_on": ["local"]}, schema), [])
        self.assertTrue(validate({"auth": {}}, schema), "then branch requires runs_on")
        self.assertTrue(validate({"auth": {}, "runs_on": ["actions"]}, schema), "then branch pins local")
        self.assertEqual(validate({"runs_on": ["actions", "local"]}, schema), [])
        self.assertTrue(validate({"runs_on": ["a", "b", "c"]}, schema), "else branch applies without auth")

    def test_ref_to_local_defs(self):
        schema = {"type": "object", "properties": {"r": {"$ref": "#/$defs/thing"}},
                  "$defs": {"thing": {"type": "string", "pattern": "^t"}}}
        self.assertEqual(validate({"r": "tea"}, schema), [])
        self.assertTrue(validate({"r": "coffee"}, schema))
        with self.assertRaises(SchemaError):
            validate({"r": "x"}, {"properties": {"r": {"$ref": "#/$defs/missing"}}})
        with self.assertRaises(SchemaError):
            validate(1, {"$ref": "https://elsewhere/schema"})

    def test_boolean_schemas_and_is_valid(self):
        self.assertTrue(is_valid(1, True))
        self.assertFalse(is_valid(1, False))
        self.assertTrue(is_valid({"a": 1}, {"properties": {"a": True}}))


if __name__ == "__main__":
    unittest.main()
