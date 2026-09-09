"""Resume bounded LP adjudication with validated checkpoints and failure evidence.

The complete request population is reconciled before calls are delegated to the LLM
layer. Sequential execution validates independent producer/checker stages and retains
failed attempts for safe resume. Processing completion is not semantic validation.
"""

# Future Library
from __future__ import annotations

# Standard Library
import fcntl
import hashlib

from pathlib import Path
from typing import Any

# Package Library
from kgfeg.config import Settings
from kgfeg.kgs import prompts
from kgfeg.kgs.llm import KGUsageTracker, generate_learning_progressions_for_request
from kgfeg.kgs.lp_checkpoints import (
    LPGenerationCheckpoints,
    archive_lp_generation_artifacts,
    content_hash,
    reconciled_response,
)
from kgfeg.kgs.lp_requests import (
    MANIFEST_FILENAME,
    LPRequestPopulation,
    build_lp_generation_requests,
    read_lp_request_population,
    write_lp_generation_request_artifacts,
)
from kgfeg.kgs.schemas import (
    AcademicStandardsLCKGBundle,
    LPGenerationResponse,
    LPGenerationValidationVerdict,
)
from kgfeg.kgs.utils import KGDirs
from kgfeg.kgs.validators import (
    verify_lp_generation_response_integrity,
    verify_lp_generation_validation_integrity,
)
from kgfeg.model_registry import ModelConfig
from kgfeg.schemas import CreateKGConfig


class LPGenerationFailed(RuntimeError):
    """A request exhausted its stage retries; LP processing cannot report success."""


def _execution_material(
    *, kg_config: CreateKGConfig, population: LPRequestPopulation
) -> dict[str, Any]:
    """Capture actual configuration, model, schemas, and prompt definition material.

    Parameters
    ----------
    kg_config
        Complete effective KG configuration, including LP retry budgets.
    population
        Reconciled upstream, candidate, and request content identities.

    Returns
    -------
    dict[str, Any]
        Code-owned execution fingerprint inputs, with no manual version selectors.
    """

    model = Settings.llm_config("kgs")
    return {
        "checker_instructions": kg_config.learning_progressions.checker_instructions,
        "config_content_hash": content_hash(
            kg_config.model_dump(by_alias=True, mode="json")
        ),
        "model_config": model.model_dump(mode="json"),
        "model_settings": dict(model.kgs_settings("learning_progressions")),
        "producer_instructions": kg_config.learning_progressions.producer_instructions,
        "prompt_definitions_content_hash": hashlib.sha256(
            Path(prompts.__file__).read_bytes()
        ).hexdigest(),
        "request_manifest": population.manifest.model_dump(mode="json"),
        "response_schema_content_hash": content_hash(
            LPGenerationResponse.model_json_schema()
        ),
        "retry_limits": {
            "draft": kg_config.learning_progressions.retry.producer_max_retries,
            "verdict": kg_config.learning_progressions.retry.checker_max_retries,
        },
        "verdict_schema_content_hash": content_hash(
            LPGenerationValidationVerdict.model_json_schema()
        ),
    }


def _finish_lp_request(
    *,
    kg_config: CreateKGConfig,
    model_config: ModelConfig,
    population: LPRequestPopulation,
    request_index: int,
    store: LPGenerationCheckpoints,
    usage_tracker: KGUsageTracker,
) -> None:
    """Resume one request at its earliest unfinished validated stage.

    Parameters
    ----------
    kg_config
        Captured effective policy and independent stage retry counts.
    model_config
        Captured shared KG model configuration.
    population
        Complete materialized request sequence.
    request_index
        First incomplete request position.
    store
        Validated locked checkpoint store.
    usage_tracker
        Existing LP token accounting buckets.
    """

    request = population.requests[request_index]
    retries = kg_config.learning_progressions.retry

    for stage, maximum in (
        ("draft", retries.producer_max_retries),
        ("verdict", retries.checker_max_retries),
    ):
        if request_index < len(store.rows[stage]):
            continue

        for attempt in range(1, maximum + 2):
            _verify_execution_material(
                kg_config=kg_config, population=population, store=store
            )
            draft = (
                None
                if stage == "draft"
                else LPGenerationResponse.model_validate(
                    store.rows["draft"][request_index].payload
                )
            )

            try:
                output = generate_learning_progressions_for_request(
                    draft=draft,
                    kg_config=kg_config,
                    model_config=model_config,
                    request=request.model_copy(deep=True),
                    usage_tracker=usage_tracker,
                )

                if stage == "draft":
                    response = LPGenerationResponse.model_validate(
                        output.model_dump(mode="python")
                    )
                    verify_lp_generation_response_integrity(
                        lp_generation_request=request, lp_generation_response=response
                    )
                    validated: LPGenerationResponse | LPGenerationValidationVerdict = (
                        response
                    )
                else:
                    if draft is None:
                        raise ValueError("LP checker execution requires a valid draft.")

                    verdict = LPGenerationValidationVerdict.model_validate(
                        output.model_dump(mode="python")
                    )
                    verify_lp_generation_validation_integrity(
                        draft_response=draft,
                        lp_generation_request=request,
                        validation_verdict=verdict,
                    )
                    validated = verdict
            except Exception as error:  # pylint: disable=broad-exception-caught
                _verify_execution_material(
                    kg_config=kg_config, population=population, store=store
                )
                exhausted = attempt == maximum + 1
                store.record_failure(
                    attempt=attempt,
                    error=error,
                    exhausted=exhausted,
                    request_index=request_index,
                    stage=stage,
                )

                if exhausted:
                    raise LPGenerationFailed(
                        f"LP {stage} failed for request {request.request_id} after "
                        f"{attempt} attempts; processing halted. Inspect "
                        f"lp_generation_failures.json."
                    ) from None

                continue

            _verify_execution_material(
                kg_config=kg_config, population=population, store=store
            )
            store.append(payload=validated, request_index=request_index, stage=stage)
            break

    draft = LPGenerationResponse.model_validate(
        store.rows["draft"][request_index].payload
    )
    verdict = LPGenerationValidationVerdict.model_validate(
        store.rows["verdict"][request_index].payload
    )
    store.append(
        payload=reconciled_response(draft=draft, verdict=verdict),
        request_index=request_index,
        stage="response",
    )


