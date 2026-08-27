"""This module defines the model registry for the backend.

The model settings registry maps provider prefixes (e.g., "anthropic", "openai") to
factory functions that generate the appropriate settings for various agents in the
pipeline. This design allows us to keep provider-specific configuration logic
encapsulated and easily extendable without cluttering the agent code.
"""

# Future Library
from __future__ import annotations

# Standard Library
from typing import Any, Callable, Literal, Type

# Third Party Library
from pydantic import BaseModel
from pydantic_ai.models.anthropic import AnthropicModelSettings
from pydantic_ai.models.openai import OpenAIResponsesModelSettings
from pydantic_ai.output import OutputSpec, PromptedOutput
from pydantic_ai.settings import ModelSettings

# Package Library
from kgfeg.schemas import BaseSchema

# Anthropic models that support adaptive thinking ({"type": "adaptive"}). Adaptive
# thinking is a 4.6+ feature; 4.5-generation models (e.g. claude-haiku-4-5) reject it
# with a 400 error and must use extended thinking with an explicit token budget
# instead. NB: extended thinking is removed on opus-4-7+, so any new flagship model
# MUST be added here, otherwise it falls back to extended thinking and is rejected.
_ADAPTIVE_THINKING_MODEL_NAMES: frozenset[str] = frozenset(
    {
        "claude-opus-4-6",
        "claude-opus-4-7",
        "claude-opus-4-8",
        "claude-opus-5",
        "claude-sonnet-4-6",
    }
)


def _anthropic_kgs_settings(
    *, config: ModelConfig, type_: str
) -> AnthropicModelSettings:
    """Build Anthropic settings for knowledge graph agents.

    Parameters
    ----------
    config
        The ModelConfig instance containing the provider-agnostic model configuration.
    type_
        Specifies the type of the knowledge graph agent for which to retrieve settings.
        Expected values are either: "learning_components" or "learning_progressions".

    Returns
    -------
    AnthropicModelSettings
        The configured AnthropicModelSettings for knowledge graph agents.
    """

    if type_ == "learning_components":
        return _anthropic_thinking_settings(config)

    return _anthropic_thinking_settings(config)


def _anthropic_page_ir_extraction_settings(
    *, config: ModelConfig, type_: str
) -> AnthropicModelSettings:
    """Build Anthropic settings for page IR extraction agents.

    Parameters
    ----------
    config
        The ModelConfig instance containing the provider-agnostic model configuration.
    type_
        Specifies the type of the page IR extraction agent for which to retrieve
        settings. Expected values are either: "extraction" or "validation".

    Returns
    -------
    AnthropicModelSettings
        The configured AnthropicModelSettings for page IR extraction agents.
    """

    if type_ == "extraction":
        return _anthropic_thinking_settings(config)

    return _anthropic_thinking_settings(config)


def _anthropic_page_ir_verification_settings(
    *, config: ModelConfig, type_: str
) -> AnthropicModelSettings:
    """Build Anthropic settings for page IR verification agents.

    Parameters
    ----------
    config
        The ModelConfig instance containing the provider-agnostic model configuration.
    type_
        Specifies the type of the page IR verification agent for which to retrieve
        settings. Expected values are either: "verification" or "validation".

    Returns
    -------
    AnthropicModelSettings
        The configured AnthropicModelSettings for page IR verification agents.
    """

    if type_ == "verification":
        return _anthropic_thinking_settings(config)

    return _anthropic_thinking_settings(config)


def _anthropic_thinking_settings(config: ModelConfig) -> AnthropicModelSettings:
    """Build the Anthropic thinking-related settings for one model.

    Adaptive thinking capable models use {"type": "adaptive"} tuned by
    `anthropic_effort`. Older models (e.g. claude-haiku-4-5) fall back to extended
    thinking with an explicit token budget, which 4.5-generation Anthropic models
    accept. The extended-thinking budget must stay below the request `max_tokens`.

    Parameters
    ----------
    config
        The ModelConfig instance containing the provider-agnostic model configuration.

    Returns
    -------
    dict[str, Any]
        Keyword arguments to merge into an AnthropicModelSettings construction.
    """

    if config.supports_adaptive_thinking:
        return AnthropicModelSettings(
            anthropic_thinking={"type": "adaptive"},
            anthropic_effort=config.anthropic_effort,
            max_tokens=config.max_output_tokens,
        )

    return AnthropicModelSettings(
        anthropic_thinking={
            "budget_tokens": config.anthropic_thinking_budget_tokens,
            "type": "enabled",
        },
        max_tokens=config.max_output_tokens,
    )


