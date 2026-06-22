"""This module contains the orchestration logic for creating various knowledge graph
artifacts using an LLM.
"""

# Standard Library
from dataclasses import dataclass

# Third Party Library
from loguru import logger

# Package Library
from skg.config import Settings
from skg.kgs.agents import create_sfi_extraction_agent
from skg.kgs.prompts import extract_sfi_candidates_from_window
from skg.kgs.schemas import DocumentProfile, ExtractionWindow, SFIExtractionResult
from skg.kgs.validators import verify_sfi_extraction_quality
from skg.utils.general import AgentUsageBucket


@dataclass
class SFIExtractionUsageTracker:
    """Track LLM token usage for SFI candidate extraction."""

    sfi_extraction: AgentUsageBucket

    def __init__(self) -> None:
        """Initialize an empty usage bucket for the SFI extraction agent."""

        self.sfi_extraction = AgentUsageBucket(agent_name="sfi_extraction")

    def to_dict(self) -> dict[str, object]:
        """Serialize usage totals to a JSON-friendly dictionary.

        Returns
        -------
        dict[str, object]
            Usage summary with per-agent and total counts.
        """

        sfi_extraction_d = self.sfi_extraction.to_dict()
        totals = {
            "cache_read_tokens": self.sfi_extraction.cache_read_tokens,
            "cache_write_tokens": self.sfi_extraction.cache_write_tokens,
            "input_tokens": self.sfi_extraction.input_tokens,
            "output_tokens": self.sfi_extraction.output_tokens,
            "requests": self.sfi_extraction.requests,
            "runs": self.sfi_extraction.runs,
            "total_tokens": self.sfi_extraction.input_tokens
            + self.sfi_extraction.output_tokens,
        }
        return {"agents": {"sfi_extraction": sfi_extraction_d}, "totals": totals}


def extract_sfi_candidates(
    *,
    document_profile: DocumentProfile,
    extraction_window: ExtractionWindow,
    usage_tracker: SFIExtractionUsageTracker,
) -> SFIExtractionResult:
    """Extract SFI candidates from one extraction window using an LLM.

    Parameters
    ----------
    document_profile
        Country/document-specific KG extraction profile.
    extraction_window
        Source-faithful extraction window to inspect.
    usage_tracker
        Usage tracker to accumulate token usage.

    Returns
    -------
    SFIExtractionResult
        Parsed and quality-validated SFI extraction result.
    """

    logger.info(
        f"Running SFI extraction for window: "
        f"{extraction_window.window_index} ({extraction_window.window_id})..."
    )

    prompts = extract_sfi_candidates_from_window(
        document_profile=document_profile, extraction_window=extraction_window
    )
    agent = create_sfi_extraction_agent(
        instructions=prompts.system_message,
        model_config=Settings.llm_config("kgs"),
        verify_quality_fn=verify_sfi_extraction_quality,
        window=extraction_window,
    )
    result = agent.run_sync(prompts.user_message)
    usage_tracker.sfi_extraction.add_run_usage(result.usage())

    logger.success(
        f"Finished SFI extraction for window {extraction_window.window_index}; "
        f"candidates={len(result.output.sfi_candidates)}, "
        f"auxiliary={len(result.output.auxiliary_candidates)}."
    )

    return result.output
