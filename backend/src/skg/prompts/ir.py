"""This module contains prompt templates for extracting intermediate representation
information from page images.
"""

# Standard Library
from textwrap import dedent

# Third Party Library
from dotmap import DotMap


def stage1_extraction_prompts(*, page_index: int) -> DotMap:
    """Generate the prompts for Stage 1: Atomic Page Extraction.

    Parameters
    ----------
    page_index
        The 0-based page index of the image being processed.

    Returns
    -------
    DotMap
        A DotMap containing 'system_message' and 'user_message'.
    """

    system_message = dedent(
        """You are a high-fidelity curriculum digitization agent. Your goal is to convert a single curriculum page image into a strict PageIR JSON object.
You provide raw visual evidence of the page's structure and contents without attempting to resolve the full document hierarchy across multiple pages.

## HARD RULES (DO NOT VIOLATE)
1. NO HALLUCINATION: Extract only what is visually present. Do not complete sentences that are cut off at the page edge. Do not invent row/column data if a table is truncated.
2. READING ORDER: Populate the `items` list in strict visual reading order (top-to-bottom, left-to-right).
3. VERBATIM TEXT: Extract original text exactly as it appears. Do not translate or summarize.
4. LOCAL IDs: Generate unique IDs based on the page and sequence (e.g., "p5_i0", "p5_i1").
5. COORDINATE SPACE: Use `coord_space="px"`. All bounding boxes must refer to the pixel dimensions of the provided image.

## STRUCTURAL BOUNDARY DETECTION (CRITICAL)
You must explicitly identify items that "bleed" across page boundaries:
- TABLE BORDERS:
    - If a table has NO visible top border line, set `boundary="resumed"`.
    - If a table has NO visible bottom border line (ends "open"), set `boundary="truncated"`.
- TEXT CONTINUITY:
    - If a block ends without terminal punctuation (., ?, !) or ends with a hyphen (-), set `boundary="truncated"`.
    - If a page starts with text that is clearly a continuation (e.g., starts with a lowercase letter or mid-sentence flow), set `boundary="resumed"`.
- PAGE STATE: Update the `boundary_state` of the PageIR object to reflect if the page is "standalone", "to_next", "from_prev", or "both".

## LAYOUT-SPECIFIC INSTRUCTIONS
- TABLES: Preserve the exact grid structure. Note visual styles (bold/italic). Set `has_header_row=true` if the first row appears to be a header (bold text or shaded background).
- FLOW: Differentiate between "heading", "paragraph", and "list".
- CODES vs MARKERS:
    - Use `local_code` for explicit section numbering on Blocks (e.g., "3.9.4.1", "Section A").
    - Use `ListItem.marker` for bullet points or list enumeration (e.g., "•", "1.", "a)").
- STYLES: Note if text is **BOLD** or *ITALIC* as this signals normative standards vs. instructional guidance.

Output ONLY valid JSON.
        """
    )

    user_message = dedent(
        f"""Extract PageIR for page_index={page_index}.

Specific requirements for this page:
1. COORDINATE SPACE: Use Pixel (px) based on the image dimensions.
2. LANGUAGE: Detect the language per item (e.g., 'en', 'sw', 'fr').
3. TASK: Identify all tables and text blocks. Determine if the table at the bottom is 'open' (truncated) or if the text at the top resumes from a previous page.
4. CODES: Verbatim capture of local codes (e.g. 3.9.4.1) in the `local_code` field for Blocks/Tables.
5. BOUNDARIES: Set the correct `boundary` status for every item and the `boundary_state` for the whole page.
        """
    )

    return DotMap(
        {"system_message": system_message.strip(), "user_message": user_message.strip()}
    )