def _openai_kgs_settings(
    *, config: ModelConfig, type_: str
) -> OpenAIResponsesModelSettings:
    """Build OpenAI settings for knowledge graph agents.

    Parameters
    ----------
    config
        The ModelConfig instance containing the provider-agnostic model configuration.
    type_
        Specifies the type of the knowledge graph agent for which to retrieve settings.
        Expected values are either: "learning_components" or "learning_progressions".

    Returns
    -------
    OpenAIResponsesModelSettings
        The configured OpenAIResponsesModelSettings for knowledge graph agents.
    """

    if type_ == "learning_components":
        return OpenAIResponsesModelSettings(
            max_tokens=config.max_output_tokens,
            openai_reasoning_effort=config.openai_reasoning_effort,
            openai_reasoning_summary="detailed",
        )

    return OpenAIResponsesModelSettings(
        max_tokens=config.max_output_tokens,
        openai_reasoning_effort=config.openai_reasoning_effort,
        openai_reasoning_summary="detailed",
    )


def _openai_page_ir_extraction_settings(
    *, config: ModelConfig, type_: str
) -> OpenAIResponsesModelSettings:
    """Build OpenAI settings for page IR extraction agents.

    Parameters
    ----------
    config
        The ModelConfig instance containing the provider-agnostic model configuration.
    type_
        Specifies the type of the page IR extraction agent for which to retrieve
        settings. Expected values are either: "extraction" or "validation".

    Returns
    -------
    OpenAIResponsesModelSettings
        The configured OpenAIResponsesModelSettings for page IR extraction agents.
    """

    if type_ == "extraction":
        return OpenAIResponsesModelSettings(
            max_tokens=config.max_output_tokens,
            temperature=config.openai_temperature,
            top_p=config.openai_top_p,
        )

    return OpenAIResponsesModelSettings(
        max_tokens=config.max_output_tokens,
        openai_reasoning_effort=config.openai_reasoning_effort,
        openai_reasoning_summary="detailed",
    )


def _openai_page_ir_verification_settings(
    *, config: ModelConfig, type_: str
) -> OpenAIResponsesModelSettings:
    """Build OpenAI settings for page IR verification agents.

    Parameters
    ----------
    config
        The ModelConfig instance containing the provider-agnostic model configuration.
    type_
        Specifies the type of the page IR verification agent for which to retrieve
        settings. Expected values are either: "verification" or "validation".

    Returns
    -------
    OpenAIResponsesModelSettings
        The configured OpenAIResponsesModelSettings for page IR verification agents.
    """

    if type_ == "verification":
        return OpenAIResponsesModelSettings(
            max_tokens=config.max_output_tokens,
            temperature=config.openai_temperature,
            top_p=config.openai_top_p,
        )

    return OpenAIResponsesModelSettings(
        max_tokens=config.max_output_tokens,
        openai_reasoning_effort=config.openai_reasoning_effort,
        openai_reasoning_summary="detailed",
    )


_KGS_SETTINGS_REGISTRY: dict[str, Callable[..., Any]] = {
    "anthropic": _anthropic_kgs_settings,
    "openai": _openai_kgs_settings,
}
_PAGE_IR_EXTRACTION_SETTINGS_REGISTRY: dict[str, Callable[..., Any]] = {
    "anthropic": _anthropic_page_ir_extraction_settings,
    "openai": _openai_page_ir_extraction_settings,
}
_PAGE_IR_VERIFICATION_SETTINGS_REGISTRY: dict[str, Callable[..., Any]] = {
    "anthropic": _anthropic_page_ir_verification_settings,
    "openai": _openai_page_ir_verification_settings,
}


