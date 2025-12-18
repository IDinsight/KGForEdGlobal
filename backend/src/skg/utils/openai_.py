"""This module contains utilities for interacting with the OpenAI API."""

# Standard Library
from pathlib import Path

# Third Party Library
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_random_exponential

# Package Library
from skg.ir.schemas import PageIR
from skg.prompts.ir import extract_page_ir_info
from skg.schemas import Limits
from skg.utils.general import encode_png_to_data_url

limits = Limits(max_retry_attempts=5)
openai_client = OpenAI()


@retry(
    stop=stop_after_attempt(limits.max_retry_attempts),
    wait=wait_random_exponential(min=1, max=60),
)
def extract_page_ir(*, model: str, page_index: int, png_fp: Path) -> PageIR:
    """Extract PageIR from a page image using LLM + Vision + Structured Outputs. Uses
    OpenAI Responses API structured parsing into a Pydantic model. Image is passed as
    an input_image with a base64 data URL.

    NB:
    1. Uses OpenAI Responses API with `text.format.type="json_schema"` for structured
        outputs.
    2. Sends the PNG as an `input_image` with a base64 data URL.
    3. Provenance: The model is allowed to emit provenance pointers, but it should use
      placeholder doc_key/pdf_name (normalize_provenance will overwrite).

    Parameters
    ----------
    model
        The OpenAI model to use.
    page_index
        The 0-based page index.
    png_fp
        The PNG file path of the page image.

    Returns
    -------
    PageIR
        The extracted PageIR.
    """

    image_url = encode_png_to_data_url(png_fp)
    prompts = extract_page_ir_info(page_index=page_index)

    completion = openai_client.beta.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": prompts.system_message},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompts.user_message},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ],
        response_format=PageIR,
        temperature=0,
        top_p=1,
    )

    page_ir = completion.choices[0].message.parsed
    assert isinstance(
        page_ir, PageIR
    ), f"Expected PageIR, got {type(page_ir)}. {page_ir = }"
    page_ir.page_index = page_index
    return page_ir
