"""Rewrite README.md's results from scores/, so a scoring PR needs no hand edits (#162).

Two tables and one snapshot id follow scores/:

- "The record so far": one row per model, pooled, from scores/cumulative.json,
  and the actuals snapshot named in the paragraph above it;
- "Results so far": one row per scored entry, from scores/gwNN_<model>.json.

A logged model with no scores yet keeps the row it has: what it says before
its first scored gameweek ("from GW9") is prose, not a number. Rows stay in
the order the README has them; a model new to scores/ is added at the end.

tests/project/test_readme.py is the independent check: it reads the same
files and fails on any number that disagrees, whoever wrote the table.

Usage, from the repository root:
    python tools/readme_results.py            # rewrite README.md in place
    python tools/readme_results.py --check    # exit 1 if it is out of date
"""

import argparse
import json
import re
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

RECORD_HEADING = "### The record so far"
PER_GAMEWEEK_HEADING = "<b>Results so far</b>"
PER_GAMEWEEK_HEADER = (
    "| Gameweek | Model | MAE | Spearman | `form` MAE | XI points | `form` XI points |",
    "|---|---|---|---|---|---|---|",
)
SNAPSHOT = re.compile(r"\(actuals from snapshot `(\d{8}T\d{6}Z)`")


def three(x):
    """A metric as the README shows it: rounded half up to three places, n/a if absent."""
    if x is None:
        return "n/a"
    return str(Decimal(str(x)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def points(x):
    return "n/a" if x is None else str(x)


def gameweek_list(numbers):
    return ", ".join(f"GW{n}" for n in numbers)


def row(cells):
    """A Markdown table row; an empty cell is written `| |`, as the README has it."""
    return "|" + "|".join(f" {c} " if c else " " for c in cells) + "|"


def table_span(lines, heading):
    """(first row, end) line indices of the body of the first table after `heading`."""
    at = next(i for i, line in enumerate(lines) if heading in line)
    header = next(i for i in range(at, len(lines)) if lines[i].startswith("|"))
    start = header + 2  # past the header and its |---| line
    end = start
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    return start, end


def model_of(line):
    return line.strip("|").split("|")[0].strip().strip("`")


def record_rows(existing, cumulative):
    """The record table's rows: one per model, in the README's order."""
    by_model = {model_of(line): line for line in existing}
    order = list(by_model) + sorted(m for m in cumulative if m not in by_model)
    out = []
    for model in order:
        block = cumulative.get(model)
        if block is None:
            out.append(by_model[model])
            continue
        form = block["comparators"].get("form", {})
        out.append(
            row(
                [
                    f"`{model}`",
                    block["role"],
                    gameweek_list(block["gameweeks_scored"]),
                    gameweek_list(block["gameweeks_missed"]),
                    three(block["pooled"]["all"]["mae"]),
                    three(form.get("mae")),
                    points(block["xi"]["captured"]),
                    points(form.get("xi_actual")),
                ]
            )
        )
    return out


def per_gameweek_rows(entries):
    """One row per scored entry, by gameweek, then model."""
    out = []
    for e in sorted(entries, key=lambda e: (e["gameweek"], e["model_version"])):
        form = e["comparators"].get("form", {})
        captured = e["xi"]["captured"]["actual"] if e["xi"]["available"] else None
        out.append(
            row(
                [
                    f"GW{e['gameweek']}",
                    f"`{e['model_version']}`",
                    three(e["metrics"]["all"]["mae"]),
                    three(e["metrics"]["all"]["spearman"]),
                    three(form.get("mae")),
                    points(captured),
                    points(form.get("xi_actual")),
                ]
            )
        )
    return out


def actuals_snapshot(entries):
    """The one actuals snapshot every score file was scored against."""
    ids = sorted({e["actuals_snapshot_id"] for e in entries})
    if len(ids) != 1:
        raise SystemExit(f"scores/ mixes actuals snapshots {ids}: rescore with one fpl-score run.")
    return ids[0]


def render(text, scores_dir):
    """README text with its results brought into line with `scores_dir`."""
    scores_dir = Path(scores_dir)
    cumulative = json.loads((scores_dir / "cumulative.json").read_text())["models"]
    entries = [json.loads(p.read_text()) for p in sorted(scores_dir.glob("gw*_*.json"))]
    if not entries:
        raise SystemExit(f"No score files in {scores_dir}/.")

    lines = text.split("\n")
    start, end = table_span(lines, RECORD_HEADING)
    lines[start:end] = record_rows(lines[start:end], cumulative)
    start, end = table_span(lines, PER_GAMEWEEK_HEADING)
    lines[start:end] = per_gameweek_rows(entries)
    text = "\n".join(lines)

    section = text.index(RECORD_HEADING)
    match = SNAPSHOT.search(text, section)
    if match is None:
        raise SystemExit(f"No '(actuals from snapshot `...`' after '{RECORD_HEADING}'.")
    snapshot = actuals_snapshot(entries)
    return text[: match.start(1)] + snapshot + text[match.end(1) :]


def cli(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--readme", default="README.md")
    ap.add_argument("--scores", default="scores", help="the season's scores directory")
    ap.add_argument("--check", action="store_true", help="exit 1 if README.md is out of date")
    args = ap.parse_args(argv)
    readme = Path(args.readme)
    before = readme.read_text()
    after = render(before, args.scores)
    if args.check:
        if after != before:
            print(f"{readme} is behind {args.scores}/: run python tools/readme_results.py")
            return 1
        print(f"OK: {readme} matches {args.scores}/.")
        return 0
    if after == before:
        print(f"{readme} already matches {args.scores}/.")
    else:
        readme.write_text(after)
        print(f"Rewrote {readme}'s results from {args.scores}/.")
    return 0


if __name__ == "__main__":
    sys.exit(cli())
