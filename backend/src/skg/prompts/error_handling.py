"""This module contains the prompts for error handling."""

# Standard Library
from textwrap import dedent

# Third Party Library
from dotmap import DotMap


def error_correction(*, error_info_str: str) -> DotMap:
    """Generates a prompt for error correction.

    Parameters
    ----------
    error_info_str
        A string containing the error information that needs to be corrected.

    Returns
    -------
    DotMap
        A dictionary containing the system message and the user message.
    """

    system_message = None
    user_message = dedent(
        f"""Your last message resulted in the following errors:

⚠️ **Error during response validation**

{error_info_str}

Please correct your response and try again.
        """
    )

    return DotMap(
        {"system_message": system_message, "user_message": user_message.strip()}
    )
