"""Capability inspection for the JSON Schema subset used to validate tool calls."""
from __future__ import annotations

from typing import Any


SUPPORTED_SCHEMA_KEYWORDS = frozenset({
    "$ref", "$schema", "$id", "$comment", "$defs", "definitions", "title", "description",
    "default", "examples", "deprecated", "readOnly", "writeOnly", "type", "enum", "const",
    "allOf", "anyOf", "oneOf", "not", "if", "then", "else", "required", "dependentRequired",
    "dependentSchemas", "properties", "patternProperties", "propertyNames",
    "additionalProperties", "additionalItems", "items", "format", "minLength", "maxLength", "pattern",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minItems",
    "maxItems", "uniqueItems", "contains", "minContains", "maxContains", "prefixItems",
    "minProperties", "maxProperties",
})
SUPPORTED_SCHEMA_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})


def unsupported_schema_features(schema: Any, *, max_depth: int = 64, max_nodes: int = 10_000) -> list[str]:
    """Return a bounded, deterministic list of schema features not validated by the engine.

    Tool schemas come from MCP servers and are untrusted input. This walk never
    resolves references or executes patterns, and stops at fixed depth/node limits.
    JSON Schema ``format`` remains an annotation, as it is under the default
    specification behavior; this does not assert that a formatted value is valid.
    """
    issues: set[str] = set()
    seen: set[int] = set()
    visited = 0

    schema_maps = {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}
    schema_singletons = {
        "additionalProperties", "additionalItems", "not", "if", "then", "else",
        "propertyNames", "contains",
    }
    schema_arrays = {"allOf", "anyOf", "oneOf", "prefixItems"}

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
                            issues.add(f"{path}.type: unsupported JSON Schema type {schema_type!r}")
                elif key == "$ref" and (not isinstance(child, str) or (child != "#" and not child.startswith("#/"))):
                    issues.add(f"{path}.$ref: external or invalid reference; only local references are supported")
                # Descend only where JSON Schema defines a value as a schema.
                # `properties` and `$defs` keys are user-selected names (such
                # as `path` or `content`), not schema keywords. Annotation and
                # example values can also be arbitrary JSON and must not be
                # interpreted as nested schemas.
                if key in schema_maps and isinstance(child, dict):
                    for name, nested_schema in child.items():
                        if isinstance(nested_schema, (dict, list)):
                            visit(nested_schema, f"{path}.{key}.{name}", depth + 1)
                elif key in schema_singletons and isinstance(child, (dict, list)):
                    visit(child, f"{path}.{key}", depth + 1)
                elif key == "items":
                    if isinstance(child, dict):
                        visit(child, f"{path}.{key}", depth + 1)
                    elif isinstance(child, list):
                        for index, nested_schema in enumerate(child):
                            if isinstance(nested_schema, (dict, list)):
                                visit(nested_schema, f"{path}.{key}[{index}]", depth + 1)
                elif key in schema_arrays and isinstance(child, list):
                    for index, nested_schema in enumerate(child):
                        if isinstance(nested_schema, (dict, list)):
                            visit(nested_schema, f"{path}.{key}[{index}]", depth + 1)
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
