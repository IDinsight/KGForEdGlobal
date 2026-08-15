"""Agent construction and the resumable judging loop for the rubric evaluation."""

# Future Library
from __future__ import annotations

# Standard Library
import asyncio
import json
import random

from pathlib import Path
from typing import Any, Sequence, cast

# Third Party Library
from pydantic import Field
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError

# Package Library
from skg.config import Settings
from skg.evals.lc_eval.prompts import (
    EDGE_SYSTEM_MESSAGE,
    RUBRIC_SYSTEM_MESSAGE,
    build_edge_user_message,
    build_rubric_user_message,
)
from skg.evals.lc_eval.schemas import (
    EdgeItem,
    EdgeJudgement,
    RubricItem,
    RubricJudgement,
    RubricVerdict,
)
from skg.model_registry import ModelConfig
from skg.schemas import BaseSchema
from skg.utils.general import make_dir
from skg.utils.logging_ import logger

JUDGE_CONCURRENCY = 8

_MAX_ATTEMPTS = 6
_RETRY_BASE_SECONDS = 4
_RETRY_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 529})


class _Selection(BaseSchema):
    """Judge output: the candidate ids accepted for one discrimination item."""

    selected_candidate_ids: list[str] = Field(
        default_factory=list,
        description="Ids of every candidate the judge accepts as a genuine pairing.",
    )


async def _call_with_retries(*, agent: Agent, message: str) -> Any:
    """Run one judge call, retrying transient API failures with exponential backoff.

    Overload and rate-limit responses are expected on long runs and are not a reason
    to lose the whole pass, since every completed verdict is already durable.

    Parameters
    ----------
    agent
        Judge agent to run.
    message
        Rendered user message.

    Returns
    -------
    Any
        The agent run result.

    Raises
    ------
    ModelHTTPError
        If the call still fails after the final attempt.
    """

    for attempt in range(_MAX_ATTEMPTS):
        try:
            return await agent.run(message)
        except ModelHTTPError as error:
            if error.status_code not in _RETRY_STATUS or attempt == _MAX_ATTEMPTS - 1:
                raise
            delay = _RETRY_BASE_SECONDS * (2**attempt)
            logger.warning(
                f"Judge call failed with status {error.status_code}; retrying in "
                f"{delay}s (attempt {attempt + 1} of {_MAX_ATTEMPTS})."
            )
            await asyncio.sleep(delay)

    raise RuntimeError("unreachable")


async def _judge_one(
    *,
    agent: Agent,
    item: RubricItem,
    replicate: int,
    semaphore: asyncio.Semaphore,
    usage: dict[str, int],
) -> RubricVerdict:
    """Judge one item once, bounded by the concurrency semaphore.

    Parameters
    ----------
    agent
        Judge agent shared across concurrent calls.
    item
        Item to judge.
    replicate
        Zero-based replicate index, controlling component presentation order.
    semaphore
        Limits how many judge calls are in flight at once.
    usage
        Accumulator updated with the call's token usage.

    Returns
    -------
    RubricVerdict
        Parsed judge verdict.

    Raises
    ------
    ValueError
        If the item shows duplicate component ids, or the verdict does not cover
        exactly the components that were shown.
    """

    components = list(item.components)
    random.Random(f"{item.item_id}|{replicate}").shuffle(components)
    async with semaphore:
        result = await _call_with_retries(
            agent=agent,
            message=build_rubric_user_message(components=components, item=item),
        )

    call_usage = result.usage()
    usage["input_tokens"] += call_usage.input_tokens or 0
    usage["output_tokens"] += call_usage.output_tokens or 0
    usage["requests"] += 1

    verdict = cast(RubricVerdict, result.output)
    judged = sorted(v.component_id for v in verdict.component_verdicts)
    shown = sorted(c.component_id for c in components)
    if len(set(shown)) != len(shown):
        raise ValueError(
            f"Item item_id={item.item_id!r} shows duplicate component ids {shown}; "
            f"every component must be separately addressable by the judge."
        )
    if judged != shown:
        raise ValueError(
            f"Judge verdict for item_id={item.item_id!r} covers component ids "
            f"{judged}, but {shown} were shown."
        )

    return verdict


