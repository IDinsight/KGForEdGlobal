"""Bind evaluator output types to explicit provider schemas without text fallback."""

# Standard Library
from dataclasses import replace
from importlib.metadata import version
from typing import Any, Literal

# Third Party Library
from pydantic import TypeAdapter
from pydantic_ai import NativeOutput, ToolOutput
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.profiles.anthropic import anthropic_model_profile
from pydantic_ai.profiles.openai import openai_model_profile
from pydantic_ai.providers.anthropic import AnthropicJsonSchemaTransformer
from pydantic_ai.tools import GenerateToolJsonSchema

# Package Library
from kgfeg.evals.lp_eval.schemas import ClassificationJudgment, CritiqueJudgment
from kgfeg.kgs.lp_requests import canonical_lp_json
from kgfeg.model_registry import ModelConfig


def judge_output_contract(config: ModelConfig) -> str:
    """Freeze mode, SDK versions and actual provider-transformed output schemas.

    Parameters
    ----------
    config
        Dedicated evaluator model and unchanged shared settings.

    Returns
    -------
    str
        Canonical material included in every scheduled request's judge binding.
    """

    profile = judge_output_profile(config)
    schemas: dict[str, Any] = {}

    for task, model in (
        ("classification", ClassificationJudgment),
        ("critique", CritiqueJudgment),
    ):
        schema = TypeAdapter(model).json_schema(schema_generator=GenerateToolJsonSchema)
        description = schema.pop("description", None)

        if profile.json_schema_transformer is not None:
            schema = profile.json_schema_transformer(schema=schema, strict=True).walk()

        schemas[task] = {
            "description": description,
            "name": model.__name__,
            "schema": schema,
            "strict": True,
        }

    return canonical_lp_json(
        {
            "agent_retries": 0,
            "mode": "native" if config.provider == "anthropic" else "tool",
            "output_retries": 0,
            "request_limit": 1,
            "schemas": schemas,
            "sdk_versions": {
                name: version(name)
                for name in ("anthropic", "openai", "pydantic", "pydantic-ai-slim")
            },
        }
    )


def judge_output_profile(config: ModelConfig) -> ModelProfile:
    """Resolve supported provider capabilities without creating a remote client.

    Parameters
    ----------
    config
        Dedicated model configuration.

    Returns
    -------
    ModelProfile
        SDK profile with the provider's schema transformation intact.

    Raises
    ------
    ValueError
        If structured output cannot coexist with the shared model settings.
    """

    name = config.model.partition(":")[2]

    if config.provider == "anthropic":
        profile = ModelProfile(
            json_schema_transformer=AnthropicJsonSchemaTransformer
        ).update(anthropic_model_profile(name))

        # The installed SDK predates this model. Anthropic documents native schema
        # output for this exact identifier; preserve thinking and all other settings.
        # https://platform.claude.com/docs/en/build-with-claude/structured-outputs
        if name == "claude-opus-5":
            profile = replace(profile, supports_json_schema_output=True)

        if not profile.supports_json_schema_output:
            raise ValueError(
                "The configured Anthropic judge lacks supported native schema output; "
                "required thinking settings forbid forced output tools."
            )

        return profile

    if config.provider == "openai":
        openai_profile = openai_model_profile(name)

        if openai_profile is not None and openai_profile.supports_tools:
            return openai_profile

    raise ValueError("The configured judge has no supported structured output mode.")


def judge_output_type(
    *, config: ModelConfig, task: Literal["classification", "critique"]
) -> (
    NativeOutput[ClassificationJudgment | CritiqueJudgment]
    | ToolOutput[ClassificationJudgment | CritiqueJudgment]
):
    """Select exactly one typed output with no automatic mode or text alternative.

    Parameters
    ----------
    config
        Dedicated evaluator configuration.
    task
        Intended classification or critique schema.

    Returns
    -------
    NativeOutput | ToolOutput
        Explicit schema-bearing output specification with SDK validation enabled.
    """

    model = ClassificationJudgment if task == "classification" else CritiqueJudgment

    if config.provider == "anthropic":
        return NativeOutput(name=model.__name__, outputs=model, strict=True)

    return ToolOutput(max_retries=0, name=model.__name__, strict=True, type_=model)
