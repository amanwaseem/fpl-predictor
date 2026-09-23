"""Models that exist to be backtested, never logged.

A candidate isolates one idea so the backtest can say whether it earns its
place before a logged model depends on it. Nothing here writes to
predictions/, and nothing here has a log entry to be consistent with: when an
idea is judged, it moves into a logged model (built from fpl.features, not
from this file) and the candidate is deleted.

baseline-prior (#30): baseline-v1 with last season's points-per-90 as the
prior in place of the positional one. Minutes, window, decay and fixture count
are baseline-v1's, so a difference on the backtest is the prior's doing.
"""

from fpl.features import (
    PLAYER_PRIOR_MINUTES,
    expected_minutes,
    player_prior,
    previous_season,
    prior,
    recent_history,
    season_minutes,
    shrunk_pp90,
)
from fpl.snapshot import fixture_counts, usable_rounds


def baseline_prior_rows(bootstrap, fixtures, histories, target_gw, pasts,
                        player_prior_minutes=PLAYER_PRIOR_MINUTES):
    """Log-shaped rows, as baseline-v1's predict_rows, with a player prior.

    `pasts` maps player id to history_past rows. `player_prior_minutes` is
    exposed so the backtest can sweep it; a logged model fixes it.
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
                player_prior(pasts.get(pid, []), season, position),
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
