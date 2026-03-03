"""This is the main module for testing utils/general.py."""

# Standard Library
import json

from copy import deepcopy
from pathlib import Path
from typing import Any

# Third Party Library
import pytest

from pydantic import BaseModel

# Package Library
from skg.utils.general import (
    compare_directories,
    open_json_type,
    recurse_replace,
    redact_tokens,
    write_to_json,
)


class _ExampleModel(BaseModel):
    """Example Pydantic model used to validate `write_to_json` behavior."""

    a: int
    b: str


def _read_json(fp: Path) -> Any:
    """Read a JSON file and return its decoded Python value.

    Parameters
    ----------
    fp
        Path to the JSON file.

    Returns
    -------
    Any
        Parsed JSON value.
    """

    return json.loads(fp.read_text(encoding="utf-8"))


def _read_jsonl(fp: Path) -> list[Any]:
    """Read a JSONL file and return a list of decoded JSON objects.

    Parameters
    ----------
    fp
        Path to the JSONL file.

    Returns
    -------
    list[Any]
        Parsed JSON objects (one per line).
    """

    lines = fp.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _touch_file(*, fp: Path, text: str = "") -> None:
    """Create a file at the given path with the given content.

    Parameters
    ----------
    fp
        Filepath to create.
    text
        Text content to write.
    """

    fp.write_text(text, encoding="utf-8")


def test_compare_directories_returns_false_on_count_mismatch(
    fixture_loguru_capture: list[str], tmp_path: Path
) -> None:
    """If directory file counts differ, the function returns False and logs a warning.

    Parameters
    ----------
    fixture_loguru_capture
        Captured log messages from the `loguru` logger during the test.
    tmp_path
        Temporary directory provided by pytest for creating test files and directories.
    """

    dir1 = tmp_path / "dir1"
    dir2 = tmp_path / "dir2"
    dir1.mkdir()
    dir2.mkdir()

    _touch_file(fp=dir1 / "a.txt", text="one")
    _touch_file(fp=dir1 / "b.txt", text="two")
    _touch_file(fp=dir2 / "a.txt", text="one")

    assert compare_directories(dir1_path=dir1, dir2_path=dir2) is False
    assert any("Mismatch: Directory 1" in msg for msg in fixture_loguru_capture)
    assert any("has 2 files" in msg for msg in fixture_loguru_capture)
    assert any("has 1 files" in msg for msg in fixture_loguru_capture)


def test_compare_directories_returns_false_on_name_mismatch(
    fixture_loguru_capture: list[str], tmp_path: Path
) -> None:
    """If file counts match but stems differ, the function returns False and logs
    details.

    Parameters
    ----------
    fixture_loguru_capture
        Captured log messages from the `loguru` logger during the test.
    tmp_path
        Temporary directory provided by pytest for creating test files and directories.
    """

    dir1 = tmp_path / "dir1"
    dir2 = tmp_path / "dir2"
    dir1.mkdir()
    dir2.mkdir()

    _touch_file(fp=dir1 / "a.txt", text="one")
    _touch_file(fp=dir1 / "b.txt", text="two")
    _touch_file(fp=dir2 / "a.md", text="one")
    _touch_file(fp=dir2 / "c.md", text="two")

    assert compare_directories(dir1_path=dir1, dir2_path=dir2) is False

    joined = "\n".join(fixture_loguru_capture)
    assert "Mismatch: The file counts are the same, but the names differ." in joined
    assert "Unique to Dir 1" in joined
    assert "Unique to Dir 2" in joined
    assert "b" in joined
    assert "c" in joined


