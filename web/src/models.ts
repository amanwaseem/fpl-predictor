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
