"""This module contains general utilities.

NB: As a general rule of thumb, this module should not import utilities from other
utils modules (in order to avoid circular imports). If a utility function is needed in
multiple modules, then it is a general utility and should be defined in this module
instead.
"""

# Future Library
from __future__ import annotations

# Standard Library
import base64
import json
import re

from copy import deepcopy
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Literal

# Third Party Library
from loguru import logger
from pydantic import BaseModel, ConfigDict

_TOKEN_RE = re.compile(
    r"(?i)\b(?:bearer\s+)?([A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+)"
)
QUOTES_TRANSLATION = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "’": "'",
        "‘": "'",
        "‚": "'",
        "‛": "'",
        "\u00a0": " ",  # NBSP -> space
    }
)


@dataclass(frozen=True)
class PipelineDirs:
    """Manages all pipeline directories."""

    root: Path
    page_images: Path
    page_irs: Path
    page_irs_raw: Path

    def __post_init__(self) -> None:
        """Ensure all fields are Path objects and create the directories if they don't
        exist.
        """

        for field in fields(self):
            dirpath = getattr(self, field.name)
            assert isinstance(
                dirpath, Path
            ), f"Expected Path for {field.name}, got {type(dirpath)}"
            make_dir(dirpath)

    @classmethod
    def create_from_root(cls, root_path: str | Path) -> PipelineDirs:
        """Create a PipelineDirs instance from a root path.

        Parameters
        ----------
        root_path
            The root directory path for the pipeline.

        Returns
        -------
        PipelineDirs
            The created PipelineDirs instance with subdirectories.
        """

        root = Path(root_path)
        return cls(
            root=root,
            page_images=root / "page_images",
            page_irs=root / "page_irs",
            page_irs_raw=root / "page_irs_raw",
        )


@dataclass(frozen=True)
class PromptPair:
    """Immutable pair of system and user messages for an LLM prompt."""

    system_message: str | None
    user_message: str | None


class Valid(BaseModel):
    """Pydantic model for global valid values."""

    completion_finish_reasons: tuple[
        Literal[None, "function_call", "length", "stop"], ...
    ] = (None, "function_call", "length", "stop")
    json_file_exts: tuple[Literal[".json", ".jsonl"], ...] = (".json", ".jsonl")
    logging_levels: tuple[
        Literal["CRITICAL", "DEBUG", "ERROR", "INFO", "WARNING"], ...
    ] = ("CRITICAL", "DEBUG", "ERROR", "INFO", "WARNING")

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    @classmethod
    def is_valid_completion_finish_reason(
        cls, *, completion_finish_reason: str
    ) -> bool:
        """Check if a given completion finish reason is valid.

        Parameters
        ----------
        completion_finish_reason
            The completion finish reason to check.

        Returns
        -------
        bool
            True if the completion finish reason is valid, False otherwise.
        """

        return completion_finish_reason in cls().completion_finish_reasons

    @classmethod
    def is_valid_json_file_ext(cls, *, file_ext: str) -> bool:
        """Check if a given JSON file extension is valid.

        Parameters
        ----------
        file_ext
            The file extension to check.

        Returns
        -------
        bool
            True if the file extension is valid, False otherwise.
        """

        return file_ext in cls().json_file_exts

    @classmethod
    def is_valid_logging_level(cls, *, logging_level: str) -> bool:
        """Check if a given logging level is valid.

        Parameters
        ----------
        logging_level
            The logging level to check.

        Returns
        -------
        bool
            True if the logging level is valid, False otherwise.
        """

        return logging_level in cls().logging_levels


def compare_directories(dir1_path: str | Path, dir2_path: str | Path) -> bool:
    """Compare two directories to see if they contain the same files (ignoring file
    extensions).

    Parameters
    ----------
    dir1_path
        Path to the first directory.
    dir2_path
        Path to the second directory.

    Returns
    -------
    bool
        True if the directories contain the same files (ignoring extensions), False
        otherwise.
    """

    p1 = Path(dir1_path)
    p2 = Path(dir2_path)

    # Use f.is_file() to ensure we don't accidentally count folders.
    files1 = [f for f in p1.iterdir() if f.is_file()]
    files2 = [f for f in p2.iterdir() if f.is_file()]

    # Check if file counts are different.
    if len(files1) != len(files2):
        logger.warning(
            f"Mismatch: Directory 1 ({dir1_path}) has {len(files1)} files, "
            f"Directory 2 ({dir2_path}) has {len(files2)} files."
        )
        return False

    # Extract "stems" (filenames without suffixes) and sort them so that we can ensure
    # ['a', 'b'] matches ['b', 'a'].
    stems1 = sorted([f.stem for f in files1])
    stems2 = sorted([f.stem for f in files2])

    if stems1 == stems2:
        logger.info("Success: Directories match!")
        return True

    set1, set2 = set(stems1), set(stems2)
    logger.error("Mismatch: The file counts are the same, but the names differ.")
    logger.error(f"Unique to Dir 1 ({dir1_path}): {set1 - set2}")
    logger.error(f"Unique to Dir 2 ({dir2_path}): {set2 - set1}")

    return False


