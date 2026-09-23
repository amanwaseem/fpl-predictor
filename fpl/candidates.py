"""Models that exist to be backtested, never logged.

A candidate isolates one idea so the backtest can say whether it earns its
place before a logged model depends on it. Nothing here writes to
predictions/, and nothing here has a log entry to be consistent with: when an
idea is judged, it moves into a logged model (built from fpl.features, not
from this file) and the candidate is deleted.

baseline-prior (#30): baseline-v1 with last season's points-per-90 as the
prior in place of the positional one. Minutes, window, decay and fixture count
are baseline-v1's, so a difference on the backtest is the prior's doing.

baseline-fixture (#31): baseline-v1 told who each side is playing. Its shrunk
rate is split into attacking returns, clean-sheet points and the rest.
Attacking returns scale with the side's expected goals in the fixture relative
to its usual; clean-sheet points become P(clean sheet) x P(60+ minutes) x the
position's points; the rest is untouched. Scaling the goals-conceded deduction
by the fixture as well was tried and lost on held-out gameweeks in 3 of 4
folds, so it stays in the rest at the player's own rate. Against an average side the split
sums back to roughly baseline-v1's number.
"""

from fpl.features import (
    ASSIST_POINTS,
    DECAY,
    GOAL_POINTS,
    PLAYER_PRIOR_MINUTES,
    POSITION_PRIOR_MINUTES,
    POSITION_PRIOR_PP90,
    availability,
    expected_minutes,
    player_prior,
    player_prior_parts,
    previous_season,
    prior,
    recent_history,
    recent_rows,
    season_minutes,
    shrunk_pp90,
)
from fpl.snapshot import fixture_counts, usable_rounds
from fpl.teams import (
    CLEAN_SHEET_POINTS,
    DifficultyRatings,
    Ratings,
    clean_sheet_probability,
    team_matches,
)


def baseline_prior_rows(bootstrap, fixtures, histories, target_gw, pasts,
                        player_prior_minutes=PLAYER_PRIOR_MINUTES, floor=None,
                        xg_weight=None):
    """Log-shaped rows, as baseline-v1's predict_rows, with a player prior.

    `pasts` maps player id to history_past rows. The prior's weight, floor and
    xG weight are exposed so the backtest can tune them held out
    (fpl.backtest.GRIDS); a logged model fixes them. None means features'
    constant.
    """
    usable, _ = usable_rounds(bootstrap["events"], target_gw)
    season = previous_season(bootstrap["events"])
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    positions = {p["id"]: p["singular_name_short"] for p in bootstrap["element_types"]}
    counts = fixture_counts(fixtures, target_gw)

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
            prior_pp90, prior_minutes = prior(
                position,
                player_prior(pasts.get(pid, []), season, position, xg_weight, floor),
                season_minutes(history, target_gw, usable),
                player_prior_minutes,
            )
            pp90 = shrunk_pp90(sum(r["points"] for r in recent),
                               sum(r["minutes"] for r in recent),
                               prior_pp90, prior_minutes)
            predicted = pp90 * (exp_min / 90.0) * n_fix

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

    rows.sort(key=lambda r: (-r["predicted_points"], r["player_id"]))
    return rows


def _attack_points(row, position):
    return ((row.get("goals_scored") or 0) * GOAL_POINTS.get(position, 0)
            + (row.get("assists") or 0) * ASSIST_POINTS)


def _clean_sheet_points(row, position):
    return (row.get("clean_sheets") or 0) * CLEAN_SHEET_POINTS.get(position, 0)


def positional_rates(elements, positions, histories, target_gw, usable):
    """League points-per-90 from attacking returns and clean sheets, by position.

    The positional prior split the same way the candidate splits a player,
    from the same settled rounds, so a player with few minutes is shrunk toward
    what his position actually earns from each part.
    """
    totals = {}
    for player in elements:
        position = positions.get(player["element_type"], "UNK")
        t = totals.setdefault(position, [0.0, 0.0, 0])
        for row in histories.get(player["id"], []):
            if row.get("round") is None or row["round"] >= target_gw or row["round"] not in usable:
                continue
            t[0] += _attack_points(row, position)
            t[1] += _clean_sheet_points(row, position)
            t[2] += row.get("minutes") or 0
    return {pos: (a * 90.0 / m, c * 90.0 / m) if m else (0.0, 0.0)
            for pos, (a, c, m) in totals.items()}


def split_rates(observed, minutes, priors, prior_minutes):
    """(total, attack, clean sheet, rest) points per 90, each shrunk alike.

    `observed` and `priors` are (total, attack, clean sheet) as points and as
    points per 90. Every part is shrunk toward its own prior at the same
    weight, so the parts add back to the total and the rest is itself a
    shrunk rate — last season's or the position's rest, blended with this
    season's — rather than whatever the other parts leave over.
    """
    total, attack, clean_sheet = (shrunk_pp90(o, minutes, p, prior_minutes)
                                  for o, p in zip(observed, priors))
    return total, attack, clean_sheet, total - attack - clean_sheet


def _chance_of_sixty(rows, player):
    """Decayed share of recent fixtures he played 60+ minutes in, times availability."""
    by_round = {}
    for row in rows:
        played, total = by_round.get(row["round"], (0, 0))
        by_round[row["round"]] = (played + ((row.get("minutes") or 0) >= 60), total + 1)
    total = weight_sum = 0.0
    for i, rnd in enumerate(sorted(by_round, reverse=True)):
        played, fixtures = by_round[rnd]
        total += DECAY ** i * played / fixtures
        weight_sum += DECAY ** i
    return (total / weight_sum if weight_sum else 0.0) * availability(player)


def baseline_fixture_rows(bootstrap, fixtures, histories, target_gw, pasts,
                          strength="xg", last_season=False,
                          player_prior_minutes=PLAYER_PRIOR_MINUTES, floor=None,
                          xg_weight=None, **rating_options):
    """Log-shaped rows, as baseline-v1's, with each fixture's opponent read in.

    `strength` is "xg" or "goals" for fpl.teams.Ratings on that source, or
    "fdr" for FPL's own difficulty. `last_season` shrinks the total rate
    toward #30's player prior instead of the positional one, which is how the
    two ideas are tested together. The rest pass through so the backtest can sweep
    them; a logged model fixes them.
    """
    usable, _ = usable_rounds(bootstrap["events"], target_gw)
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
        ratings = DifficultyRatings(matches, fixtures, **rating_options)
    else:
        ratings = Ratings(matches, source=strength, **rating_options)
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
            obs_minutes = sum(r["minutes"] for r in recent)
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
                prior_pp90 = parts["total"]
                prior_attack, prior_cs = parts["attack"], parts["clean_sheet"]
            else:
                prior_pp90 = POSITION_PRIOR_PP90.get(position, 3.5)
                prior_minutes = POSITION_PRIOR_MINUTES
                prior_attack, prior_cs = league.get(position, (0.0, 0.0))
            pp90, attack_pp90, cs_pp90, rest_pp90 = split_rates(
                (sum(r["points"] for r in recent),
                 sum(_attack_points(r, position) for r in window),
                 sum(_clean_sheet_points(r, position) for r in window)),
                obs_minutes, (prior_pp90, prior_attack, prior_cs), prior_minutes)
            sixty = _chance_of_sixty(window, player)
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

    rows.sort(key=lambda r: (-r["predicted_points"], r["player_id"]))
    return rows
