"""This module contains utility functions for creating knowledge graphs."""

# Standard Library
import uuid

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Third Party Library
from loguru import logger

# Package Library
from skg.schemas import CreateKGConfig, RunCtx
from skg.utils.general import make_dir, write_to_json


@dataclass(frozen=True)
class KGDirs:
    """Dataclass for KG directories."""

    root: Path
    cache: Path


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
    cache = root / "cache"

    for p in [root, cache]:
        make_dir(p)

    return KGDirs(root=root, cache=cache)


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
