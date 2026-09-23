"""Team strength, and what it says about one fixture.

Goals, assists and clean sheets were ~42% of GW4's points, and they are team
events first: knowing a side will score three matters more than knowing which
of its players will. This module rates each club's attack and defence from
settled rounds and turns a fixture into expected goals for each side.

Shared rather than inside a model file, like fpl.features: two models reading
fixtures differently would make their entries incomparable.

## The model

    expected goals for A against B = league rate x attack(A) x defence(B)
                                     (x home advantage, if HOME_ADVANTAGE)

attack and defence are multipliers around 1.0, fitted jointly so that a side
that has played three strong defences is not rated a weak attack for it. Each
is shrunk toward 1.0 by TEAM_PRIOR_MATCHES pseudo-matches of league-average
football, for the same small-sample reason as the player prior in #30.

Rated on expected goals by default rather than goals: xG settles faster, and
five rounds of goals are mostly finishing luck.

## Where the numbers come from

Team xG for a fixture is the sum of its players' `expected_goals` from their
history rows, and goals come from the fixture's score. Both read only rounds
strictly before the target and in `usable` — the same rule 2 boundary as
fpl.features.recent_history — so in the backtest's cut-back view they are
leak-free by construction.
"""

import math

# Pseudo-matches of league-average football blended into every rating. Tuned
# on the backtest over GW2-5 (#31): 1-4 were within 0.001 MAE of each other,
# and 2 gave the smallest per-club spread on xG.
TEAM_PRIOR_MATCHES = 2.0

# Multiply the home side's expected goals by this and divide the away side's.
# Home sides out-created away sides 1.65 to 1.41 xG over GW1-5, but a home term
# made the backtest worse at every strength and shrinkage tried, 1.08 and 1.17
# both — so 1.0, no home term, until the season says otherwise.
HOME_ADVANTAGE = 1.0

# Rounds of the alternating attack/defence fit. It settles well inside this.
ITERATIONS = 50

# Points for a clean sheet, by position, for 60+ minutes.
CLEAN_SHEET_POINTS = {"GKP": 4, "DEF": 4, "MID": 1, "FWD": 0}


def _club(row, fixture):
    return fixture["team_h"] if row["was_home"] else fixture["team_a"]


def team_matches(fixtures, histories, target_gw, usable):
    """One record per club per settled fixture before the target.

    {fixture, team, opponent, home, xg_for, xg_against, goals_for,
    goals_against}. A fixture counts only if it is finished, has a score, and
    falls in a usable round strictly before the target.
    """
    played = {
        f["id"]: f for f in fixtures
        if f.get("event") is not None and f["event"] < target_gw
        and f["event"] in usable and f.get("finished")
        and f.get("team_h_score") is not None and f.get("team_a_score") is not None
    }

    xg = {}
    for rows in histories.values():
        for row in rows:
            fixture = played.get(row.get("fixture"))
            if fixture is None:
                continue
            key = (fixture["id"], _club(row, fixture))
            xg[key] = xg.get(key, 0.0) + float(row.get("expected_goals") or 0)

    matches = []
    for f in played.values():
        for team, opp, home, gf, ga in (
            (f["team_h"], f["team_a"], True, f["team_h_score"], f["team_a_score"]),
            (f["team_a"], f["team_h"], False, f["team_a_score"], f["team_h_score"]),
        ):
            matches.append({
                "fixture": f["id"], "team": team, "opponent": opp, "home": home,
                "xg_for": xg.get((f["id"], team), 0.0),
                "xg_against": xg.get((f["id"], opp), 0.0),
                "goals_for": gf, "goals_against": ga,
            })
    return matches