class ModelConfig(BaseSchema):
    """Provider-agnostic model configuration.

    The `model` string uses pydantic-ai's `provider:model-name` convention.
    Provider-specific knobs live here with sensible defaults; the registry
    functions above pick the ones relevant to each provider.
    """

    model: str

    # Shared settings.
    max_output_tokens: int = 32768

    # Anthropic settings.
    anthropic_effort: Literal["low", "medium", "high"] = "high"
    anthropic_thinking_budget_tokens: int = 16384

    # OpenAI settings.
    openai_reasoning_effort: Literal["low", "medium", "high"] = "high"
    openai_temperature: float = 0.0
    openai_top_p: float = 0.95

    def kgs_settings(self, type_: str) -> ModelSettings:
        """Resolve the correct model settings for knowledge graph agents.

        Parameters
        ----------
        type_
            Specifies the type of knowledge graph agent for which to retrieve settings.
            Expected values are either: "learning_components" or
            "learning_progressions".

        Returns
        -------
        ModelSettings
            The configured ModelSettings for the specified provider.

        Raises
        ------
        ValueError
            If an invalid `type_` is provided.
        """

        if type_ not in {
            "learning_components",
            "learning_progressions",
            "sfi_dedup",
            "sfi_has_child",
            "sfi_extraction",
        }:
            raise ValueError(
                f"Invalid knowledge graph model type '{type_}'. "
                f"Valid options are: "
                f"'learning_components', "
                f"'learning_progressions', "
                f"`sfi_dedup`, "
                f"`sfi_has_child`, "
                f"or `sfi_extraction`."
            )

        factory = _KGS_SETTINGS_REGISTRY[self.provider]
        return factory(config=self, type_=type_)

    def page_ir_extraction_settings(self, type_: str) -> ModelSettings:
        """Resolve the correct model settings for page IR extraction agents.

        Parameters
        ----------
        type_
            Specifies the type of page IR extraction agent for which to retrieve
            settings. Expected values are either: "extraction" or "validation".

        Returns
        -------
        ModelSettings
            The configured ModelSettings for the specified provider.

        Raises
        ------
        ValueError
            If an invalid `type_` is provided.
        """

        if type_ not in {"extraction", "validation"}:
            raise ValueError(
                f"Invalid page IR extraction model type '{type_}'. "
                f"Valid options are 'extraction' or 'validation'."
            )

        factory = _PAGE_IR_EXTRACTION_SETTINGS_REGISTRY[self.provider]
        return factory(config=self, type_=type_)

    def page_ir_verification_settings(self, type_: str) -> ModelSettings:
        """Resolve the correct model settings for page IR verification agents.

        Parameters
        ----------
        type_
            Specifies the type of page IR verification agent for which to retrieve
            settings. Expected values are either: "verification" or "validation".

        Returns
        -------
        ModelSettings
            The configured ModelSettings for the specified provider.

        Raises
        ------
        ValueError
            If an invalid `type_` is provided.
        """

        if type_ not in {"verification", "validation"}:
            raise ValueError(
                f"Invalid page IR verification model type '{type_}'. "
                f"Valid options are 'verification' or 'validation'."
            )

        factory = _PAGE_IR_VERIFICATION_SETTINGS_REGISTRY[self.provider]
        return factory(config=self, type_=type_)

    @property
    def provider(self) -> str:
        """Extract the provider prefix from the model string.

        Returns
        -------
        str
            The provider prefix (e.g., "anthropic", "openai") from the model string.
        """

        return self.model.split(":")[0]

    @property
    def supports_adaptive_thinking(self) -> bool:
        """Return whether the configured model supports Anthropic adaptive thinking.

        Returns
        -------
        bool
            True when the model is a known adaptive-thinking-capable Anthropic model;
            otherwise False. Non-Anthropic models always return False.
        """

        if self.provider != "anthropic":
            return False

        return self.model.split(":")[-1] in _ADAPTIVE_THINKING_MODEL_NAMES

    def wrap_output_type(
        self, output_type: Type[BaseModel]
    ) -> OutputSpec | PromptedOutput:
        """Wrap output type for providers that need prompted mode.

        Parameters
        ----------
        output_type
            The output type to wrap if necessary. For providers that don't require
            prompted mode, this is returned as-is.

        Returns
        -------
        OutputSpec | PromptedOutput
            The wrapped output type if the provider requires prompted mode, else the
            original output type.
        """

        if self.provider == "anthropic":
            return PromptedOutput(output_type)

        return output_type  # OpenAI uses default tool output
