"""This module contains the prompts used for chat."""

# Standard Library
from textwrap import dedent

# Third Party Library
from dotmap import DotMap


def summarize_chat_history(*, conversation: str) -> DotMap:
    """Summarize the chat history.

    Parameters
    ----------
    conversation
        The conversation to summarize.

    Returns
    -------
    DotMap
        A dictionary containing the system message and the user message.
    """

    system_message = None
    user_message = dedent(
        f"""Summarize the following conversation to be used as a prompt for continuing the conversation later:

{conversation}
"""
    )

    return DotMap(
        {"system_message": system_message, "user_message": user_message.strip()}
    )
