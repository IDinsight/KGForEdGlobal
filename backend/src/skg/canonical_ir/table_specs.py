"""This module contains table specifications for the canonical IR."""

# Standard Library
import re

from dataclasses import dataclass
from typing import Sequence


# Table specifications.
@dataclass(frozen=True)
class TableSpec:
    """Specification for matching and interpreting a curriculum table."""

    name: str

    # Matching hints.
    required_caption_terms: tuple[str, ...] = ()
    required_header_terms: tuple[str, ...] = ()
    required_local_code_prefixes: tuple[str, ...] = ()

    # Interpretation.
    expectation_col: int | None = None  # e.g., "Specific competences"
    forward_fill_cols: tuple[int, ...] = ()
    group_col: int | None = None  # e.g., "Main competences"
    split_expectations: bool = True
    subject_col: int | None = None

    def match(
        self,
        *,
        caption_text: str | None,
        header_texts: Sequence[str],
        local_code: str | None,
    ) -> bool:
        """Check if the table matches this specification.

        Parameters
        ----------
        caption_text
            The caption text of the table.
        header_texts
            The header texts of the table.
        local_code
            The local code associated with the table.

        Returns
        -------
        bool
            True if the table matches the specification, False otherwise.
        """

        h = " | ".join([t.casefold() for t in header_texts])
        c = (caption_text or "").casefold()
        lc = (local_code or "").casefold()

        if self.required_local_code_prefixes:
            # If this spec relies on local-code prefixes, local_code must be present.
            if not local_code:
                return False
            if not any(
                lc.startswith(p.casefold()) for p in self.required_local_code_prefixes
            ):
                return False

        for term in self.required_header_terms:
            tt = term.casefold()

            # Token-ish match: avoids matching "main" inside "mainstream", etc.
            if not re.search(rf"(^|[^a-z0-9]){re.escape(tt)}([^a-z0-9]|$)", h):
                return False

        for term in self.required_caption_terms:
            if term.casefold() not in c:
                return False

        return True


TANZANIA_TABLE_SPECS: list[TableSpec] = [
    TableSpec(
        expectation_col=3,
        forward_fill_cols=(0, 1, 2),  # SN, Subject, Main competences often span
        group_col=2,
        name="tanzania_table_4_subject_main_specific",
        required_header_terms=("subject", "main", "specific"),
        required_local_code_prefixes=("table 4",),
        split_expectations=True,
        subject_col=1,
    ),
]