def test_compare_directories_returns_true_when_stems_match(
    fixture_loguru_capture: list[str], tmp_path: Path
) -> None:
    """Directories match when stems are identical, regardless of file extensions.

    This test also asserts that subdirectories are ignored (only files count).

    Parameters
    ----------
    fixture_loguru_capture
        Captured log messages from the `loguru` logger during the test.
    tmp_path
        Temporary directory provided by pytest for creating test files and directories.
    """

    dir1 = tmp_path / "dir1"
    dir2 = tmp_path / "dir2"
    dir1.mkdir()
    dir2.mkdir()

    _touch_file(fp=dir1 / "a.txt", text="one")
    _touch_file(fp=dir1 / "b.json", text="{}")
    _touch_file(fp=dir2 / "a.csv", text="1,2,3")
    _touch_file(fp=dir2 / "b.md", text="# hi")

    # Subdirectories should be ignored.
    (dir1 / "subdir").mkdir()
    (dir2 / "subdir").mkdir()
    _touch_file(fp=dir1 / "subdir" / "ignored.txt", text="x")
    _touch_file(fp=dir2 / "subdir" / "ignored.txt", text="x")

    assert compare_directories(dir1_path=dir1, dir2_path=dir2) is True
    assert any("Success: Directories match!" in msg for msg in fixture_loguru_capture)


def test_open_json_type_raises_on_invalid_suffix(tmp_path: Path) -> None:
    """`open_json_type` rejects file extensions outside of {`.json`, `.jsonl`}.

    Parameters
    ----------
    tmp_path
        Temporary directory provided by pytest for creating test files.
    """

    fp = tmp_path / "data.txt"
    fp.write_text("not json", encoding="utf-8")

    with pytest.raises(expected_exception=AssertionError):
        _ = open_json_type(filepath=fp)


def test_open_json_type_raises_on_missing_file(tmp_path: Path) -> None:
    """`open_json_type` asserts the file exists.

    Parameters
    ----------
    tmp_path
        Temporary directory provided by pytest for creating test files.
    """

    fp = tmp_path / "missing.json"

    with pytest.raises(expected_exception=AssertionError):
        _ = open_json_type(filepath=fp)


def test_open_json_type_reads_json(tmp_path: Path) -> None:
    """`open_json_type` loads a `.json` file as a dictionary.

    Parameters
    ----------
    tmp_path
        Temporary directory provided by pytest for creating test files.
    """

    fp = tmp_path / "data.json"
    fp.write_text(json.dumps({"a": 1, "b": [1, 2]}), encoding="utf-8")
    assert open_json_type(filepath=fp) == {"a": 1, "b": [1, 2]}


def test_open_json_type_reads_jsonl(tmp_path: Path) -> None:
    """`open_json_type` loads a `.jsonl` file as a list of dictionaries.

    Parameters
    ----------
    tmp_path
        Temporary directory provided by pytest for creating test files.
    """

    fp = tmp_path / "data.jsonl"
    fp.write_text('{"i": 1}\n{"i": 2}\n', encoding="utf-8")
    assert open_json_type(filepath=fp) == [{"i": 1}, {"i": 2}]


def test_recurse_replace_handles_key_collisions_last_write_wins() -> None:
    """If replacing keys causes a collision, the later key overwrites the earlier one.

    This documents the current behavior of the in-place pop/reinsert implementation.
    """

    x: dict[str, Any] = {"bar": 2, "foo": 1}
    result = recurse_replace(new_str="bar", orig_str="foo", x=x)
    assert result == {"bar": 1}


def test_recurse_replace_replaces_in_nested_containers_in_place() -> None:
    """`recurse_replace` mutates lists/dicts in-place and replaces inside dict keys."""

    x: list[Any] = [
        "foo",
        {"foo": "barfoo", "nested": ["xfoo", {"kfoo": "y"}]},
        123,
        None,
    ]
    x_id = id(x)
    result = recurse_replace(new_str="baz", orig_str="foo", x=x)
    assert id(result) == x_id
    assert result == [
        "baz",
        {"baz": "barbaz", "nested": ["xbaz", {"kbaz": "y"}]},
        123,
        None,
    ]


