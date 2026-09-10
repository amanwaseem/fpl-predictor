"""Turn a committed prediction log entry into something a person can read.

    python -m fpl.summarise predictions/gw04_baseline-v1.csv
    python -m fpl.summarise predictions/gw04_baseline-v1.csv --out reports/

The log entry is the evidence and has to carry every player and every
intermediate output, which makes it 654 rows of fifteen columns — correct, and
unreadable. This writes the short version beside it: who the model likes, what
it costs, and how much of the entry is players it has written off.

Derived output, like `scores/`. It is regenerated from the entry whenever the
format changes and carries no information the entry does not already have, so
hard rule 1 does not apply to it — the entry it was built from is the thing
that must never move.

Reads the entry alone and never opens a snapshot. `data/raw/` is gitignored, so
a digest that needed one could not be regenerated from a clean checkout months
later, which is exactly when someone auditing the track record wants it.
"""

import argparse
import csv
from collections import Counter
from pathlib import Path

TOP_N = 20
VALUE_N = 10

# Below this a player's points-per-million is noise: a 0.3-point prediction on
# a 4.0m defender outranks every real pick on ratio alone while being nobody's
# actual choice.
VALUE_FLOOR = 2.0

# SPEC section 5 types status as one of `a d i s u n`. Spelled out because the
# single letters are FPL's shorthand and mean nothing to a reader arriving from
# the PR.
STATUS_MEANING = {
    "a": "available",
    "d": "doubtful",
    "i": "injured",
    "s": "suspended",
    "u": "unavailable",
    "n": "not in squad",
}


def _cell(value):
    """Markdown table cells cannot contain a bare pipe."""
    return str(value).replace("|", "\\|")


def _table(headers, aligns, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(aligns) + "|"]
    out += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def read_entry(path):
    """Read a committed entry, with the numbers as numbers.

    Deliberately trusting: this runs on an entry that `fpl.verify_entry` has
    already accepted, so it parses for display rather than re-litigating the
    schema. A digest is not a validator and should not grow into one.
    """
    with Path(path).open(newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"{path} has no rows to summarise.")
    for row in rows:
        for field in ("price", "predicted_points", "expected_minutes",
                      "points_per_90"):
            row[field] = float(row[field])
        row["n_fixtures"] = int(row["n_fixtures"])
    return rows


def summarise(rows):
    """Build the digest as markdown."""
    # A committed entry is already in this order and verify_entry refuses one
    # that is not, so this changes nothing for the real case. It matters for an
    # exploratory run in scratch/, where nothing has checked the order and a
    # silently wrong "Top 20" would be worse than no digest at all.
    rows = sorted(rows, key=lambda r: (-r["predicted_points"],
                                       int(r["player_id"])))
    first = rows[0]
    gameweek = first["gameweek"]
    scoring = [r for r in rows if r["predicted_points"] > 0]
    blanking = [r for r in rows if r["n_fixtures"] == 0]

    parts = [
        f"# GW{gameweek} predictions — {first['model_version']}",
        "",
        f"**Deadline** `{first['deadline_utc']}` · "
        f"**Generated** `{first['generated_at_utc']}` · "
        f"**Snapshot** `{first['snapshot_id']}`",
        "",
        f"{len(rows)} players across {len({r['team'] for r in rows})} teams. "
        f"{len(scoring)} are predicted to score; {len(rows) - len(scoring)} are "
        "on zero — injured, suspended, out of the squad, or not expected to "
        "play enough to matter.",
        "",
        "Generated from the committed entry by `python -m fpl.summarise`. The "
        "entry is the record; this is a reading of it.",
        "",
        f"## Top {TOP_N}",
        "",
    ]

    top = rows[:TOP_N]
    parts.append(_table(
        ["#", "Player", "Team", "Pos", "£m", "Pred", "xMins"],
        ["--:", "---", "---", "---", "--:", "--:", "--:"],
        [[i, r["web_name"], r["team"], r["position"], f"{r['price']:.1f}",
          f"{r['predicted_points']:.2f}", f"{r['expected_minutes']:.0f}"]
         for i, r in enumerate(top, 1)],
    ))

    parts += ["", "## Best in each position", ""]
    best = []
    for position in ("GKP", "DEF", "MID", "FWD"):
        pick = next((r for r in rows if r["position"] == position), None)
        if pick:
            best.append([position, pick["web_name"], pick["team"],
                         f"{pick['price']:.1f}",
                         f"{pick['predicted_points']:.2f}"])
    parts.append(_table(["Pos", "Player", "Team", "£m", "Pred"],
                        ["---", "---", "---", "--:", "--:"], best))

    value = sorted(
        (r for r in rows if r["predicted_points"] >= VALUE_FLOOR),
        key=lambda r: (-(r["predicted_points"] / r["price"]), r["player_id"]),
    )[:VALUE_N]
    if value:
        parts += ["", f"## Best value (predicted points per £m, "
                      f"{VALUE_FLOOR:.1f}+ predicted)", ""]
        parts.append(_table(
            ["Player", "Team", "Pos", "£m", "Pred", "Per £m"],
            ["---", "---", "---", "--:", "--:", "--:"],
            [[r["web_name"], r["team"], r["position"], f"{r['price']:.1f}",
              f"{r['predicted_points']:.2f}",
              f"{r['predicted_points'] / r['price']:.2f}"] for r in value],
        ))

    counts = Counter(r["status"] for r in rows)
    parts += ["", "## Availability at the time of prediction", ""]
    parts.append(_table(
        ["Flag", "Meaning", "Players"], ["---", "---", "--:"],
        [[f"`{flag}`", STATUS_MEANING.get(flag, "unknown"), counts[flag]]
         for flag in sorted(counts, key=lambda f: -counts[f])],
    ))

    if blanking:
        teams = ", ".join(sorted({r["team"] for r in blanking}))
        parts += ["", f"{len(blanking)} players have no GW{gameweek} fixture "
                      f"and are predicted zero: {teams}."]

    parts += [
        "",
        "## How to read this",
        "",
        "`Pred` is expected FPL points for this gameweek. `xMins` is the "
        "minutes the model expects, which is the first thing to check when a "
        "prediction looks wrong — a good scoring rate over too few minutes and "
        "a bad rate over ninety fail in different ways, and the entry keeps "
        "both so a miss can be attributed rather than argued about.",
        "",
        "This is a committed prediction, not advice, and it is not revised "
        "after the fact. It gets scored as it stands.",
        "",
    ]
    return "\n".join(parts)


def main(entry, out_dir=None):
    path = Path(entry)
    if not path.is_file():
        raise SystemExit(f"No entry at {path}.")
    text = summarise(read_entry(path))

    if out_dir is None:
        print(text)
        return 0

    outdir = Path(out_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    outpath = outdir / f"{path.stem}.md"
    outpath.write_text(text)
    print(f"wrote {outpath}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Write the readable digest of a prediction log entry."
    )
    ap.add_argument("entry", help="path to a predictions/gw<NN>_<model>.csv")
    ap.add_argument("--out", default=None, metavar="DIR",
                    help="write <entry>.md here instead of printing")
    args = ap.parse_args()
    raise SystemExit(main(args.entry, args.out))
