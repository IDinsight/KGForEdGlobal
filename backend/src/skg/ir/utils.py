"""This module contains utility functions for Intermediate Representation (IR) package."""

# Standard Library
from typing import Any

# Package Library
from skg.ir.schemas import DocumentIR, PageIR


def document_ir_json_schema(strict: bool = True) -> dict[str, Any]:
    """Get the JSON schema for the DocumentIR model.

    Parameters
    ----------
    strict
        Whether to enforce strictness (i.e., no additional properties).

    Returns
    -------
    dict[str, Any]
        The JSON schema for the DocumentIR model.
    """

    schema = DocumentIR.model_json_schema()
    return make_schema_strict(schema) if strict else schema


def make_schema_strict(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively enforce `additionalProperties: false` for object schemas, without
    clobbering schemas that already specify additionalProperties (e.g., dict/map fields
    that intentionally allow arbitrary keys).

    This function prevents the extractor from inventing arbitrary fields not defined in
    the schema and forces the extraction to stay within the expected IR shape so that
    downstream components can rely on a stable contract.

    Parameters
    ----------
    schema
        The JSON schema to make strict.

    Returns
    -------
    dict[str, Any]
        The strict JSON schema.
    """

    def _walk(node: Any) -> Any:
        if isinstance(node, list):
            return [_walk(x) for x in node]

        if not isinstance(node, dict):
            return node

        # Recurse into common schema containers.
        for key in ("properties", "$defs", "definitions"):
            if key in node and isinstance(node[key], dict):
                node[key] = {k: _walk(v) for k, v in node[key].items()}

        for key in ("items", "additionalProperties"):
            if key in node:
                node[key] = _walk(node[key])

        for key in ("anyOf", "oneOf", "allOf"):
            if key in node and isinstance(node[key], list):
                node[key] = [_walk(x) for x in node[key]]

        # Enforce strictness for objects when not explicitly set.
        is_object = node.get("type") == "object" or "properties" in node
        if is_object and "additionalProperties" not in node:
            node["additionalProperties"] = False

        return node

    # Work on a shallow copy; nested dicts are rewritten by _walk anyway.
    return _walk(dict(schema))


def page_ir_json_schema(strict: bool = True) -> dict[str, Any]:
    """Get the JSON schema for the PageIR model.

    Parameters
    ----------
    strict
        Whether to enforce strictness (i.e., no additional properties).

    Returns
    -------
    dict[str, Any]
        The JSON schema for the PageIR model.
    """

    schema = PageIR.model_json_schema()
    return make_schema_strict(schema) if strict else schema
