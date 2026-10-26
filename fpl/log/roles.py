"""What each logged model is for: the baseline, the main model, a challenger (#109, ADR 0020).

- **The baseline** is the yardstick every claim is measured against. It is
  logged every gameweek, never changed and never retired (ADR 0007).
- **The main model** is the best so far. It improves by new versions, each
  with a record of its own (ADR 0005), not by edits under the same name.
- **A challenger**, at most one, is being proven against the main model. If its
  entries beat the main model's over enough gameweeks, it becomes the main
  model and the old one retires; if not, it retires itself.

A retired model is no longer logged. Its entries stay in the log, and its
record ends at its last gameweek: the weeks after are not missed, they are
after it. Retiring a model is moving it here from ROLES to RETIRED, in the PR
that takes it out of fpl.log.entry.LOGGED_MODELS, with the season, its last
gameweek and the reason.

Standard library only: the entry, the scoring harness and the API read it.
"""

BASELINE, MAIN, CHALLENGER, RETIRED_ROLE = "baseline", "main", "challenger", "retired"

# Every model logged now, and its role.
ROLES = {
    "baseline-v1": BASELINE,
    "form-fixture-v1": MAIN,
    "gbm-v1": CHALLENGER,
}

# Retired models: {model: {"season": "2026/27", "last_gameweek": 12, "reason": "..."}}.
RETIRED: dict[str, dict] = {}


def last_gameweek(model, season):
    """The last gameweek `model` was logged for in `season`, or None if it was not
    retired in that season."""
    retired = RETIRED.get(model)
    if isinstance(retired, dict) and retired.get("season") == season:
        return retired.get("last_gameweek")
    return None


def role(model, season):
    """`model`'s role in `season`: retired, if it retired then; else its role now,
    or None for a model with no role (only a test log has one)."""
    return RETIRED_ROLE if last_gameweek(model, season) is not None else ROLES.get(model)


def problems(roles=None, retired=None, logged=None):
    """Every way the roles break the rules above, as readable lines.

    `logged` is the model versions fpl-entry runs.
    """
    roles = ROLES if roles is None else roles
    retired = RETIRED if retired is None else retired
    found = []

    def holders(wanted):
        return sorted(m for m, r in roles.items() if r == wanted)

    if len(holders(BASELINE)) != 1:
        found.append(f"baseline model(s) {holders(BASELINE)}: there must be exactly one")
    if len(holders(MAIN)) != 1:
        found.append(f"main model(s) {holders(MAIN)}: there must be exactly one")
    if len(holders(CHALLENGER)) > 1:
        found.append(f"challenger(s) {holders(CHALLENGER)}: there may be at most one")
    unknown = sorted(set(roles.values()) - {BASELINE, MAIN, CHALLENGER})
    if unknown:
        found.append(f"unknown role(s): {unknown}")
    both = sorted(set(roles) & set(retired))
    if both:
        found.append(f"both logged and retired: {both}")
    for model, why in retired.items():
        gameweek = why.get("last_gameweek") if isinstance(why, dict) else None
        # bool is an int in Python; True is not a gameweek.
        if (
            not isinstance(why, dict)
            or not isinstance(gameweek, int)
            or isinstance(gameweek, bool)
            or not why.get("reason")
            or not why.get("season")
        ):
            found.append(f"{model}: a retirement is a season, a last_gameweek and a reason")
    if logged is not None:
        if sorted(logged) != sorted(roles):
            found.append(
                f"fpl-entry runs {sorted(logged)}, but the roles are for "
                f"{sorted(roles)}: every logged model has exactly one role"
            )
        running_retired = sorted(set(logged) & set(retired))
        if running_retired:
            found.append(f"fpl-entry runs retired model(s): {running_retired}")
    return found


def entries_after_retirement(entries, season):
    """Entries the log holds for a model after its retirement, as readable lines.

    `entries` is [(gameweek, model)] for `season`. A retirement contradicted
    by the log is wrong, not a model with entries after its end.
    """
    return [
        f"{model} retired after GW{last_gameweek(model, season)} but has a GW{gameweek} entry"
        for gameweek, model in sorted(entries)
        if (last := last_gameweek(model, season)) is not None and gameweek > last
    ]
