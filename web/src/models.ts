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

// Users see one set of predictions (#194): the main model's, or for a
// gameweek it did not log, the baseline's, then any other's. The models are
// compared on Track record; a link may still ask for one by ?model=.
const ROLE_ORDER: Partial<Record<NonNullable<Role>, number>> = { main: 0, baseline: 1 };

function rank(role: Role | null | undefined): number {
  return role ? (ROLE_ORDER[role] ?? 2) : 3;
}

export function byPreference(models: Schemas["Model"][]): Schemas["Model"][] {
  return [...models].sort((a, b) => rank(a.role) - rank(b.role));
}

// A player's one prediction for a gameweek, from the most preferred model
// that logged it that week.
export function pickPrediction<T extends { model_version: string }>(
  predictions: T[],
  models: Schemas["Model"][] | undefined,
): T | undefined {
  const roleOf = (version: string) => models?.find((m) => m.model_version === version)?.role;
  return [...predictions].sort(
    (a, b) => rank(roleOf(a.model_version)) - rank(roleOf(b.model_version)),
  )[0];
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
