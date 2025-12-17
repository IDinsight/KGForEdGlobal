"""This module contains the prompts used to extract information for the intermediate
representation.
"""

# Standard Library
from textwrap import dedent

# Third Party Library
from dotmap import DotMap


def extract_page_ir_info() -> DotMap:
    """Get the prompt messages for extracting PageIR information.

    Returns
    -------
    DotMap
        A dictionary containing the system message and the user message.
    """

    system_message = dedent(
        """You extract curriculum structure into PageIR. Do not guess."""
    )
    user_message = dedent(
        """Extract hierarchy nodes (grade/stage/subject/theme/topic/etc.) and statements. 
Use role=expectation for normative outcomes/competences. 
Use performance_descriptor for benchmarks/expected standards. 
Use guidance for activities/teacher notes. 

If unsure, omit and add a warning.

refs must be unique on this page (e.g., 'n0', 'n1', 's0', 's1').
        """
    )

    return DotMap(
        {"system_message": system_message.strip(), "user_message": user_message.strip()}
    )
