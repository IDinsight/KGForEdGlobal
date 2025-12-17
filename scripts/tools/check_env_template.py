#!/usr/bin/env python3

# Standard Library
import sys

from pathlib import Path

# Folders that have paired .env.* / .template.env.* files. These paths are relative to
# the location of the .pre-commit-config.yaml file.
SECTIONS = [
    Path("."),
    Path("backend"),
    Path("cicd/deployment/docker-compose"),
    Path("frontend"),
]

# Specify which file pairs to compare in each section.
FILE_PAIRS = [
    (".env", ".template.env"),
    (".env.local", ".template.env.local"),
    (".env.development", ".template.env.development"),
    (".env.production", ".template.env.production"),
]


def keys(*, path: Path) -> set[str]:
    """Return the set of variable names (strings before the first '=') from a
    .env-style file, ignoring blank lines and comments.

    Parameters
    ----------
    path
        The path to the .env file to read.

    Returns
    -------
    set[str]
        A set of variable names found in the file.
    """

    out = set()
    with path.open() as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            out.add(line.split("=", 1)[0].strip())
    return out


def main() -> int:
    """Check that each .env.* file has a corresponding .template.env.* file with the
    same variable names, and vice versa.

    Returns
    -------
    int
        0 if no errors were found, 1 if there were errors.
    """

    had_errors = False

    for section in SECTIONS:
        for env_name, template_name in FILE_PAIRS:
            env_file = section / env_name
            template_file = section / template_name

            if not (env_file.is_file() and template_file.is_file()):
                print(
                    f"Skipping {section} ({env_name} <-> {template_file}) – missing one or both files"
                )
                continue

            env_keys = keys(path=env_file)
            template_keys = keys(path=template_file)

            missing_in_template = env_keys - template_keys
            extra_in_template = template_keys - env_keys

            rel = section.as_posix() or "."

            if missing_in_template:
                had_errors = True
                print(
                    f"\n{rel} – The following variables are in .env.* but missing in "
                    f"its corresponding .template.env.*:"
                )
                for k in sorted(missing_in_template):
                    print(f"  {k}")

            if extra_in_template:
                had_errors = True
                print(
                    f"\n{rel} – The following variables are in .template.env.* but not "
                    f"in its corresponding .env.*:"
                )
                for k in sorted(extra_in_template):
                    print(f"  {k}")

    return 1 if had_errors else 0


if __name__ == "__main__":
    sys.exit(main())
