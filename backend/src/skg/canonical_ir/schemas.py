"""This module contains schemas used for creating the canonical Intermediate
Representation (IR) from a single document IR.
"""

# Future Library
from __future__ import annotations

# Standard Library
from datetime import datetime, timezone
from typing import Any, Literal, Optional

# Third Party Library
from pydantic import BaseModel, ConfigDict, Field

# Package Library
from skg.utils.constants import StatementRole


# Schemas for primitives.
class BaseModelCanonicalIR(BaseModel):
    """Base model that enforces 'additionalProperties: false' in JSON schema for
    compatibility with OpenAI Structured Outputs.
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)


# Schemas for canonical IR.
class CanonicalEdge(BaseModelCanonicalIR):
    """A hierarchy edge in the canonical IR."""

    child_id: str
    parent_id: str
    rel: Literal["hasChild"] = "hasChild"


class CanonicalNode(BaseModelCanonicalIR):
    """A single semantic node in the curriculum hierarchy.

    NB: Do NOT include children nodes here---this is meant to be a flat hierarchy.
    """

    bbox: Optional[list[float]] = None
    body: Optional[str] = Field(None, description="Full normative text.")
    doc_key: str
    list_id: Optional[str] = Field(
        None, description="The alphanumeric code (e.g., '3.1.1')"
    )
    node_id: str = Field(..., description="Deterministic global UUID.")
    page_indices: list[int] = Field(default_factory=list)
    parent_id: Optional[str] = None
    role: StatementRole
    source_ids: list[str] = Field(
        default_factory=list, description="Pointers to segment keys."
    )
    title: Optional[str] = Field(None, description="Short title/heading text")


class NormalizedRow(BaseModelCanonicalIR):
    """A row where all spans are filled and cells are accessible by index."""

    cells: list[str]  # Text content of cells
    original_row_index: int
    provenance_bbox: list[float]
    row_index: int


class CanonicalIR(BaseModelCanonicalIR):
    """Represents a semantic, provenance-rich representation of a document."""

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    doc_key: str
    edges: list[CanonicalEdge] = Field(default_factory=list)
    pdf_name: Optional[str] = None
    nodes: list[CanonicalNode] = Field(default_factory=list)
    root_id: str
    unresolved: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
