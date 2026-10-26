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
that takes it out of fpl.log.entry.LOGGED_MODELS, with the reason.

Standard library only: the entry, the scoring harness and the API read it.
"""

BASELINE, MAIN, CHALLENGER = "baseline", "main", "challenger"

# Every model logged now, and its role.
ROLES = {
    "baseline-v1": BASELINE,
    "form-fixture-v1": MAIN,
    "gbm-v1": CHALLENGER,
}

# Retired models: the last gameweek each was logged for, and why it stopped.
# Keyed by model alone while the log holds one season (#72 adds the season).
RETIRED: dict[str, dict] = {}


def last_gameweek(model):
    """The last gameweek `model` is logged for, or None while it is still logged."""
    retired = RETIRED.get(model)
    return retired["last_gameweek"] if retired else None


def problems(roles=None, retired=None, logged=None):
    """Every way the roles break the rules above, as readable lines.

    `logged` is the model versions fpl-entry runs.
    """
    roles = ROLES if roles is None else roles
    retired = RETIRED if retired is None else retired
    found = []
    for role, most in ((BASELINE, 1), (MAIN, 1), (CHALLENGER, 1)):
        holders = sorted(m for m, r in roles.items() if r == role)
        if len(holders) > most or (role == BASELINE and not holders):
            found.append(
                f"{len(holders)} {role} model(s), {holders}: there must be "
                f"{'exactly' if role == BASELINE else 'at most'} {most}"
            )
    unknown = sorted(set(roles.values()) - {BASELINE, MAIN, CHALLENGER})
    if unknown:
        found.append(f"unknown role(s): {unknown}")
    both = sorted(set(roles) & set(retired))
    if both:
        found.append(f"both logged and retired: {both}")
    # Retiring the baseline leaves none, which the count above reports.
    for model, why in retired.items():
        if not isinstance(why.get("last_gameweek"), int) or not why.get("reason"):
            found.append(f"{model}: a retirement needs its last_gameweek and a reason")
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
