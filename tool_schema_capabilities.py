"""Dialect-aware JSON Schema capability inspection for untrusted tool schemas."""
from __future__ import annotations

from typing import Any

from jsonschema import (
    Draft3Validator, Draft4Validator, Draft6Validator, Draft7Validator,
    Draft201909Validator, Draft202012Validator, validators,
)


_ANNOTATION_KEYWORDS = frozenset({
    "$schema", "$id", "$comment", "$anchor", "$dynamicAnchor", "$vocabulary",
    "$defs", "definitions", "then", "else",
    "title", "description", "default", "examples", "deprecated", "readOnly",
    "writeOnly", "minContains", "maxContains", "contentEncoding", "contentMediaType", "contentSchema",
})


def schema_validator_class(schema: dict[str, Any]):
    """Return a validator for a declared supported dialect, defaulting to 2020-12.

    An unknown explicit dialect is not treated as a different supported draft.
    """
    if not isinstance(schema, dict):
        return None
    if "$schema" not in schema:
        return Draft202012Validator
    validator = validators.validator_for(schema, default=None)
    # Only accept dialects implemented and reviewed by this adapter. A
    # globally registered third-party validator is not automatically trusted.
    return validator if validator in _VALIDATOR_CLASSES else None


_VALIDATOR_CLASSES = (
    Draft3Validator, Draft4Validator, Draft6Validator, Draft7Validator,
    Draft201909Validator, Draft202012Validator,
)
SUPPORTED_SCHEMA_KEYWORDS = frozenset().union(
    _ANNOTATION_KEYWORDS, *(set(validator.VALIDATORS) for validator in _VALIDATOR_CLASSES)
)


def unsupported_schema_features(schema: Any, *, max_depth: int = 64, max_nodes: int = 10_000) -> list[str]:
    """Find unsupported schema dialects/extensions and unsafe remote references.

    Standard assertion keywords are derived from the installed dialect-aware
    JSON Schema validators. References are limited to the current schema
    resource; the engine never fetches remote schemas from an MCP server.
    """
    if not isinstance(schema, dict):
        return ["input schema is not an object"]
    validator = schema_validator_class(schema)
    if validator is None:
        return [f"$: unsupported JSON Schema dialect {schema.get('$schema')!r}"]

    known_keywords = set(validator.VALIDATORS).union(_ANNOTATION_KEYWORDS)
    issues: set[str] = set()

    declared_vocabularies = schema.get("$vocabulary")
    if declared_vocabularies is not None:
        if not isinstance(declared_vocabularies, dict) or any(
            not isinstance(uri, str) or not isinstance(required, bool)
            for uri, required in declared_vocabularies.items()
        ):
            issues.add("$.$vocabulary: invalid vocabulary declaration")
        else:
            dialect_vocabularies = validator.META_SCHEMA.get("$vocabulary", {})
            for uri, required in declared_vocabularies.items():
                if required and uri not in dialect_vocabularies:
                    issues.add(f"$.$vocabulary: required vocabulary {uri!r} is unsupported")
    seen: set[int] = set()
    visited = 0

    schema_maps = {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}
    schema_singletons = {
        "additionalProperties", "additionalItems", "unevaluatedProperties", "unevaluatedItems",
        "not", "if", "then", "else", "propertyNames", "contains", "contentSchema",
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
                if key not in known_keywords:
                    issues.add(f"{path}.{key}: unsupported keyword")
                if key == "type":
                    types = child if isinstance(child, list) else [child]
                    for schema_type in types:
                        if not isinstance(schema_type, str) or schema_type not in {
                            "object", "array", "string", "integer", "number", "boolean", "null",
                        }:
                            issues.add(f"{path}.type: unsupported JSON Schema type {schema_type!r}")
                elif key in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                    not isinstance(child, str) or not child.startswith("#")
                ):
                    issues.add(f"{path}.{key}: external or invalid reference; remote resolution is disabled")

                # Only descend where the selected JSON Schema dialect defines
                # a value as a schema. Property names and annotation data are
                # user-selected JSON, not schema keywords.
                if key in schema_maps and isinstance(child, dict):
                    for name, nested in child.items():
                        if isinstance(nested, (dict, list)):
                            visit(nested, f"{path}.{key}.{name}", depth + 1)
                elif key in schema_singletons and isinstance(child, (dict, list)):
                    visit(child, f"{path}.{key}", depth + 1)
                elif key == "items":
                    if isinstance(child, dict):
                        visit(child, f"{path}.{key}", depth + 1)
                    elif isinstance(child, list):
                        for index, nested in enumerate(child):
                            if isinstance(nested, (dict, list)):
                                visit(nested, f"{path}.{key}[{index}]", depth + 1)
                elif key in schema_arrays and isinstance(child, list):
                    for index, nested in enumerate(child):
                        if isinstance(nested, (dict, list)):
                            visit(nested, f"{path}.{key}[{index}]", depth + 1)
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

    visit(schema, "$", 0)
    return sorted(issues)
