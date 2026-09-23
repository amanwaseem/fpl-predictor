"""form-fixture-v1: recent form, last season, and who each side is playing.

The first model logged alongside baseline-v1 (#32). It keeps baseline-v1's
minutes and recent window, and changes two things:

- **The prior** (#30). A player's rate is shrunk toward his own last season —
  where it rests on 2700+ minutes — instead of a positional guess, at a weight
  that fades as this season's minutes accumulate.
- **The fixture** (#31). The rate is split into attacking returns, clean-sheet
  points and the rest. Attacking returns scale with his side's expected goals
  in this fixture relative to its usual; clean-sheet points become
  P(clean sheet) x P(60+ minutes) x the position's points; the rest (bonus,
  saves, defensive contribution, cards, conceding) stays at his shrunk rate.

Judged held out before it was logged (SPEC section 6): tuned on three of
GW2-5 and scored on the fourth, it beat baseline-v1 on pooled MAE (1.117 vs
1.138) and Spearman (0.715 vs 0.712). The settings are features' and teams'
constants, frozen for the log on 6 Oct.

`points_per_90` in the log is the shrunk rate before the fixture is applied,
so it keeps baseline-v1's meaning; `predicted_points` is after.

Usage, as fpl.predict_baseline:
    python -m fpl.predict_fixture                 # predict the next gameweek
    python -m fpl.predict_fixture --gw 6          # a specific gameweek
    python -m fpl.predict_fixture --out scratch/  # exploratory, not the log

Run from the repository root.
"""

import argparse
import json

from fpl.features import (
    CLEAN_SHEET_POINTS,
    PLAYER_PRIOR_MINUTES,
    POSITION_PRIOR_MINUTES,
    POSITION_PRIOR_PP90,
    attack_points,
    chance_of_sixty,
    clean_sheet_points,
    expected_minutes,
    player_prior_parts,
    positional_rates,
    previous_season,
    prior,
    recent_history,
    recent_rows,
    season_minutes,
    split_rates,
)
from fpl.log import write_entry
from fpl.snapshot import fixture_counts, load_snapshot, resolve_target_gw, usable_rounds
from fpl.teams import DifficultyRatings, Ratings, clean_sheet_probability, team_matches

MODEL_VERSION = "form-fixture-v1"


def predict_rows(bootstrap, fixtures, histories, pasts, target_gw, usable, *,
                 strength="xg", last_season=True,
                 player_prior_minutes=PLAYER_PRIOR_MINUTES,
                 floor=None, xg_weight=None, prior_matches=None):
    """Predictions for every player, as log rows without provenance.

    The whole model, separated from where its inputs come from, as in
    baseline-v1: main() feeds it a snapshot, the backtest a snapshot cut back
    to an earlier deadline. `pasts` maps player id to history_past rows.

    The keyword arguments default to the logged model. They exist so that the
    backtest can tune it held out, and so that its comparators — FPL's
    difficulty in place of the ratings (`strength="fdr"`), the positional
    prior in place of last season (`last_season=False`) — are this code with
    one piece swapped rather than copies that could drift from it.
    """
    season = previous_season(bootstrap["events"]) if last_season else None
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    positions = {p["id"]: p["singular_name_short"] for p in bootstrap["element_types"]}
    counts = fixture_counts(fixtures, target_gw)
    target_fixtures = {}
    for f in fixtures:
        if f.get("event") == target_gw:
            target_fixtures.setdefault(f["team_h"], []).append(f)
            target_fixtures.setdefault(f["team_a"], []).append(f)

    matches = team_matches(fixtures, histories, target_gw, usable)
    if strength == "fdr":
        ratings = DifficultyRatings(matches, fixtures, prior_matches=prior_matches)
    else:
        ratings = Ratings(matches, source=strength, prior_matches=prior_matches)
    league = positional_rates(bootstrap["elements"], positions, histories, target_gw, usable)

    rows = []
    for player in bootstrap["elements"]:
        pid = player["id"]
        if pid not in histories:
            continue
        history = histories[pid]
        position = positions.get(player["element_type"], "UNK")
        n_fix = counts.get(player["team"], 0)

        predicted = exp_min = pp90 = 0.0
        recent = recent_history(history, target_gw, usable)
        if n_fix and recent:
            exp_min = expected_minutes(recent, player)
            window = recent_rows(history, target_gw, usable)

            # One prior for every part, at one weight: last season's parts, or
            # the position's. Shrinking the total toward one prior and its
            # parts toward another would leave `rest` as whatever is left
            # over — negative, even — and the fixture would scale the wrong
            # thing.
            parts = (player_prior_parts(pasts.get(pid, []), season, position, xg_weight, floor)
                     if last_season else None)
            if parts is not None:
                _, prior_minutes = prior(position, parts["total"],
                                         season_minutes(history, target_gw, usable),
                                         player_prior_minutes)
                priors = (parts["total"], parts["attack"], parts["clean_sheet"])
            else:
                prior_minutes = POSITION_PRIOR_MINUTES
                priors = (POSITION_PRIOR_PP90.get(position, 3.5),
                          *league.get(position, (0.0, 0.0)))

            pp90, attack_pp90, _, rest_pp90 = split_rates(
                (sum(r["points"] for r in recent),
                 sum(attack_points(r, position) for r in window),
                 sum(clean_sheet_points(r, position) for r in window)),
                sum(r["minutes"] for r in recent), priors, prior_minutes)
            sixty = chance_of_sixty(window, player)
            usual = ratings.usual_goals(player["team"])

            for fixture in target_fixtures.get(player["team"], []):
                goals_for, goals_against = ratings.fixture_goals(fixture, player["team"])
                scale = goals_for / usual if usual else 1.0
                predicted += (rest_pp90 + attack_pp90 * scale) * exp_min / 90.0
                predicted += (CLEAN_SHEET_POINTS.get(position, 0) * sixty
                              * clean_sheet_probability(goals_against))

        rows.append({
            "gameweek": target_gw,
            "player_id": pid,
            "web_name": player["web_name"],
            "team": teams.get(player["team"], "UNK"),
            "position": position,
            "price": player["now_cost"] / 10.0,
            "predicted_points": round(predicted, 2),
            "expected_minutes": round(exp_min, 1),
            "points_per_90": round(pp90, 2),
            "n_fixtures": n_fix,
            "status": player.get("status", ""),
        })

    # SPEC section 5: descending predicted_points, ties by ascending player_id.
    rows.sort(key=lambda r: (-r["predicted_points"], r["player_id"]))
    return rows


