"""A small JSON Schema validator, standard library only.

Supported keywords: type (single or list; "integer" is an int that is not a bool, "number" accepts
int and float), enum, const, pattern, minLength, maxLength, required, properties,
additionalProperties (boolean or schema), items, minItems, maxItems, minimum, maximum, if/then/else,
and `$ref` to a local `#/$defs/<name>` or `#/definitions/<name>`. Anything else is ignored, so a
schema that relies on an unsupported keyword is not enforced here; keep the schemas inside this set.

`validate(instance, schema)` returns a list of error strings, each prefixed with the JSON path of the
offending value (`$` is the root). An empty list means the instance conforms.
"""
from __future__ import annotations

import re
from typing import Any

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


class SchemaError(ValueError):
    """The schema itself is malformed or references something unsupported."""


def _resolve_ref(ref: str, root: dict) -> dict:
    if not ref.startswith("#/"):
        raise SchemaError(f"only local refs are supported, got {ref!r}")
    node: Any = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise SchemaError(f"unresolvable ref {ref!r}")
        node = node[part]
    if not isinstance(node, dict):
        raise SchemaError(f"ref {ref!r} does not point at a schema object")
    return node


def _type_ok(value: Any, type_spec: Any) -> bool:
    types = type_spec if isinstance(type_spec, list) else [type_spec]
    for name in types:
        check = _TYPE_CHECKS.get(name)
        if check is None:
            raise SchemaError(f"unknown type {name!r}")
        if check(value):
            return True
    return False


def _validate(value: Any, schema: Any, path: str, root: dict, errors: list[str]) -> None:
    if schema is True:
        return
    if schema is False:
        errors.append(f"{path}: value not allowed")
        return
    if not isinstance(schema, dict):
        raise SchemaError(f"schema at {path} must be an object or boolean")
    if "$ref" in schema:
        schema = {**_resolve_ref(schema["$ref"], root), **{k: v for k, v in schema.items() if k != "$ref"}}

    if "type" in schema and not _type_ok(value, schema["type"]):
        errors.append(f"{path}: expected type {schema['type']}, got {type(value).__name__}")
        return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected the constant {schema['const']!r}")

    if isinstance(value, str):
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{path}: {value!r} does not match pattern {schema['pattern']!r}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} is above maximum {schema['maximum']}")

    if isinstance(value, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}: missing required property {name!r}")
        for name, sub in props.items():
            if name in value:
                _validate(value[name], sub, f"{path}.{name}", root, errors)
        extra = schema.get("additionalProperties", True)
        if extra is not True:
            for name in value:
                if name in props:
                    continue
                if extra is False:
                    errors.append(f"{path}: unexpected property {name!r}")
                else:
                    _validate(value[name], extra, f"{path}.{name}", root, errors)

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems {schema['minItems']}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than maxItems {schema['maxItems']}")
        if "items" in schema:
            for index, element in enumerate(value):
                _validate(element, schema["items"], f"{path}[{index}]", root, errors)

    if "if" in schema:
        probe: list[str] = []
        _validate(value, schema["if"], path, root, probe)
        branch = schema.get("then") if not probe else schema.get("else")
        if branch is not None:
            _validate(value, branch, path, root, errors)


def validate(instance: Any, schema: dict) -> list[str]:
    """Return the list of conformance errors for `instance` against `schema` (empty when valid)."""
    errors: list[str] = []
    _validate(instance, schema, "$", schema, errors)
    return errors


def is_valid(instance: Any, schema: dict) -> bool:
    return not validate(instance, schema)


__all__ = ["SchemaError", "is_valid", "validate"]
