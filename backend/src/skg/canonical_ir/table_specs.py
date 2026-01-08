"""This module contains table specifications for the canonical IR."""

# Standard Library
import re

from dataclasses import dataclass
from typing import Sequence

# Package Library
from skg.utils.constants import StatementRole


@dataclass(frozen=True)
class TableSpec:
    """Specification for matching and interpreting a curriculum table.

    A `TableSpec``has two responsibilities:
        1. **MATCH**: decide whether a stitched TableSegment is this table kind.
        2. **INTERPRET**: define which columns map to which canonical roles.

    Attributes
    ----------
    caption_regex
        An optional regex string to validate the caption (case-insensitive).
    descriptor_col
        0-based index of the column containing Descriptor data.
    descriptor_role
        The canonical role for the descriptor column.
    expectation_col
        0-based index of the column containing Expectation/Competency data.
    expectation_role
        The canonical role for the expectation column.
    forward_fill_cols
        Column indices that should be forward-filled (merged cells handling).
    group_col
        0-based index of the column containing Group/Strand data.
    group_role
        The canonical role for the group column.
    ignore
        If True, the matched table is acknowledged but not converted to nodes/edges.
    name
        A unique identifier for this table specification.
    required_caption_any_terms
        Terms where **at least one** must appear in the caption for a match (OR logic).
    required_caption_terms
        Terms that **must all** appear in the caption for a match (AND logic).
        Case-insensitive.
    required_header_any_terms
        Terms where **at least one** must appear in the headers (OR logic).
    required_header_terms
        Terms that **must all** appear in the headers (AND logic). Matching is
        "token-ish" (whole word).
    required_local_code_prefixes
        Prefixes that the local table code must start with (e.g., "Table 4").
    split_descriptors
        Whether to split text in the descriptor column into multiple statements.
    split_expectations
        Whether to split text in the expectation column into multiple statements.
    subject_col
        0-based index of the column containing Subject data.
    subject_role
        The canonical role to assign to nodes extracted from the subject column.
    topic_col
        0-based index of the column containing Topic data.
    topic_role
        The canonical role for the topic column.
    """

    name: str

    # If True: matched table is acknowledged but not converted to nodes/edges.
    ignore: bool = False

    # Matching hints.

    # All-of/any-of caption/header checks keep specs simple but more resilient to
    # wording/formatting drift.
    caption_regex: str | None = None  # optional regex on caption (case-insensitive)
    required_caption_any_terms: tuple[str, ...] = ()  # OR: at least one must match
    required_caption_terms: tuple[str, ...] = ()  # AND: all must match
    required_header_any_terms: tuple[
        str, ...
    ] = ()  # OR: at least one must match (token-ish)
    required_header_terms: tuple[str, ...] = ()  # AND: all must match (token-ish)
    required_local_code_prefixes: tuple[str, ...] = ()

    # Interpretation

    # Structural columns.
    group_col: int | None = None
    subject_col: int | None = None
    topic_col: int | None = None

    # Leaf columns.
    descriptor_col: int | None = None
    expectation_col: int | None = None

    # Forward fill (structural columns frequently span/merge).
    forward_fill_cols: tuple[int, ...] = ()

    # Leaf parsing behavior.
    split_descriptors: bool = True
    split_expectations: bool = True

    # Role mapping.
    descriptor_role: StatementRole = StatementRole.DESCRIPTOR
    expectation_role: StatementRole = StatementRole.EXPECTATION
    group_role: StatementRole = StatementRole.STRAND
    subject_role: StatementRole = StatementRole.SUBJECT
    topic_role: StatementRole = StatementRole.TOPIC

    def _matches_local_code(self, local_code: str | None) -> bool:
        """Check if the local code starts with one of the required prefixes.

        Parameters
        ----------
        local_code
            The local identifier code (e.g., "Table 4").

        Returns
        -------
        bool
            True if the local code matches the required prefixes.
        """

        if not self.required_local_code_prefixes:
            return True

        if not local_code:
            return False

        lc_norm = local_code.casefold()

        return any(
            lc_norm.startswith(p.casefold()) for p in self.required_local_code_prefixes
        )

    def _matches_regex(self, caption_text: str | None) -> bool:
        """Check if the caption matches the optional regex.

        Parameters
        ----------
        caption_text
            The full text of the table caption (e.g., "Table 4.1:

        Returns
        -------
        bool
            True if the caption matches the regex or if no regex is defined.
        """

        if not self.caption_regex:
            return True

        if not caption_text:
            return False

        return bool(re.search(self.caption_regex, caption_text, flags=re.IGNORECASE))

    def _satisfies_terms(
        self, *, required_all: tuple[str, ...], required_any: tuple[str, ...], text: str
    ) -> bool:
        """Check if text satisfies the 'ALL' and 'ANY' term constraints.

        Parameters
        ----------
        required_all
            Terms that must ALL be present.
        required_any
            Terms where AT LEAST ONE must be present (if tuple is not empty).
        text
            The normalized text to check against.

        Returns
        -------
        bool
            True if all constraints are met.
        """

        # Check AND logic (All terms must be present).
        if not all(
            self._tokenish_contains(haystack=text, term=t) for t in required_all
        ):
            return False

        # Check OR logic (At least one term, if any are specified).
        if required_any:
            if not any(
                self._tokenish_contains(haystack=text, term=t) for t in required_any
            ):
                return False

        return True

    @staticmethod
    def _tokenish_contains(*, haystack: str, term: str) -> bool:
        """Check if ``term`` exists in ``haystack`` as a distinct token/phrase.
        Treats non-alphanumeric characters as boundaries to prevent partial matches
        (e.g., 'art' matching inside 'part').

        Parameters
        ----------
        haystack
            The text to search within.
        term
            The term to search for.

        Returns
        -------
        bool
            True if the term is found as a distinct token/phrase.
        """

        tt = (term or "").casefold().strip()
        if not tt:
            return True

        # Allow flexible whitespace in multi-word terms.
        tt_re = re.escape(tt).replace(r"\ ", r"\\s+")

        return re.search(rf"(^|[^a-z0-9]){tt_re}([^a-z0-9]|$)", haystack) is not None

    def match(
        self,
        *,
        caption_text: str | None,
        header_texts: Sequence[str],
        local_code: str | None,
    ) -> bool:
        """Return True if this table matches the spec. Matches are determined based on
        local code prefixes, header terms, and caption terms using the configured
        AND/OR logic.

        Parameters
        ----------
        caption_text
            The full text of the table caption (e.g., "Table 4.1: Physics").
        header_texts
            A sequence of strings representing the table headers.
        local_code
            The local identifier code (e.g., "Table 4").

        Returns
        -------
        bool
            True if the provided metadata matches the criteria in this spec.
        """

        # Normalize inputs.
        h_norm = " | ".join([t.casefold() for t in header_texts])
        c_norm = (caption_text or "").casefold()

        # Perform matching checks.
        if not self._matches_local_code(local_code):
            return False

        if not self._satisfies_terms(
            required_all=self.required_header_terms,
            required_any=self.required_header_any_terms,
            text=h_norm,
        ):
            return False

        if not self._satisfies_terms(
            required_all=self.required_caption_terms,
            required_any=self.required_caption_any_terms,
            text=c_norm,
        ):
            return False

        if not self._matches_regex(caption_text):
            return False

        return True