def main(requested_gw, out_dir):
    snap, bootstrap, fixtures, players_dir = load_snapshot()
    target_gw, deadline = resolve_target_gw(bootstrap["events"], requested_gw)

    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    positions = {p["id"]: p["singular_name_short"] for p in bootstrap["element_types"]}

    # As in baseline-v1: an element_type the snapshot does not name stops the
    # run rather than entering the log as UNK.
    unknown = sorted({p["element_type"] for p in bootstrap["elements"]
                      if p["element_type"] not in positions})
    if unknown:
        raise SystemExit(
            f"element_type(s) {unknown} appear on players but have no entry in "
            f"this snapshot's element_types ({sorted(positions)}).\n"
            "Refusing to write 'UNK' as a position. Nothing was written."
        )

    counts = fixture_counts(fixtures, target_gw)
    usable, excluded = usable_rounds(bootstrap["events"], target_gw)

    print(f"snapshot:  {snap.name}")
    print(f"model:     {MODEL_VERSION}")
    print(f"target:    GW{target_gw}")
    print(f"deadline:  {deadline}")
    inc_txt = ", ".join(str(r) for r in sorted(usable)) or "none"
    exc_txt = ", ".join(str(r) for r in excluded) or "none"
    print(f"included: {inc_txt}")
    print(f"excluded: {exc_txt}" + ("  (results not final)" if excluded else ""))

    if not usable:
        why = (f"data_checked is false for: {exc_txt}" if excluded
               else "no earlier gameweeks exist in this snapshot")
        raise SystemExit(
            f"\nNo usable history before GW{target_gw} — {why}.\n"
            "Refusing to predict from provisional data. Nothing was written.\n"
            "Take a fresh snapshot once the previous gameweek is settled."
        )

    blanks = [teams[t] for t in teams if counts.get(t, 0) == 0]
    doubles = [teams[t] for t in teams if counts.get(t, 0) > 1]
    if blanks:
        print(f"blanking:  {', '.join(sorted(blanks))}")
    if doubles:
        print(f"doubles:   {', '.join(sorted(doubles))}")

    histories, pasts = {}, {}
    for p in bootstrap["elements"]:
        path = players_dir / f"{p['id']}.json"
        if path.exists():
            summary = json.loads(path.read_text())
            histories[p["id"]] = summary.get("history", [])
            pasts[p["id"]] = summary.get("history_past", [])

    # Always reported, like included/excluded: how much of the entry rests on
    # last season rather than on a positional guess (#30). Counted over the
    # players the prior is actually applied to — a fixture this gameweek and
    # settled recent rounds — so a blank does not inflate it.
    season = previous_season(bootstrap["events"])
    predicted = [p for p in bootstrap["elements"] if p["id"] in histories
                 and counts.get(p["team"], 0)
                 and recent_history(histories[p["id"]], target_gw, usable)]
    with_prior = sum(
        1 for p in predicted
        if player_prior_parts(pasts[p["id"]], season,
                              positions.get(p["element_type"])) is not None)
    print(f"prior:     {with_prior} from {season} / "
          f"{len(predicted) - with_prior} positional")
    matches = team_matches(fixtures, histories, target_gw, usable)
    print(f"ratings:   {len(matches)} club-matches from settled rounds")

    rows = predict_rows(bootstrap, fixtures, histories, pasts, target_gw, usable)
    outpath = write_entry(rows, out_dir, target_gw, MODEL_VERSION, snap.name, deadline)

    print(f"\nwrote {len(rows)} predictions -> {outpath}\n")
    print(f"{'player':<18}{'team':<6}{'pos':<5}{'pred':>6}{'mins':>7}{'pp90':>7}")
    print("-" * 49)
    for r in rows[:20]:
        print(f"{r['web_name'][:17]:<18}{r['team']:<6}{r['position']:<5}"
              f"{r['predicted_points']:>6}{r['expected_minutes']:>7}{r['points_per_90']:>7}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gw", type=int, default=None, help="target gameweek (default: next)")
    ap.add_argument(
        "--out",
        default="predictions",
        metavar="DIR",
        help="output directory (default: predictions/). Use a scratch directory "
             "for exploratory runs — predictions/ is the append-only log.",
    )
    args = ap.parse_args()
    main(args.gw, args.out)
