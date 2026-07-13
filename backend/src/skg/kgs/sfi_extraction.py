"""This module contains functionalities related to extracting SFI candidates from
extraction windows using an LLM.
"""

# Standard Library
from collections import Counter
from pathlib import Path
from typing import Sequence

# Third Party Library
from loguru import logger

# Package Library
from skg.kgs.llm import KGUsageTracker, extract_sfi_candidates
from skg.kgs.schemas import (
    ExtractionWindow,
    SFIExtractionResult,
    SFIExtractionSummary,
)
from skg.kgs.utils import append_jsonl_model
from skg.kgs.validators import verify_sfi_extraction_integrity
from skg.page_ir_extraction.validators import QualityError
from skg.schemas import CreateKGConfig
from skg.utils.general import make_dir, write_to_json


def _build_sfi_extraction_summary(
    sfi_extraction_results: Sequence[SFIExtractionResult],
) -> SFIExtractionSummary:
    """Build an aggregate summary for SFI extraction results.

    Parameters
    ----------
    sfi_extraction_results
        Final SFI extraction results accepted or corrected by the validation LLM.

    Returns
    -------
    SFIExtractionSummary
        Aggregate counts for the extraction run.
    """

    auxiliary_candidate_count = 0
    candidate_count = 0
    normalized_counts: Counter[str] = Counter()
    statement_type_counts: Counter[str] = Counter()
    windows_with_auxiliary_candidates = 0
    windows_with_sfi_candidates = 0

    for sfi_extraction_result in sfi_extraction_results:
        auxiliary_candidate_count += len(sfi_extraction_result.auxiliary_candidates)
        candidate_count += len(sfi_extraction_result.sfi_candidates)

        if sfi_extraction_result.auxiliary_candidates:
            windows_with_auxiliary_candidates += 1

        if sfi_extraction_result.sfi_candidates:
            windows_with_sfi_candidates += 1

        for candidate in sfi_extraction_result.sfi_candidates:
            normalized_counts[candidate.normalized_statement_type] += 1
            statement_type_counts[candidate.statement_type] += 1

    return SFIExtractionSummary(
        auxiliary_candidate_count=auxiliary_candidate_count,
        candidate_count=candidate_count,
        candidate_count_by_normalized_statement_type=dict(
            sorted(normalized_counts.items())
        ),
        candidate_count_by_statement_type=dict(sorted(statement_type_counts.items())),
        window_count=len(sfi_extraction_results),
        windows_with_auxiliary_candidates=windows_with_auxiliary_candidates,
        windows_with_sfi_candidates=windows_with_sfi_candidates,
        windows_without_candidates=len(sfi_extraction_results)
        - windows_with_sfi_candidates,
    )


def _load_existing_sfi_extraction_results(save_fp: Path) -> list[SFIExtractionResult]:
    """Load existing SFI extraction results from a JSONL artifact.

    Blank lines are ignored. Every non-empty line must validate as an
    `SFIExtractionResult` so resumed runs cannot silently continue from malformed
    output.

    Parameters
    ----------
    save_fp
        File path for the JSONL extraction-result artifact.

    Returns
    -------
    list[SFIExtractionResult]
        Existing validated extraction results in file order.

    Raises
    ------
    ValueError
        If any non-empty JSONL line cannot be parsed or validated.
    """

    if not save_fp.exists():
        return []

    loaded_results: list[SFIExtractionResult] = []

    with save_fp.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line_clean = line.strip()

            if not line_clean:
                continue

            try:
                loaded_results.append(
                    SFIExtractionResult.model_validate_json(line_clean)
                )
            except Exception as e:  # pylint: disable=broad-except
                raise ValueError(
                    f"Could not parse SFI extraction JSONL artifact {save_fp} "
                    f"line {line_number}."
                ) from e

    return loaded_results


def _persist_sfi_extraction_summary(
    *, results: Sequence[SFIExtractionResult], summary_fp: Path
) -> SFIExtractionSummary:
    """Build and persist the current SFI extraction summary artifact.

    Parameters
    ----------
    results
        Final validation-LLM-reviewed extraction results to summarize.
    summary_fp
        File path for the aggregate summary JSON artifact.

    Returns
    -------
    SFIExtractionSummary
        The summary written to disk.
    """

    make_dir(summary_fp.parent)
    summary = _build_sfi_extraction_summary(results)
    write_to_json(fp=summary_fp, json_info=summary)
    return summary


def _validate_existing_sfi_extraction_results(
    *,
    extraction_windows: Sequence[ExtractionWindow],
    kg_config: CreateKGConfig,
    results: Sequence[SFIExtractionResult],
) -> None:
    """Validate that existing SFI results are a quality-checked current prefix.

    Resumability assumes this module wrote prior results in extraction-window order.
    This check prevents a resumed run from mixing outputs from a stale or different
    extraction-window artifact with the current run, and re-runs current universal
    integrity validation on persisted final results.

    Parameters
    ----------
    extraction_windows
        Ordered source-faithful extraction windows for the current run.
    kg_config
        Country/document-specific KG extraction configuration.
    results
        Existing extraction results loaded from the JSONL artifact.

    Raises
    ------
    ValueError
        If existing results are longer than the current window list, do not match the
        corresponding window IDs/indexes/source segment IDs, or fail current quality
        validation.
    """

    if len(results) > len(extraction_windows):
        raise ValueError(
            f"Existing SFI extraction results contain {len(results)} windows, but "
            f"the current extraction-window artifact contains only "
            f"{len(extraction_windows)} windows."
        )

    for result_index, result in enumerate(results):
        extraction_window = extraction_windows[result_index]

        if result.window_id != extraction_window.window_id:
            raise ValueError(
                f"Existing SFI extraction result at position {result_index} has "
                f"window_id={result.window_id!r}, but the current extraction window "
                f"has window_id={extraction_window.window_id!r}."
            )

        if result.window_index != extraction_window.window_index:
            raise ValueError(
                f"Existing SFI extraction result at position {result_index} has "
                f"window_index={result.window_index!r}, but the current extraction "
                f"window has window_index={extraction_window.window_index!r}."
            )

        if result.window_source_segment_ids != extraction_window.source_segment_ids:
            raise ValueError(
                f"Existing SFI extraction result at position {result_index} has "
                f"window_source_segment_ids={result.window_source_segment_ids!r}, "
                f"but the current extraction window has "
                f"source_segment_ids={extraction_window.source_segment_ids!r}."
            )

        try:
            verify_sfi_extraction_integrity(
                extraction_result=result, kg_config=kg_config, window=extraction_window
            )
        except QualityError as e:
            raise ValueError(
                f"Existing SFI extraction result at position {result_index} failed "
                f"current integrity validation: {e}"
            ) from e


