"""Resolve Learning Progressions judge settings without dispatching model calls."""

# Standard Library
import json

# Package Library
from kgfeg.config import BackendSettings, Settings
from kgfeg.evals.lp_eval.schemas import ResolvedJudgeSettings

JUDGE_ATTEMPT_TIMEOUT_SECONDS = 180
JUDGE_CONCURRENCY = 4
JUDGE_MAX_RETRIES = 2
JUDGE_RETRY_WAITS_SECONDS = (5, 20)


def resolve_judge_settings(
    settings: BackendSettings | None = None,
) -> ResolvedJudgeSettings:
    """Capture the dedicated model and existing shared registry settings.

    This performs local configuration resolution only. It does not instantiate provider
    clients, verify remote model availability or authorize execution. Model
    availability is an execution preflight obligation.

    Parameters
    ----------
    settings
        Injectable backend settings. Omission uses the environment-loaded Settings.
        Only the LP evaluator model is selected; production and LC bindings are
        independent.

    Returns
    -------
    ResolvedJudgeSettings
        Immutable canonical non-secret configuration and operational settings. The
        retry count covers all attempts, including output repair; SDK retry
        multiplication is forbidden.

    Raises
    ------
    ValueError
        If the evaluator model is unset, malformed, unsupported by the shared provider
        registry, or its shared settings cannot be serialized.
    """

    backend_settings = Settings if settings is None else settings
    model_config = backend_settings.llm_config("lp_eval_judge")
    provider, separator, model_name = model_config.model.partition(":")

    if (
        not separator
        or not provider
        or not model_name
        or ":" in model_name
        or any(character.isspace() for character in model_config.model)
    ):
        raise ValueError("LP evaluator model must use provider:model-name syntax.")

    try:
        model_settings = model_config.kgs_settings("learning_progressions")
    except KeyError as exc:
        raise ValueError(
            "LP evaluator provider is unsupported by the shared model registry."
        ) from exc

    execution = {
        "attempt_timeout_seconds": JUDGE_ATTEMPT_TIMEOUT_SECONDS,
        "concurrency": JUDGE_CONCURRENCY,
        "max_retries": JUDGE_MAX_RETRIES,
        "retry_waits_seconds": JUDGE_RETRY_WAITS_SECONDS,
        "sdk_max_retries": 0,
    }
    return ResolvedJudgeSettings(
        execution_json=json.dumps(
            execution, allow_nan=False, separators=(",", ":"), sort_keys=True
        ),
        model=model_config.model,
        model_config_json=json.dumps(
            model_config.model_dump(mode="json"),
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        model_settings_json=json.dumps(
            dict(model_settings), allow_nan=False, separators=(",", ":"), sort_keys=True
        ),
        provider=provider,
    )
