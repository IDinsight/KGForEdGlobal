"""This module contains the orchestration logic for creating various knowledge graph
artifacts using an LLM.
"""

# Standard Library
from dataclasses import dataclass

# Package Library
from skg.config import Settings
from skg.kgs.agents import create_sfi_extraction_agent
from skg.kgs.prompts import extract_sfi_candidates_from_window
from skg.kgs.schemas import ExtractionWindow, SFIExtractionResult
from skg.kgs.validators import verify_sfi_extraction_quality
from skg.schemas import CreateKGConfig
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
    extraction_window: ExtractionWindow,
    kg_config: CreateKGConfig,
    usage_tracker: SFIExtractionUsageTracker,
) -> SFIExtractionResult:
    """Extract SFI candidates from one extraction window using an LLM.

    Parameters
    ----------
    extraction_window
        Source-faithful extraction window to inspect.
    kg_config
        Country/document-specific KG extraction configuration.
    usage_tracker
        Usage tracker to accumulate token usage.

    Returns
    -------
    SFIExtractionResult
        Parsed and quality-validated SFI extraction result.
    """

    prompts = extract_sfi_candidates_from_window(
        extraction_window=extraction_window, kg_config=kg_config
    )
    agent = create_sfi_extraction_agent(
        instructions=prompts.system_message,
        kg_config=kg_config,
        model_config=Settings.llm_config("kgs"),
        verify_quality_fn=verify_sfi_extraction_quality,
        window=extraction_window,
    )
    result = agent.run_sync(prompts.user_message)
    usage_tracker.sfi_extraction.add_run_usage(result.usage())
    return result.output