def _load_existing_judgements(save_fp: Path) -> dict[tuple[str, int], RubricJudgement]:
    """Load previously cached judge verdicts keyed by item and replicate.

    Parameters
    ----------
    save_fp
        JSONL artifact holding cached verdicts.

    Returns
    -------
    dict[tuple[str, int], RubricJudgement]
        Cached verdicts, empty when the artifact does not exist.
    """

    if not save_fp.exists():
        return {}

    cached: dict[tuple[str, int], RubricJudgement] = {}
    for line in save_fp.read_text().splitlines():
        if not line.strip():
            continue
        judgement = RubricJudgement.model_validate_json(line)
        cached[(judgement.item_id, judgement.replicate)] = judgement

    return cached


async def _run_judging(
    *,
    concurrency: int,
    model_config: ModelConfig,
    pending: Sequence[tuple[RubricItem, int]],
    save_fp: Path,
) -> tuple[list[RubricJudgement], dict[str, int]]:
    """Judge all pending item and replicate pairs concurrently.

    Verdicts are written as each call completes rather than after all of them, so an
    interrupted run keeps whatever finished.

    Parameters
    ----------
    concurrency
        Maximum judge calls in flight at once.
    model_config
        Judge model configuration.
    pending
        Item and replicate pairs still needing a verdict.
    save_fp
        JSONL artifact verdicts are appended to.

    Returns
    -------
    tuple[list[RubricJudgement], dict[str, int]]
        Newly produced verdicts and the run's token usage.
    """

    agent = Agent(
        model_config.model,
        instructions=RUBRIC_SYSTEM_MESSAGE,
        model_settings=model_config.kgs_settings("learning_components"),
        output_type=model_config.wrap_output_type(RubricVerdict),
    )
    semaphore = asyncio.Semaphore(concurrency)
    usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0}

    async def judge(pair: tuple[RubricItem, int]) -> RubricJudgement:
        """Judge one item and replicate pair into a persistable record.

        Parameters
        ----------
        pair
            Item and zero-based replicate index to judge.

        Returns
        -------
        RubricJudgement
            The verdict with its caching metadata attached.
        """

        item, replicate = pair
        return RubricJudgement(
            item_id=item.item_id,
            judge_model=model_config.model,
            replicate=replicate,
            verdict=await _judge_one(
                agent=agent,
                item=item,
                replicate=replicate,
                semaphore=semaphore,
                usage=usage,
            ),
        )

    produced: list[RubricJudgement] = []
    with save_fp.open("a", encoding="utf-8") as handle:
        tasks = [asyncio.create_task(judge(pair)) for pair in pending]
        for index, task in enumerate(asyncio.as_completed(tasks), start=1):
            judgement = await task
            handle.write(
                json.dumps(judgement.model_dump(mode="json"), ensure_ascii=False) + "\n"
            )
            handle.flush()
            produced.append(judgement)
            if index % 50 == 0 or index == len(tasks):
                logger.info(f"Judged {index}/{len(tasks)} pending items.")

    return (produced, usage)


def judge_model_config() -> ModelConfig:
    """Build the judge model configuration from environment settings.

    Returns
    -------
    ModelConfig
        Judge configuration driven by `LLM_LC_EVAL_JUDGE_MODEL`, keeping the judge on
        the same environment-driven footing as the pipeline's own model selection.
    """

    return Settings.llm_config("lc_eval_judge")


