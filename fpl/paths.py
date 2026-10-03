"""Where the commands run: the repository root, and nowhere else.

Every path in this project is relative to the working directory —
`data/raw/LATEST`, `predictions/`, `scores/`, `scratch/`. That is deliberate:
it is what lets the tests run a whole season inside a temporary directory, and
it keeps every file landing exactly where it always has.

What it cannot survive is a command run from somewhere else. Installed console
scripts work from any directory, and from the wrong one `fpl-fetch` would
quietly start a second `data/raw/` with its own LATEST, and a predictor would
write a `predictions/` that is not the log. Neither is an error by itself, so
each command that touches those paths checks first and refuses instead.

The root is recognised by its pyproject.toml naming this project, not by
walking upward to find it: an entry path typed relative to a subdirectory and
a default resolved against the root would be two anchors in one command.
"""

import tomllib
from pathlib import Path

# Must match [project] name in pyproject.toml; tests/test_paths.py checks it.
PROJECT_NAME = "fpl-predictor"


def _project_name(directory):
    try:
        with (directory / "pyproject.toml").open("rb") as f:
            return tomllib.load(f).get("project", {}).get("name")
    except (OSError, tomllib.TOMLDecodeError):
        return None


def require_project_root():
    """Refuse to run unless the working directory is the repository root."""
    cwd = Path.cwd()
    if _project_name(cwd) != PROJECT_NAME:
        raise SystemExit(
            f"Run this from the repository root, not {cwd}.\n"
            "Snapshots, predictions/ and scores/ are found relative to the "
            "working directory, so from anywhere else this would read or write "
            "the wrong place. cd to the directory holding this project's "
            "pyproject.toml and run it again."
        )