def test_redact_tokens_redacts_message_and_headers_without_mutating_input() -> None:
    """`redact_tokens` deep-copies input and redacts JWT/Bearer tokens in message and
    headers.
    """

    token1 = "aa-bb_cc.11-22_33.dd-ee_ff"
    token2 = "xxYYzz.abcDEF.123_456-789"
    record_in: dict[str, Any] = {
        "extra": {
            "headers": {
                "Authorization": f"Bearer {token1}",
                "X-Other": "ok",
            }
        },
        "message": f"Got {token1} and Bearer {token2} in the logs",
        "other": 123,
    }
    record_before = deepcopy(record_in)

    record_out = redact_tokens(record=record_in)

    # Input unchanged.
    assert record_in == record_before
    assert isinstance(record_in["extra"]["headers"], dict)

    # Output is a deep-copied structure.
    assert record_out is not record_in
    assert record_out["extra"] is not record_in["extra"]

    # Message redaction: both tokens should be removed.
    assert "<redacted>" in record_out["message"]
    assert token1 not in record_out["message"]
    assert token2 not in record_out["message"]

    # Header redaction: becomes a string (by design) and is redacted.
    assert isinstance(record_out["extra"]["headers"], str)
    assert "<redacted>" in record_out["extra"]["headers"]
    assert token1 not in record_out["extra"]["headers"]


def test_redact_tokens_skips_headers_if_absent_without_error() -> None:
    """If no headers are present, only the message should be redacted."""

    token = "aa-bb_cc.11-22_33.dd-ee_ff"
    record_in: dict[str, Any] = {"extra": {}, "message": f"Bearer {token}"}
    record_out = redact_tokens(record=record_in)
    assert record_out["extra"] == {}
    assert record_out["message"] == "<redacted>"


def test_write_to_json_raises_on_invalid_suffix(tmp_path: Path) -> None:
    """`write_to_json` rejects suffixes other than `.json` and `.jsonl`.

    Parameters
    ----------
    tmp_path
        Temporary directory provided by pytest for creating test files.
    """

    fp = tmp_path / "bad.txt"

    with pytest.raises(expected_exception=ValueError):
        write_to_json(encoding="utf-8", fp=fp, indent=2, json_info={"a": 1})


def test_write_to_json_roundtrips_dict_via_open_json_type(tmp_path: Path) -> None:
    """Writing a dict to `.json` can be loaded back via `open_json_type` unchanged.

    Parameters
    ----------
    tmp_path
        Temporary directory provided by pytest for creating test files.
    """

    fp = tmp_path / "data.json"
    data = {"a": 1, "b": {"c": "d"}}
    write_to_json(encoding="utf-8", fp=fp, indent=2, json_info=data)
    assert fp.is_file()
    assert open_json_type(filepath=fp) == data


def test_write_to_json_roundtrips_list_of_dicts_via_open_json_type(
    tmp_path: Path,
) -> None:
    """Writing a list of dicts to `.jsonl` can be loaded back via `open_json_type`
    unchanged.

    Parameters
    ----------
    tmp_path
        Temporary directory provided by pytest for creating test files.
    """

    fp = tmp_path / "data.jsonl"
    data = [{"i": 1}, {"i": 2}]
    write_to_json(encoding="utf-8", fp=fp, indent=2, json_info=data)
    assert fp.is_file()
    assert open_json_type(filepath=fp) == data


def test_write_to_json_writes_list_of_pydantic_models_to_jsonl(tmp_path: Path) -> None:
    """A list of Pydantic models written to `.jsonl` should be one JSON object per line.

    Parameters
    ----------
    tmp_path
        Temporary directory provided by pytest for creating test files.
    """

    fp = tmp_path / "models.jsonl"
    models = [_ExampleModel(a=1, b="x"), _ExampleModel(a=2, b="y")]
    write_to_json(encoding="utf-8", fp=fp, indent=2, json_info=models)
    assert _read_jsonl(fp=fp) == [m.model_dump() for m in models]


def test_write_to_json_writes_pydantic_model_to_json(tmp_path: Path) -> None:
    """A single Pydantic model written to `.json` should parse back to `model_dump()`.

    Parameters
    ----------
    tmp_path
        Temporary directory provided by pytest for creating test files.
    """

    fp = tmp_path / "model.json"
    model = _ExampleModel(a=1, b="x")
    write_to_json(encoding="utf-8", fp=fp, indent=2, json_info=model)
    assert _read_json(fp=fp) == model.model_dump()