def _verify_execution_material(
    *,
    kg_config: CreateKGConfig,
    population: LPRequestPopulation,
    store: LPGenerationCheckpoints,
) -> None:
    """Reconcile population and checkpoint material immediately around every call.

    Parameters
    ----------
    kg_config
        Captured effective configuration.
    population
        Complete authoritative request population.
    store
        Current validated checkpoint state and execution material.

    Raises
    ------
    ValueError
        If the LP prompt, model, or material changed during execution.
    """

    read_lp_request_population(expected=population, root=store.root)

    if (
        _execution_material(kg_config=kg_config, population=population)
        != store.material
    ):
        raise ValueError("LP prompt or model material changed during execution.")

    store.verify_bytes()


def generate_learning_progressions(
    *,
    as_lc_bundle: AcademicStandardsLCKGBundle,
    doc_key: str,
    kg_config: CreateKGConfig,
    kg_dirs: KGDirs,
    overwrite: bool,
    usage_tracker: KGUsageTracker,
) -> tuple[LPGenerationResponse, ...]:
    """Materialize, execute, and resume bounded LP producer/checker adjudication.

    Each request finishes before another starts. Valid completed calls are reused; a
    saved draft survives checker failure. Every failed attempt is retained outside
    successful prefixes, with later completion linked to the resumed run ordinal. No
    relationship finalization, graph validation, or release status is performed.

    Parameters
    ----------
    as_lc_bundle
        Final validated upstream AS+LC bundle for this document.
    doc_key
        Authoritative document identity.
    kg_config
        Effective curriculum policy, evidence bounds, and stage retry counts.
    kg_dirs
        Directory receiving the complete request and checkpoint artifacts.
    overwrite
        Explicit regeneration archives prior evidence before replacing affected files.
        False requires exact current material and validated stage-prefix reuse.
    usage_tracker
        Existing LP producer/checker token accounting buckets.

    Returns
    -------
    tuple[LPGenerationResponse, ...]
        Complete ordered accepted/corrected responses, including normal negative and
        ambiguous judgments. Processing completion does not establish semantic truth.

    Raises
    ------
    LPGenerationFailed
        If any request exhausts producer or checker retries; no partial success returns.
    ValueError
        If persisted material is malformed, stale, truncated, or misaligned.
    OSError
        If the directory is locked by another writer or persistence fails.
    """

    if not isinstance(overwrite, bool):
        raise ValueError("LP overwrite must be an explicit boolean.")

    bundle = AcademicStandardsLCKGBundle.model_validate_json(
        as_lc_bundle.model_dump_json()
    )
    config = CreateKGConfig.model_validate_json(
        kg_config.model_dump_json(by_alias=True)
    )

    # Complete deterministic construction must succeed before replacing any evidence.
    expected = build_lp_generation_requests(
        as_lc_bundle=bundle, doc_key=doc_key, kg_config=config
    )
    kg_dirs.root.mkdir(exist_ok=True, parents=True)

    with (kg_dirs.root / ".lp_generation.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        if overwrite:
            archive_lp_generation_artifacts(kg_dirs.root)

        input_names = (*expected.manifest.artifact_byte_hashes, MANIFEST_FILENAME)
        execution_names = (
            "lp_generation_checkpoint_manifest.json",
            "lp_generation_checkpoint_transaction.json",
            "lp_generation_failures.json",
            "lp_generation_draft_responses.jsonl",
            "lp_generation_validation_verdicts.jsonl",
            "lp_generation_responses.jsonl",
        )

        if any(
            (kg_dirs.root / name).exists() for name in (*input_names, *execution_names)
        ):
            population = read_lp_request_population(
                expected=expected, root=kg_dirs.root
            )
        else:
            population = write_lp_generation_request_artifacts(
                as_lc_bundle=bundle, doc_key=doc_key, kg_config=config, kg_dirs=kg_dirs
            )

        material = _execution_material(kg_config=config, population=population)
        store = LPGenerationCheckpoints(
            material=material, population=population, root=kg_dirs.root
        )
        store.begin_run()
        model = ModelConfig.model_validate(material["model_config"])

        for index in range(len(store.rows["response"]), len(population.requests)):
            _finish_lp_request(
                kg_config=config,
                model_config=model,
                population=population,
                request_index=index,
                store=store,
                usage_tracker=usage_tracker,
            )

        _verify_execution_material(kg_config=config, population=population, store=store)

        if any(failure.resolved_run_number is None for failure in store.failures):
            raise LPGenerationFailed("LP processing failures remain unresolved.")

        return tuple(
            LPGenerationResponse.model_validate(row.payload)
            for row in store.rows["response"]
        )