class Ratings:
    """Attack and defence per club, and expected goals for any pairing."""

    def __init__(self, matches, source="xg", prior_matches=None, home_advantage=None):
        self.prior_matches = TEAM_PRIOR_MATCHES if prior_matches is None else prior_matches
        self.home = HOME_ADVANTAGE if home_advantage is None else home_advantage
        scored = f"{source}_for"
        self.rate = _league_rate(matches, scored)
        clubs = {m["team"] for m in matches}
        self.attack = {c: 1.0 for c in clubs}
        self.defence = {c: 1.0 for c in clubs}
        k = self.prior_matches
        for _ in range(ITERATIONS):
            # Each rating is goals observed over goals expected from the rest
            # of the model, with k matches of exactly-average football on both
            # sides of the fraction.
            self.attack = {c: (sum(m[scored] for m in matches if m["team"] == c) + k * self.rate)
                           / (sum(self._base(m) * self.defence[m["opponent"]]
                                  for m in matches if m["team"] == c) + k * self.rate)
                           for c in clubs}
            self.defence = {c: (sum(m[scored] for m in matches if m["opponent"] == c)
                                + k * self.rate)
                            / (sum(self._base(m) * self.attack[m["team"]]
                                   for m in matches if m["opponent"] == c) + k * self.rate)
                            for c in clubs}

    def _base(self, match):
        return self.rate * (self.home if match["home"] else 1.0 / self.home)

    def expected_goals(self, team, opponent, home):
        """Expected goals for `team` against `opponent`. Unrated clubs are average."""
        venue = self.home if home else 1.0 / self.home
        return (self.rate * venue * self.attack.get(team, 1.0)
                * self.defence.get(opponent, 1.0))

    def usual_goals(self, team):
        """Expected goals for `team` against an average side at a neutral venue."""
        return self.rate * self.attack.get(team, 1.0)

    def fixture_goals(self, fixture, team):
        """(goals for, goals against) expected for `team` in `fixture`."""
        home = fixture["team_h"] == team
        opponent = fixture["team_a"] if home else fixture["team_h"]
        return (self.expected_goals(team, opponent, home),
                self.expected_goals(opponent, team, not home))


class DifficultyRatings:
    """FPL's own fixture difficulty, as a comparator for Ratings (#31).

    A side facing difficulty d is expected to score the league rate times the
    rate sides facing d have scored at, shrunk toward 1.0. Difficulty is read
    off the fixture, so this needs the fixture rather than just the pairing.
    FPL's ratings in a snapshot are current rather than as-published, which
    flatters this comparator in the backtest a little.
    """

    def __init__(self, matches, fixtures, source="xg", prior_matches=None):
        k = TEAM_PRIOR_MATCHES if prior_matches is None else prior_matches
        by_id = {f["id"]: f for f in fixtures}
        scored = f"{source}_for"
        self.rate = _league_rate(matches, scored)
        faced = {}
        for m in matches:
            d = _difficulty_faced(by_id[m["fixture"]], m["home"])
            faced.setdefault(d, []).append(m[scored])
        self.factor = {
            d: (sum(xs) + k * self.rate) / ((len(xs) + k) * self.rate)
            for d, xs in faced.items()
        }

    def goals_in(self, fixture, home):
        """Expected goals for the home (or away) side of `fixture`."""
        return self.rate * self.factor.get(_difficulty_faced(fixture, home), 1.0)

    def usual_goals(self, team):
        """Difficulty rates fixtures, not clubs: every side's usual is the league's."""
        return self.rate

    def fixture_goals(self, fixture, team):
        """(goals for, goals against) expected for `team` in `fixture`."""
        home = fixture["team_h"] == team
        return self.goals_in(fixture, home), self.goals_in(fixture, not home)


def _league_rate(matches, scored):
    """Mean goals (or xG) per side per match. Refuses a league with none.

    A zero rate would make every clean sheet certain and every attack worth
    nothing — a prediction, not an absence of one. Stop instead.
    """
    rate = sum(m[scored] for m in matches) / len(matches) if matches else 0.0
    if rate <= 0:
        raise SystemExit(
            f"No {scored.removesuffix('_for')} in {len(matches)} settled club-matches "
            "to rate clubs from. Has the element-summary schema changed?")
    return rate


def _difficulty_faced(fixture, home):
    return fixture.get("team_h_difficulty" if home else "team_a_difficulty")


def clean_sheet_probability(goals_against):
    """P(no goals conceded), treating goals against as Poisson."""
    return math.exp(-goals_against)
