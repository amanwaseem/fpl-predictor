import type { Schemas } from "../api/client";
import { fixed } from "../format";
import { Link } from "react-router-dom";

type Squad = Schemas["Squad"];
type Pick = Schemas["Pick"];

const POSITIONS = ["GKP", "DEF", "MID", "FWD"];

// A squad as the optimiser chose it: the XI by position with the captain and
// vice-captain marked, then the bench in order.
export function SquadView({ squad, label }: { squad: Squad; label: string }) {
  const mark = (p: Pick) =>
    p.player_id === squad.captain.player_id
      ? " (C)"
      : p.player_id === squad.vice_captain.player_id
        ? " (V)"
        : "";
  const row = (p: Pick) => (
    <tr key={p.player_id}>
      <th scope="row">
        <Link to={`/players/${p.player_id}`}>{p.web_name}</Link>
        {mark(p)}
      </th>
      <td>{p.team}</td>
      <td>{p.position}</td>
      <td className="num">£{(p.selling_price ?? p.price).toFixed(1)}m</td>
      <td className="num">{fixed(p.predicted_points, 2)}</td>
    </tr>
  );
  const xi = [...squad.xi].sort(
    (a, b) => POSITIONS.indexOf(a.position) - POSITIONS.indexOf(b.position),
  );

  return (
    <>
      <dl className="facts">
        <div>
          <dt>Expected points</dt>
          <dd>{fixed(squad.expected_points, 2)}</dd>
        </div>
        <div>
          <dt>Formation</dt>
          <dd>{squad.formation}</dd>
        </div>
        <div>
          <dt>Cost</dt>
          <dd>
            £{squad.cost.toFixed(1)}m of £{squad.budget.toFixed(1)}m
          </dd>
        </div>
        <div>
          <dt>Bench</dt>
          <dd>{fixed(squad.bench_points, 2)} points</dd>
        </div>
      </dl>
      <div className="table-scroll">
        <table aria-label={label}>
          <thead>
            <tr>
              <th scope="col">Player</th>
              <th scope="col">Club</th>
              <th scope="col">Pos</th>
              <th scope="col" className="num">Price</th>
              <th scope="col" className="num">Predicted</th>
            </tr>
          </thead>
          <tbody>
            {xi.map(row)}
            <tr>
              <th scope="rowgroup" colSpan={5} className="subhead">
                Bench, in order
              </th>
            </tr>
            {squad.bench.map(row)}
          </tbody>
        </table>
      </div>
      <p className="note">
        Expected points are the XI's predicted points with the captain's counted twice. (C) is the
        captain and (V) the vice-captain.
      </p>
    </>
  );
}
