"""This module contains utility functions for Intermediate Representation (IR) package."""

# Standard Library
import json

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

# Package Library
from skg.ir.schemas import DocumentIR, PageIR
from skg.utils.general import make_dir, write_text


@dataclass
class ContinuityState:
    """Dataclass for cross-page continuity state."""

    active_parent_ref: Optional[str] = None
    active_path: list[str] = field(default_factory=list)

    # Maps raw refs like "n12" -> "p0007:n12".
    recent_raw_ref_map: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractionDirs:
    """Dataclass for extraction directories."""

    root: Path
    artifacts: Path
    page_images: Path
    page_ir: Path


def continuity_fp(extraction_dirs: ExtractionDirs) -> Path:
    """Get the file path for the continuity state JSON.

    Parameters
    ----------
    extraction_dirs
        The extraction directories.

    Returns
    -------
    Path
        The file path for the continuity state JSON.
    """

    return extraction_dirs.root / "continuity_state.json"


def create_extraction_dirs(*, doc_key: str, output_dir: Path) -> ExtractionDirs:
    """Create extraction directories for a given document key.

    Parameters
    ----------
    doc_key
        The document key.
    output_dir
        The output directory root.

    Returns
    -------
    ExtractionDirs
        The created extraction directories.
    """

    root = output_dir / doc_key
    artifacts = root / "artifacts"
    page_images = root / "page_images"
    page_ir = root / "page_ir"

    for p in [root, page_images, page_ir, artifacts]:
        make_dir(p)

    return ExtractionDirs(
        root=root, artifacts=artifacts, page_images=page_images, page_ir=page_ir
    )


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


def load_continuity_state(extraction_dirs: ExtractionDirs) -> ContinuityState:
    """Load the continuity state from file.

    Parameters
    ----------
    extraction_dirs
        The extraction directories.

    Returns
    -------
    ContinuityState
        The loaded continuity state.
    """

    fp = continuity_fp(extraction_dirs)
    if not fp.exists():
        return ContinuityState()
    return ContinuityState(**json.loads(fp.read_text("utf-8")))


def make_schema_strict(  # pylint:disable=too-complex
    schema: dict[str, Any],
) -> dict[str, Any]:
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
        """Recursively walk the schema node and enforce strictness.

        Parameters
        ----------
        node
            The current schema node.

        Returns
        -------
        Any
            The processed schema node.
        """

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


def save_continuity_state(
    extraction_dirs: ExtractionDirs, state: ContinuityState
) -> None:
    """Save the continuity state to disk.

    Parameters
    ----------
    extraction_dirs
        The extraction directories.
    state
        The continuity state to save.
    """

    write_text(continuity_fp(extraction_dirs), json.dumps(asdict(state), indent=2))
