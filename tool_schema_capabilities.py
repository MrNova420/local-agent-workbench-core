"""Capability inspection for the JSON Schema subset used to validate tool calls."""
from __future__ import annotations

from typing import Any


SUPPORTED_SCHEMA_KEYWORDS = frozenset({
    "$ref", "$schema", "$id", "$comment", "$defs", "definitions", "title", "description",
    "default", "examples", "deprecated", "readOnly", "writeOnly", "type", "enum", "const",
    "allOf", "anyOf", "oneOf", "not", "required", "properties", "patternProperties",
    "additionalProperties", "additionalItems", "items", "minLength", "maxLength", "pattern",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minItems",
    "maxItems", "uniqueItems", "minProperties", "maxProperties",
})
SUPPORTED_SCHEMA_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})


def unsupported_schema_features(schema: Any, *, max_depth: int = 64, max_nodes: int = 10_000) -> list[str]:
    """Return a bounded, deterministic list of schema features not validated by the engine.

    Tool schemas come from MCP servers and are untrusted input. This walk never
    resolves references or executes patterns, and stops at fixed depth/node limits.
    """
    issues: set[str] = set()
    seen: set[int] = set()
    visited = 0

    def visit(value: Any, path: str, depth: int) -> None:
        nonlocal visited
        if depth > max_depth:
            issues.add("schema nesting beyond the supported inspection depth")
            return
        if isinstance(value, dict):
            identity = id(value)
            if identity in seen:
                return
            seen.add(identity)
            visited += 1
            if visited > max_nodes:
                issues.add("schema larger than the supported inspection size")
                return
            for key, child in value.items():
                if not isinstance(key, str):
                    issues.add(f"{path}: non-string keyword")
                    continue
                if key not in SUPPORTED_SCHEMA_KEYWORDS:
                    issues.add(f"{path}.{key}: unsupported keyword")
                if key == "type":
                    types = child if isinstance(child, list) else [child]
                    for schema_type in types:
                        if not isinstance(schema_type, str) or schema_type not in SUPPORTED_SCHEMA_TYPES:
                            issues.add(f"{path}.type: unsupported type {schema_type!r}")
                elif key == "$ref" and (not isinstance(child, str) or (child != "#" and not child.startswith("#/"))):
                    issues.add(f"{path}.$ref: only local references are supported")
                if isinstance(child, (dict, list)):
                    visit(child, f"{path}.{key}", depth + 1)
        elif isinstance(value, list):
            identity = id(value)
            if identity in seen:
                return
            seen.add(identity)
            visited += 1
            if visited > max_nodes:
                issues.add("schema larger than the supported inspection size")
                return
            for index, child in enumerate(value):
                if isinstance(child, (dict, list)):
                    visit(child, f"{path}[{index}]", depth + 1)

    if not isinstance(schema, dict):
        return ["input schema is not an object"]
    visit(schema, "$", 0)
    return sorted(issues)
