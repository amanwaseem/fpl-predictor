// How models are named on the site. The log and the API name a model by its
// version (`form-fixture-v1`); managers get a name and one line on what it
// does, with the version still shown wherever the numbers are. A model not
// listed here is shown by its version alone.
import type { Schemas } from "./api/client";

type Role = Schemas["Model"]["role"];

const MODELS: Record<string, { name: string; about: string }> = {
  "baseline-v1": {
    name: "Baseline",
    about:
      "Recent minutes and scoring rate, adjusted for availability. The yardstick the others must beat.",
  },
  "form-fixture-v1": {
    name: "Form & fixtures",
    about: "The baseline plus last season's record and how strong each opponent is.",
  },
  "gbm-v1": {
    name: "Machine learning",
    about:
      "A gradient-boosted model trained on ten past seasons, minutes and points predicted separately.",
  },
};

// The models logged now: those with a role that is not retired.
export function liveModels(models: Schemas["Model"][] | undefined): Schemas["Model"][] {
  return (models ?? []).filter((m) => m.role && m.role !== "retired");
}

// The model that was main in `gameweek`, among those that logged it. A
// promotion retires the old main model (ADR 0020), but its predictions are
// still what users saw before those deadlines, so a retired model that ended
// as main comes first for the gameweeks it logged. Main models follow one
// another, so of two that logged a gameweek, the one retired first was main.
export function mainIn(
  models: Schemas["Model"][] | undefined,
  gameweek: number,
): Schemas["Model"] | undefined {
  const logged = (models ?? []).filter((m) => m.gameweeks_logged.includes(gameweek));
  const former = logged
    .filter((m) => m.retired_from === "main")
    .sort((a, b) => (a.retired_after ?? 0) - (b.retired_after ?? 0))[0];
  return former ?? logged.find((m) => m.role === "main");
}

// Users see one set of predictions (#194): the main model's for that
// gameweek, or for one before a main model ran, the baseline's, then any
// other's. The models are compared on Track record; a link may still ask for
// one by ?model=. Only models that logged the gameweek are listed.
export function byPreference(
  models: Schemas["Model"][] | undefined,
  gameweek: number,
): Schemas["Model"][] {
  const main = mainIn(models, gameweek)?.model_version;
  const rank = (m: Schemas["Model"]) =>
    m.model_version === main ? 0 : m.role === "baseline" ? 1 : m.role ? 2 : 3;
  return (models ?? [])
    .filter((m) => m.gameweeks_logged.includes(gameweek))
    .sort((a, b) => rank(a) - rank(b));
}

// A player's one prediction for a gameweek, from the most preferred model
// that logged it that week.
export function pickPrediction<T extends { model_version: string }>(
  predictions: T[],
  models: Schemas["Model"][] | undefined,
  gameweek: number,
): T | undefined {
  const order = byPreference(models, gameweek).map((m) => m.model_version);
  const rank = (version: string) => {
    const i = order.indexOf(version);
    return i === -1 ? order.length : i;
  };
  return [...predictions].sort((a, b) => rank(a.model_version) - rank(b.model_version))[0];
}

export function modelName(version: string): string {
  return MODELS[version]?.name ?? version;
}

export function modelAbout(version: string): string | null {
  return MODELS[version]?.about ?? null;
}

// ADR 0020's roles, as a manager would read them.
export function roleName(role: Role | null | undefined): string | null {
  switch (role) {
    case "main":
      return "Main model";
    case "challenger":
      return "Challenger";
    case "baseline":
      return "Baseline";
    case "retired":
      return "Retired";
    default:
      return null;
  }
}
