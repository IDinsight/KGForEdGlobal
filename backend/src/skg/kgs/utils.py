"""This module contains utility functions for creating knowledge graphs."""

# Standard Library
import uuid

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Third Party Library
from loguru import logger
from PIL import Image

# Package Library
from skg.schemas import CreateKGConfig, RunCtx
from skg.utils.general import make_dir, write_to_json


@dataclass(frozen=True)
class KGDirs:
    """Dataclass for KG directories."""

    root: Path
    academic_standards: Path
    learning_components: Path
    learning_progressions: Path
    combined: Path


def create_kg_dirs(*, output_dir: Path) -> KGDirs:
    """Create KG directories for a given KG run.

    Parameters
    ----------
    output_dir
        The output directory root.

    Returns
    -------
    KGDirs
        The created KG directories.
    """

    root = output_dir
    academic_standards = root / "academic_standards"
    learning_components = root / "learning_components"
    learning_progressions = root / "learning_progressions"
    combined = root / "combined"

    for p in [
        root,
        academic_standards,
        learning_components,
        learning_progressions,
        combined,
    ]:
        make_dir(p)

    return KGDirs(
        root=root,
        academic_standards=academic_standards,
        learning_components=learning_components,
        learning_progressions=learning_progressions,
        combined=combined,
    )


def persist_kg_run(
    *, config: CreateKGConfig, output_dir: Path
) -> tuple[KGDirs, RunCtx]:
    """Persist KG run metadata.

    Parameters
    ----------
    config
        The KG creation run configuration.
    output_dir
        The output directory for the KG run results.

    Returns
    -------
    tuple[KGDirs, RunCtx]
        The created KG directories and persisted KG run metadata.
    """

    kg_dirs = create_kg_dirs(output_dir=output_dir)
    exclude_keys = {"model", "overwrite"}
    kg_run = RunCtx(
        extra={k: v for k, v in config.model_dump().items() if k not in exclude_keys},
        run_id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
    )
    write_to_json(fp=output_dir / "kg_run.json", json_info=kg_run)
    logger.info(f"Saving KG creation results to: {kg_dirs}")

    return kg_dirs, kg_run
