"""This module contains utility functions for page Intermediate Representations (IRs)."""

# Standard Library
from dataclasses import dataclass
from pathlib import Path

# Package Library
from skg.utils.general import make_dir


@dataclass(frozen=True)
class PageIRExtractionDirs:
    """Dataclass for page IR extraction directories."""

    root: Path
    artifacts: Path
    page_images: Path
    page_ir: Path


def create_page_ir_extraction_dirs(
    *, doc_key: str, output_dir: Path
) -> PageIRExtractionDirs:
    """Create page IR extraction directories for a given document key.

    Parameters
    ----------
    doc_key
        The document key.
    output_dir
        The output directory root.

    Returns
    -------
    PageIRExtractionDirs
        The created page IR extraction directories.
    """

    root = output_dir / doc_key
    artifacts = root / "artifacts"
    page_images = root / "page_images"
    page_ir = root / "page_ir"

    for p in [root, page_images, page_ir, artifacts]:
        make_dir(p)

    return PageIRExtractionDirs(
        root=root, artifacts=artifacts, page_images=page_images, page_ir=page_ir
    )