def extract_sfi_candidates_from_windows(
    *,
    extraction_windows: Sequence[ExtractionWindow],
    kg_config: CreateKGConfig,
    overwrite: bool,
    save_fp: Path,
    summary_fp: Path,
    usage_tracker: KGUsageTracker,
) -> list[SFIExtractionResult]:
    """Extract SFI candidates and incrementally persist resumable artifacts.

    If `overwrite` is true, any existing SFI extraction JSONL and summary artifacts are
    deleted and extraction starts from the first window. If `overwrite` is false, any
    existing JSONL artifact is treated as a completed prefix only after validating that
    it matches the current extraction-window artifact. The run skips LLM calls only
    when the existing prefix already covers every extraction window. Otherwise, the
    summary is refreshed from the existing prefix and extraction resumes at the first
    unprocessed window. After every successful window extraction, the new result is
    appended to the JSONL artifact and the summary JSON is overwritten with fresh
    aggregate counts.

    Parameters
    ----------
    extraction_windows
        Ordered source-faithful extraction windows to process.
    kg_config
        Country/document-specific KG extraction configuration.
    overwrite
        Whether to discard existing SFI extraction artifacts and restart extraction
        from the first window. When False, existing validated prefix results are reused
        and extraction resumes until all windows are complete.
    save_fp
        File path for the JSONL extraction-result artifact.
    summary_fp
        File path for the aggregate summary JSON artifact.
    usage_tracker
        Usage tracker to accumulate token usage.

    Returns
    -------
    list[SFIExtractionResult]
        Final validation-LLM-reviewed extraction results in window order.

    Raises
    ------
    ValueError
        If no extraction windows were provided.
    """

    if not extraction_windows:
        raise ValueError(
            "SFI extraction requires at least one extraction window. Zero extraction "
            "windows indicate a failed upstream windowing/configuration run and must "
            "not produce empty SFI extraction artifacts."
        )

    total_windows = len(extraction_windows)

    if overwrite:
        for fp in [save_fp, summary_fp]:
            if fp.exists():
                fp.unlink()
                logger.info(f"Removed existing SFI extraction artifact: {fp}")

        sfi_extraction_results: list[SFIExtractionResult] = []

        logger.info(
            f"Starting SFI extraction from scratch because overwrite=True; "
            f"completed_windows=0/{total_windows}."
        )
    else:
        sfi_extraction_results = _load_existing_sfi_extraction_results(save_fp)
        _validate_existing_sfi_extraction_results(
            extraction_windows=extraction_windows,
            kg_config=kg_config,
            results=sfi_extraction_results,
        )

        _persist_sfi_extraction_summary(
            results=sfi_extraction_results, summary_fp=summary_fp
        )

        completed_windows = len(sfi_extraction_results)

        if completed_windows == total_windows:
            logger.info(
                f"SFI extraction is already complete and overwrite=False; "
                f"completed_windows={completed_windows}/{total_windows}. "
                f"Skipping LLM calls: {save_fp}, {summary_fp}."
            )

            return sfi_extraction_results

        logger.info(
            f"Resuming SFI extraction from existing artifact {save_fp}; "
            f"completed_windows={completed_windows}/{total_windows}, "
            f"remaining_windows={total_windows - completed_windows}."
        )

    for current_window_number, extraction_window in enumerate(
        extraction_windows[len(sfi_extraction_results) :],
        start=len(sfi_extraction_results) + 1,
    ):
        logger.info(
            f"Running SFI extraction for window "
            f"{current_window_number}/{total_windows}: "
            f"window_id={extraction_window.window_id}..."
        )

        sfi_extraction_result = extract_sfi_candidates(
            extraction_window=extraction_window,
            kg_config=kg_config,
            usage_tracker=usage_tracker,
        )
        sfi_extraction_results.append(sfi_extraction_result)
        append_jsonl_model(fp=save_fp, model=sfi_extraction_result)
        _persist_sfi_extraction_summary(
            results=sfi_extraction_results, summary_fp=summary_fp
        )

        logger.success(
            f"Finished SFI extraction for window "
            f"{current_window_number}/{total_windows}: "
            f"candidates={len(sfi_extraction_result.sfi_candidates)}, "
            f"auxiliary={len(sfi_extraction_result.auxiliary_candidates)}."
        )

    logger.success(
        f"Finished all SFI extraction: "
        f"completed_windows={len(sfi_extraction_results)}/{total_windows}; "
        f"{save_fp} and {summary_fp}."
    )

    return sfi_extraction_results
