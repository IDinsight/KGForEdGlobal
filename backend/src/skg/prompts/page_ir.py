"""This module contains prompt templates for extracting intermediate representation
information from page images.
"""

# Standard Library
from textwrap import dedent
from typing import Optional

# Third Party Library
from dotmap import DotMap


def extract_page_ir_from_pdf_age(
    *,
    country: str,
    image_height: int,
    image_width: int,
    languages: list[str],
    page_index: int,
    text_layer_hints: Optional[str] = None,
    year: Optional[int] = None,
) -> DotMap:
    """Generate the prompts for extracting PageIR from a page image.

    Parameters
    ----------
    country
        The country of origin for the document.
    image_height
        The height of the image in pixels.
    image_width
        The width of the image in pixels.
    languages
        List of expected languages (e.g., ['en', 'sw']).
    page_index
        The 0-based page index of the image being processed.
    text_layer_hints
        Optional text layer hint extracted from the PDF.
    year
        The document year, if known.

    Returns
    -------
    DotMap
        A DotMap containing 'system_message' and 'user_message'.
    """

    doc_context = f"from {country}" + (f" ({year})" if year else "")
    lang_context = ", ".join(languages)
    text_layer_context = (
        "" if not text_layer_hints else f"## TEXT LAYER HINTS\n{text_layer_hints}"
    )

    system_message = dedent(
        f"""You are a high-fidelity curriculum digitization agent. Convert the image {doc_context} into a strict PageIR JSON object.

## DOCUMENT CONTEXT
- **Expected Languages**: {lang_context}. Use BCP-47 codes. Use "und" if unknown; "mul" if mixed.

{text_layer_context}

## HARD RULES
1. **KIND DISCRIMINATOR**: Every item in the `items` list MUST have a `"kind"` field set to either `"block"` or `"table"`.
2. **TABLE CELL STRUCTURE**: The `text` field inside a `TableCell` is an OBJECT (TextUnit).
   - Correct: `"text": {{"text": "content", "language": "en"}}`
   - Incorrect: `"text": "content"`
   - Empty: If a cell is blank, set `"text": null`.
3. **READING ORDER**: Populate `items` in visual reading order (left-to-right columns, then down).
4. **COORDINATES**: Use pixel coordinates (px) relative to {image_width}x{image_height}.
5. **VERBATIM**: Extract text exactly as seen. Do not fix typos or complete truncated sentences.
6. **BBOX REQUIRED**: Every block/table MUST include a localized bbox [x0,y0,x1,y1]. Never omit it.
7. Every item MUST include a non-null bbox localized to that item (no full-page placeholders).
8. Do not output empty tables; if you see a table, it must have at least one row.

## BLOCK CLASSIFICATION
- **artifact**: Headers, footers, page numbers.
- **caption**: Labels for tables/figures (e.g., "Table 1").
- **heading**: Section titles.
- **list**: Bulleted/numbered items (use `list_items`, set `text=null`).
- **paragraph**: Prose (use `text`, set `list_items=null`).

## BOUNDARIES
- Item `boundary`: Use "resumed" (top missing), "truncated" (bottom missing), or "complete".
- Page `boundary_state`: Use "from_prev", "to_next", "both", or "standalone".
        """
    )

    user_message = dedent(
        f"""Extract PageIR for page_index={page_index}.

Requirements:
1. Identify blocks and tables from the {image_width}x{image_height} image.
2. Set "kind": "block" or "kind": "table" for every entry.
3. Use "und" for unknown languages.
4. If a table continues from a previous page and shows its headers again, set "repeats_header": true.
        """
    )

    return DotMap(
        {"system_message": system_message.strip(), "user_message": user_message.strip()}
    )
