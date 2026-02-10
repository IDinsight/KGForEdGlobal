"""This module contains general utilities.

NB: As a general rule of thumb, this module should not import utilities from other
utils modules (in order to avoid circular imports). If a utility function is needed in
multiple modules, then it is a general utility and should be defined in this module
instead.
"""

# Standard Library
import base64
import hashlib
import json
import re
import unicodedata

from copy import deepcopy
from pathlib import Path
from typing import Any, Literal, Optional

# Third Party Library
import langcodes

from loguru import logger
from pydantic import BaseModel, ConfigDict

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


def bbox_contains(*, inner: list[float], outer: list[float], tol: float = 2.0) -> bool:
    """Return True if `inner` bbox is fully contained in `outer` bbox (with tolerance).

    Parameters
    ----------
    inner
        The inner bounding box [x0, y0, x1, y1].
    outer
        The outer bounding box [x0, y0, x1, y1].
    tol
        Tolerance in pixels.

    Returns
    -------
    bool
        True if `inner` is contained in `outer`, False otherwise.
    """

    ox0, oy0, ox1, oy1 = outer
    ix0, iy0, ix1, iy1 = inner

    return (
        ix0 >= ox0 - tol and iy0 >= oy0 - tol and ix1 <= ox1 + tol and iy1 <= oy1 + tol
    )


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


def compute_sha256_hex(*, n_hex: int = 16, s: str) -> str:
    """Compute the SHA-256 hex digest of a string and return the first `n_hex`
    characters.

    Parameters
    ----------
    n_hex
        Number of hex characters to return from the digest.
    s
        The input string to hash.

    Returns
    -------
    str
        The first `n_hex` characters of the SHA-256 hex digest of `s`.
    """

    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:n_hex]


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


def escape_angle_brackets(x: Any) -> str:
    """Escape angle brackets for colorized logging. If this is not done, then
    `loguru` will throw a `ValueError` when attempting to log objects with angle
    brackets. See: https://github.com/Delgan/loguru/issues/140 for more details.

    Parameters
    ----------
    x
        Any object.

    Returns
    -------
    str
        The string version of `x` with escaped angle brackets.
    """

    return recurse_replace(r"\>", ">", recurse_replace(r"\<", "<", str(x)))


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


def normalize_text(text: Optional[str]) -> str:
    """Normalize text for comparisons.

    Parameters
    ----------
    text
        The text to normalize.

    Returns
    -------
    str
        The normalized text.
    """

    if text is None:
        return ""

    # Normalize unicode characters (e.g., standardize accents). NFKC form is usually
    # best for compatibility comparisons.
    text = unicodedata.normalize("NFKC", text)

    # Collapse whitespace, strip, and lowercase.
    return re.sub(r"\s+", " ", text).strip().lower()


def open_json_type(filepath: str | Path) -> Any:
    """Helper function to open JSON-type files. This includes JSON, JSONL, JSONNET, and
    YAML file types.

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

    _TOKEN_RE = re.compile(
        r"(?i)\b(?:bearer\s+)?([A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+)"
    )

    record = deepcopy(record)
    record["message"] = _TOKEN_RE.sub("<redacted>", record["message"])

    # Redact the same way inside extra dict if user code added a header dump.
    if "headers" in record["extra"]:
        record["extra"]["headers"] = _TOKEN_RE.sub(
            "<redacted>", str(record["extra"]["headers"])
        )

    return record


def truncate_text(*, max_chars: int, text: str) -> str:
    """Return a single-line truncated preview string.

    Parameters
    ----------
    max_chars
        The maximum number of characters to return (including ellipsis).
    text
        The text to truncate.

    Returns
    -------
    str
        The truncated text.
    """

    text = (text or "").replace("\n", " ").strip()

    return (
        text if len(text) <= max_chars else text[: max(0, max_chars - 1)].rstrip() + "…"
    )


def validate_bbox_order(bbox: list[float]) -> list[float]:
    """Ensure bbox is well-ordered: [x0, y0, x1, y1] with x0 < x1 and y0 < y1.

    Parameters
    ----------
    bbox
        The bounding box to validate.

    Returns
    -------
    list[float]
        The validated bounding box.

    Raises
    ------
    ValueError
        If the bounding box does not have exactly 4 numbers.
    """

    if len(bbox) != 4:
        raise ValueError(
            f"Bounding box must have exactly 4 numbers: [x0, y0, x1, y1]. Got: {bbox}"
        )

    x0, y0, x1, y1 = bbox

    # Auto-correct inverted or zero-dimension axes. For equal dimensions, add 1 pixel.
    if x0 >= x1:
        if x0 > x1:
            x0, x1 = x1, x0
        else:
            x1 = x0 + 1.0
    if y0 >= y1:
        if y0 > y1:
            y0, y1 = y1, y0
        else:
            y1 = y0 + 1.0

    return [x0, y0, x1, y1]


def validate_bcp47(code: str) -> str:
    """Validates that a string is a valid BCP-47 language tag.

    Parameters
    ----------
    code
        The language tag to validate.

    Returns
    -------
    str
        The standardized version (e.g., 'en_us' -> 'en-US').

    Raises
    ------
    ValueError
        If the language tag is invalid or unparseable.
    """

    code = (code or "und").strip().replace("_", "-")
    if code in {"und", "mul"}:
        return code

    try:
        lang = langcodes.Language.get(code)
        if not lang.is_valid():
            raise ValueError(f"Invalid BCP-47 language tag: '{code}'")
        return lang.to_tag()
    except langcodes.LanguageTagError as exc:
        raise ValueError(f"Unparseable language tag: '{code}'") from exc


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