def encode_png_to_data_url(png_fp: Path) -> str:
    """Encode a PNG file to a base64 data URL.

    Parameters
    ----------
    png_fp
        Path to the PNG file.

    Returns
    -------
    str
        The base64 data URL of the PNG file.
    """

    b64 = base64.b64encode(png_fp.read_bytes()).decode("utf-8")
    return f"data:image/png;base64,{b64}"


def make_dir(dir_: str | Path, mode: int = 0o777, verbose: bool = True) -> None:
    """Create a directory.

    Parameters
    ----------
    dir_
        Directory to create.
    mode
        The mode to set on the directory. Defaults to `0o777` (read, write, and
        execute for everyone).
    verbose
        Specifies whether to log directory creation.
    """

    dir_ = Path(dir_)
    if not Path.is_dir(dir_):
        if verbose:
            logger.info(f"Creating directory: {dir_}")
        Path.mkdir(dir_, exist_ok=True, mode=mode, parents=True)
        if verbose:
            logger.success(f"Created directory: {dir_}")


def open_json_type(filepath: str | Path) -> Any:
    """Helper function to open JSON-type files. This includes JSON and JSONL file types.

    Parameters
    ----------
    filepath
        Path to the file to be loaded.

    Returns
    -------
    Any
        Contains (key, value) pairs from the file specified by `filepath`. This can
        either be a dictionary or a list of dictionaries.

    Raises
    ------
    RuntimeError
        If an error occurs when loading a .jsonnet file.
    ValueError
        If an error occurs when loading a .json or YAML file.
    """

    filepath = Path(filepath)
    assert Path.is_file(filepath)
    file_ext = filepath.suffix
    assert Valid.is_valid_json_file_ext(file_ext=file_ext)
    if file_ext == ".json":
        with filepath.open("r", encoding="utf-8") as f:
            dict_ = json.load(f)
        return dict_
    with filepath.open("r", encoding="utf-8") as f:
        json_list = list(f)
    return [json.loads(json_str) for json_str in json_list]


def recurse_replace(new_str: str, orig_str: str, x: Any) -> Any:
    """Recursively replace all instances of `orig_str` in `x` with the value specified
    by `new_str`.

    Parameters
    ----------
    new_str
        The replacement string.
    orig_str
        The original string.
    x
        Either a string, list, or dictionary. This object will be recursively scanned
        in order to replace all instances of `orig_str` with `new_str`.

    Returns
    -------
    Any
        The final return of this function is the original passed in `x` with all
        instances of `orig_str` replaced with `new_str`.

    """

    if isinstance(x, str) and orig_str in x:
        return x.replace(orig_str, new_str)
    if isinstance(x, list):
        for i, item in enumerate(x):
            x[i] = recurse_replace(new_str, orig_str, x=item)
    elif isinstance(x, dict):
        for k, v in list(x.items()):
            k_ = recurse_replace(new_str, orig_str, x=k)
            x.pop(k)
            x[k_] = recurse_replace(new_str, orig_str, x=v)
    return x


def redact_tokens(record: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow-copied record with JWTs / Bearer tokens replaced.

    Parameters
    ----------
    record
        The log record to redact.

    Returns
    -------
    dict[str, Any]
        The log record with tokens redacted.
    """

    record = deepcopy(record)
    record["message"] = _TOKEN_RE.sub("<redacted>", record["message"])

    # Redact the same way inside extra dict if user code added a header dump.
    if "headers" in record["extra"]:
        record["extra"]["headers"] = _TOKEN_RE.sub(
            "<redacted>", str(record["extra"]["headers"])
        )

    return record


def write_to_json(
    *,
    encoding: str = "utf-8",
    fp: str | Path,
    indent: int = 2,
    json_info: dict[str, Any] | list[dict[str, Any]] | BaseModel | list[BaseModel],
) -> None:
    """Write data either to .json or .jsonl file. The format is determined by the
    filepath extension.

    Parameters
    ----------
    encoding
        The encoding scheme for the JSON file.
    fp
        Filepath to write the JSON file to.
    indent
        The number of spaces to use for indentation in the JSON file.
    json_info
        JSON data to write out or Pydantic BaseModel instance(s).

    Raises
    ------
    ValueError
        If an incorrect suffix is specified for the filepath.
    """

    fp = Path(fp)
    suffix = fp.suffix

    if suffix == ".json":
        # Single Pydantic model.
        if isinstance(json_info, BaseModel):
            fp.write_text(json_info.model_dump_json(indent=indent), encoding=encoding)
            return

        # List of Pydantic models (convert to list of dicts for json.dump).
        if (
            isinstance(json_info, list)
            and json_info
            and isinstance(json_info[0], BaseModel)
        ):
            json_info = [m.model_dump() for m in json_info]  # type: ignore

        # Standard dict or list[dict].
        with fp.open("w", encoding=encoding) as f:
            json.dump(json_info, f, indent=indent)
    elif suffix == ".jsonl":
        items = [json_info] if isinstance(json_info, (dict, BaseModel)) else json_info
        with fp.open("w", encoding=encoding) as f:
            for item in items:
                # Use Pydantic's serializer for models.
                if isinstance(item, BaseModel):
                    f.write(item.model_dump_json() + "\n")
                else:
                    f.write(json.dumps(item) + "\n")
    else:
        raise ValueError(
            f"Invalid suffix for writing to JSON: {suffix}. "
            f"Valid suffixes are: '.json' and '.jsonl'"
        )
