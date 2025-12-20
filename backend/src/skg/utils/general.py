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

from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

# Third Party Library
import langcodes

from loguru import logger

# Package Library
from skg.schemas import Valid


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


def stable_text_hash(text: Optional[str]) -> str:
    """Return a deterministic SHA-256 hex digest of normalized text.

    Normalization removes repeated whitespace and trims, so inconsequential formatting
    changes don't change the hash.

    Parameters
    ----------
    text
        The input text to hash.

    Returns
    -------
    str
        The SHA-256 hex digest of the normalized text.
    """

    if text is None:
        text = ""
    norm = re.sub(r"\s+", " ", str(text)).strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


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

    code = (code or "").strip()
    if not code:
        return "unk"
    if code == "unk":
        return code

    code = code.replace("_", "-")

    try:
        lang = langcodes.Language.get(code)
        if not lang.is_valid():
            raise ValueError(f"Invalid BCP-47 language tag: '{code}'")
        return lang.to_tag()
    except langcodes.LanguageTagError as exc:
        raise ValueError(f"Unparseable language tag: '{code}'") from exc


def write_to_json(
    fp: str | Path,
    json_info: dict[str, Any] | list[dict[str, Any]],
    encoding: str = "utf-8",
) -> None:
    """Write data either to .json or .jsonl file. The format is determined by the
    filepath extension.

    Parameters
    ----------
    fp
        Filepath to write the JSON file to.
    json_info
        JSON data to write out.
    encoding
        The encoding scheme for the JSON file.

    Raises
    ------
    ValueError
        If an incorrect suffix is specified for the filepath.
    """

    fp = Path(fp)
    suffix = fp.suffix
    if suffix == ".json":
        with fp.open("w", encoding=encoding) as f:
            json.dump(json_info, f)
    elif suffix == ".jsonl":
        with fp.open("w", encoding=encoding) as f:
            for dict_ in json_info:
                f.write(json.dumps(dict_) + "\n")
    else:
        raise ValueError(
            f"Invalid suffix for writing to JSON: {suffix}. "
            f"Valid suffixes are: '.json' and '.jsonl'"
        )