def run_edge_judging(
    *,
    concurrency: int = JUDGE_CONCURRENCY,
    items: Sequence[EdgeItem],
    model_config: ModelConfig,
    output_dir: Path,
    replicates: int = 1,
) -> list[EdgeJudgement]:
    """Judge every discrimination item the requested number of times, resuming.

    Candidate order is shuffled per replicate, so replicates measure position bias as
    well as judge variability.

    Parameters
    ----------
    concurrency
        Maximum judge calls in flight at once.
    items
        Items to judge.
    model_config
        Judge model configuration.
    output_dir
        Directory holding the verdict cache.
    replicates
        Number of times each item is judged.

    Returns
    -------
    list[EdgeJudgement]
        All verdicts for the requested items and replicates.
    """

    make_dir(output_dir)
    save_fp = output_dir / "lc_eval_edge_judgements.jsonl"
    cached: dict[tuple[str, int], EdgeJudgement] = {}
    if save_fp.exists():
        for line in save_fp.read_text().splitlines():
            if line.strip():
                judgement = EdgeJudgement.model_validate_json(line)
                cached[(judgement.item_id, judgement.replicate)] = judgement

    pending = [
        (item, replicate)
        for item in items
        for replicate in range(replicates)
        if (item.item_id, replicate) not in cached
    ]
    logger.info(
        f"Judging edge items: items={len(items)}; replicates={replicates}; "
        f"concurrency={concurrency}; resumed={len(cached)}; pending={len(pending)}."
    )

    async def run() -> None:
        """Judge all pending item and replicate pairs concurrently."""

        agent = Agent(
            model_config.model,
            instructions=EDGE_SYSTEM_MESSAGE,
            model_settings=model_config.kgs_settings("learning_components"),
            output_type=model_config.wrap_output_type(_Selection),
        )
        semaphore = asyncio.Semaphore(concurrency)

        async def judge(pair: tuple[EdgeItem, int]) -> EdgeJudgement:
            """Judge one item and replicate pair.

            Parameters
            ----------
            pair
                Item and zero-based replicate index to judge.

            Returns
            -------
            EdgeJudgement
                The judge's selection.
            """

            item, replicate = pair
            shuffled = list(item.candidates)
            random.Random(f"{item.item_id}|{replicate}").shuffle(shuffled)
            async with semaphore:
                result = await _call_with_retries(
                    agent=agent,
                    message=build_edge_user_message(
                        item.model_copy(update={"candidates": shuffled})
                    ),
                )
            offered = {c.candidate_id for c in item.candidates}
            chosen = cast(_Selection, result.output).selected_candidate_ids
            return EdgeJudgement(
                item_id=item.item_id,
                judge_model=model_config.model,
                replicate=replicate,
                selected_candidate_ids=sorted(set(chosen) & offered),
            )

        with save_fp.open("a", encoding="utf-8") as handle:
            tasks = [asyncio.create_task(judge(pair)) for pair in pending]
            for index, task in enumerate(asyncio.as_completed(tasks), start=1):
                judgement = await task
                handle.write(
                    json.dumps(judgement.model_dump(mode="json"), ensure_ascii=False)
                    + "\n"
                )
                handle.flush()
                cached[(judgement.item_id, judgement.replicate)] = judgement
                if index % 200 == 0 or index == len(tasks):
                    logger.info(f"Judged {index}/{len(tasks)} pending edge items.")

    if pending:
        asyncio.run(run())

    return [
        cached[(item.item_id, replicate)]
        for item in items
        for replicate in range(replicates)
        if (item.item_id, replicate) in cached
    ]


def run_rubric_judging(
    *,
    concurrency: int = JUDGE_CONCURRENCY,
    items: Sequence[RubricItem],
    model_config: ModelConfig,
    output_dir: Path,
    replicates: int,
) -> list[RubricJudgement]:
    """Judge every item the requested number of times, resuming from cache.

    Verdicts are appended to `lc_eval_judgements.jsonl` as they are produced, so an
    interrupted run resumes without re-judging completed item and replicate pairs.
    Items are judged concurrently; the cache is keyed by item and replicate rather
    than by position, so completion order does not affect resumability.

    Parameters
    ----------
    concurrency
        Maximum judge calls in flight at once.
    items
        Items to judge.
    model_config
        Judge model configuration.
    output_dir
        Directory holding the verdict cache.
    replicates
        Number of times each item is judged.

    Returns
    -------
    list[RubricJudgement]
        All verdicts for the requested items and replicates.
    """

    make_dir(output_dir)
    save_fp = output_dir / "lc_eval_judgements.jsonl"
    cached = _load_existing_judgements(save_fp)
    pending = [
        (item, replicate)
        for item in items
        for replicate in range(replicates)
        if (item.item_id, replicate) not in cached
    ]

    logger.info(
        f"Judging LC eval items: items={len(items)}; replicates={replicates}; "
        f"concurrency={concurrency}; resumed={len(cached)}; pending={len(pending)}."
    )

    if pending:
        produced, usage = asyncio.run(
            _run_judging(
                concurrency=concurrency,
                model_config=model_config,
                pending=pending,
                save_fp=save_fp,
            )
        )
        for judgement in produced:
            cached[(judgement.item_id, judgement.replicate)] = judgement
        logger.success(
            f"Judge usage: requests={usage['requests']}; "
            f"input_tokens={usage['input_tokens']}; "
            f"output_tokens={usage['output_tokens']}."
        )

    return [
        cached[(item.item_id, replicate)]
        for item in items
        for replicate in range(replicates)
    ]
