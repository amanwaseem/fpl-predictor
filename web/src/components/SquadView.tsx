import { Link } from "react-router-dom";
import type { Schemas } from "../api/client";
import { POSITIONS } from "../api/queries";
import { fixed } from "../format";
import { PlayerPhoto } from "./bits";

type Squad = Schemas["Squad"];
type Pick = Schemas["Pick"];

// A squad as the optimiser chose it: the XI on a pitch, goalkeeper at the
// back, with the captain and vice-captain marked, then the bench in order.
// `codes` maps player ids to FPL codes for photos (#136); a missing one shows
// initials.
export function SquadView({
  squad,
  label,
  codes,
}: {
  squad: Squad;
  label: string;
  codes: Map<number, number | null>;
}) {
  const shirt = (p: Pick) => {
    const captain = p.player_id === squad.captain.player_id;
    const vice = p.player_id === squad.vice_captain.player_id;
    return (
      <li className="shirt" key={p.player_id} data-captain={captain || undefined} data-vice={vice || undefined}>
        {(captain || vice) && (
          <span
            className={`armband${vice ? " armband-vice" : ""}`}
            title={captain ? "Captain" : "Vice-captain"}
            aria-label={captain ? "Captain" : "Vice-captain"}
          >
            {captain ? "C" : "V"}
          </span>
        )}
        <PlayerPhoto code={codes.get(p.player_id)} name={p.web_name} team={p.team} size={56} />
        <Link className="shirt-name" to={`/players/${p.player_id}`} title={`${p.web_name}, ${p.team}`}>
          {p.web_name}
        </Link>
        <span className="shirt-points">
          {fixed(p.predicted_points * (captain ? 2 : 1), 1)} pts
        </span>
      </li>
    );
  };

  return (
    <>
      <dl className="squad-summary">
        <div className="stat">
          <dt>Expected points</dt>
          <dd>{fixed(squad.expected_points, 1)}</dd>
        </div>
        <div className="stat">
          <dt>Formation</dt>
          <dd>{squad.formation}</dd>
        </div>
        <div className="stat">
          <dt>Squad cost</dt>
          <dd>£{squad.cost.toFixed(1)}m</dd>
        </div>
        <div className="stat">
          <dt>Bench</dt>
          <dd>{fixed(squad.bench_points, 1)} pts</dd>
        </div>
      </dl>
      <div role="group" aria-label={label}>
        <div className="pitch">
          {POSITIONS.map((position) => (
            <ul className="pitch-row" key={position} aria-label={position}>
              {squad.xi.filter((p) => p.position === position).map(shirt)}
            </ul>
          ))}
        </div>
        <div className="bench">
          <p className="eyebrow">Bench, in order</p>
          <ul className="pitch-row" aria-label="Bench">
            {squad.bench.map(shirt)}
          </ul>
        </div>
      </div>
      <p className="note small after">
        Points are each player's prediction for the gameweek, the captain's doubled. Expected points
        are the XI's total. Budget £{squad.budget.toFixed(1)}m.
      </p>
    </>
  );
}
