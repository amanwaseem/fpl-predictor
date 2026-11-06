import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api/client";
import { useModels, usePhotoCodes } from "../api/queries";
import { ClubBadge, Loading, PlayerPhoto } from "../components/bits";
import { useTitle } from "../useTitle";
import { Problem } from "../components/Problem";
import { SquadView } from "../components/SquadView";
import { fixed, formatLocal, positiveInt, signed } from "../format";
import { modelName } from "../models";

type Plan = Schemas["Plan"];

export function Optimise() {
  useTitle("My team");
  const [params, setParams] = useSearchParams();
  const models = useModels();
  const live = (models.data?.models ?? []).filter((m) => m.role && m.role !== "retired");
  const main = live.find((m) => m.role === "main") ?? live[0];

  // What is on screen comes from the URL alone; the form is applied on submit.
  // With no model chosen and none known, the API's default is used.
  const team = positiveInt(params.get("team") ?? undefined);
  const model = params.get("model") ?? main?.model_version;
  const freeParam = params.get("free");
  const free = freeParam !== null && /^[0-5]$/.test(freeParam) ? Number(freeParam) : undefined;

  // The form follows the URL, so back and forward leave the two agreeing, and
  // only the URL: the models list arriving must not clear what was typed. An
  // empty model is the default one.
  const fromUrl = () => ({
    team: params.get("team") ?? "",
    model: params.get("model") ?? "",
    free: params.get("free") ?? "",
  });
  // Reset while rendering, not in an effect: React's pattern for state that
  // follows a prop (react.dev, "You might not need an effect"). `params` is a
  // new object only when the URL has changed.
  const [form, setForm] = useState(fromUrl);
  const [formParams, setFormParams] = useState(params);
  if (params !== formParams) {
    setFormParams(params);
    setForm(fromUrl());
  }
  const chosen = form.model || model || "";
  // A model from a link that is not offered is still shown as chosen.
  const offered = live.map((m) => m.model_version);
  if (chosen && !offered.includes(chosen)) offered.push(chosen);

  const advice = useQuery({
    queryKey: ["advice", team, model, free],
    enabled: team !== null,
    // A failure here may be the FPL API's; asking it again at once rarely helps.
    retry: false,
    queryFn: () =>
      unwrap(
        api.GET("/v1/entries/{fpl_team_id}/advice", {
          params: { path: { fpl_team_id: team! }, query: { model, free_transfers: free } },
        }),
      ),
  });

  const fresh = useMutation({
    mutationFn: () => unwrap(api.POST("/v1/optimise", { body: { model } })),
  });

  // Photos for the picks, from the same entry's predictions.
  const adviceCodes = usePhotoCodes(advice.data?.gameweek, advice.data?.model_version);
  const freshCodes = usePhotoCodes(fresh.data?.gameweek, fresh.data?.model_version);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const next = new URLSearchParams();
    next.set("team", form.team.trim());
    if (chosen) next.set("model", chosen);
    if (form.free !== "") next.set("free", form.free);
    setParams(next);
  };

  return (
    <div className="wrap page">
      <div className="page-head">
        <div>
          <p className="eyebrow">My team</p>
          <h1>Get advice for your team</h1>
          <p className="lede">
            Enter your FPL team ID for the best transfers, starting XI and captain this gameweek,
            worked out from our latest predictions.
          </p>
        </div>
      </div>

      <form className="card" onSubmit={submit}>
        <div className="controls">
          <label className="field">
            FPL team ID
            <input
              inputMode="numeric"
              pattern="[0-9]*"
              placeholder="e.g. 1234567"
              required
              value={form.team}
              onChange={(e) => setForm({ ...form, team: e.target.value })}
            />
          </label>
          <label className="field">
            Model
            <select value={chosen} onChange={(e) => setForm({ ...form, model: e.target.value })}>
              {offered.map((name) => (
                <option key={name} value={name}>
                  {modelName(name)}
                  {live.find((m) => m.model_version === name)?.role === "main"
                    ? " (recommended)"
                    : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Free transfers
            <select value={form.free} onChange={(e) => setForm({ ...form, free: e.target.value })}>
              <option value="">Work it out</option>
              {[0, 1, 2, 3, 4, 5].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" className="button">
            Get advice
          </button>
        </div>
        <details className="help">
          <summary>Where do I find my team ID?</summary>
          <ol>
            <li>Sign in at fantasy.premierleague.com and open the Points tab.</li>
            <li>
              The address ends like <code>/entry/1234567/event/9</code>: the number after{" "}
              <code>entry</code> is your team ID.
            </li>
            <li>Nothing is saved, and no sign-in is needed here: your squad is public on FPL.</li>
          </ol>
        </details>
      </form>

      {models.isError && <Problem error={models.error} />}
      {advice.isFetching && <Loading what="Reading your team and working out the best moves" />}
      {advice.isError && <Problem error={advice.error} />}
      {advice.data && (
        <section className="section" aria-labelledby="advice">
          <div className="section-head">
            <h2 id="advice">Your advice for gameweek {advice.data.gameweek}</h2>
            <span className="muted small">
              From {modelName(advice.data.model_version)} · deadline{" "}
              {formatLocal(advice.data.deadline_utc)}
            </span>
          </div>
          {advice.data.deadline_passed && (
            <p role="alert" className="problem problem-warn">
              The gameweek {advice.data.gameweek} deadline has passed. This is what the model would
              have advised.
            </p>
          )}
          {advice.data.plan && <PlanView plan={advice.data.plan} codes={adviceCodes} />}
          <h3 className="section">Your best XI</h3>
          <SquadView squad={advice.data.squad} label="Advised squad" codes={adviceCodes} />
          <p className="note small after">
            Based on your squad as of gameweek {advice.data.picks_gameweek}. FPL keeps selling
            prices and free transfers private, so unless you gave them they were estimated. Selling
            prices: {advice.data.estimates.selling_prices} Free transfers:{" "}
            {advice.data.estimates.free_transfers}
          </p>
        </section>
      )}

      <section className="section card" aria-labelledby="fresh">
        <div className="section-head">
          <div>
            <h2 id="fresh">Best squad from scratch</h2>
            <p className="muted small" style={{ margin: 0 }}>
              The best fifteen within £100m under every FPL rule. Handy for a wildcard or free hit.
            </p>
          </div>
          <button
            type="button"
            className="button"
            disabled={fresh.isPending}
            onClick={() => fresh.mutate()}
          >
            {fresh.isPending ? "Picking…" : "Pick the best squad"}
          </button>
        </div>
        {fresh.isError && <Problem error={fresh.error} />}
        {fresh.data && (
          <>
            <p className="muted small">
              Gameweek {fresh.data.gameweek}, from {modelName(fresh.data.model_version)}.{" "}
              <Link to={`/gameweeks/${fresh.data.gameweek}?model=${fresh.data.model_version}`}>
                See every prediction
              </Link>
            </p>
            <SquadView squad={fresh.data.squad} label="Best squad" codes={freshCodes} />
          </>
        )}
      </section>
    </div>
  );
}

function PlanView({ plan, codes }: { plan: Plan; codes: Map<number, number | null> }) {
  const overCap = Object.entries(plan.over_cap);
  const n = plan.transfers.length;
  return (
    <>
      <div className="card" style={{ marginBottom: "1rem" }}>
        <p className="eyebrow">The verdict</p>
        <p className="big" style={{ margin: "0.1rem 0 0.4rem" }}>
          {n === 0 ? "Hold" : `Make ${n} transfer${n === 1 ? "" : "s"}`}
        </p>
        <p className="muted" style={{ margin: 0 }}>
          {n === 0 ? (
            "No transfer gains enough this gameweek to be worth making."
          ) : (
            <>
              Expected gain this gameweek:{" "}
              <strong
                className={plan.xi_gain > 0 ? "better" : plan.xi_gain < 0 ? "worse" : undefined}
              >
                {signed(plan.xi_gain, 2)} points
              </strong>
              {plan.hits
                ? `, after ${plan.hits} hit${plan.hits === 1 ? "" : "s"} (−${plan.hit_points})`
                : ""}
              .
            </>
          )}{" "}
          {plan.free_transfers} free transfer{plan.free_transfers === 1 ? "" : "s"} · bank £
          {fixed(plan.bank, 1)}m{n ? `, then £${fixed(plan.bank_after, 1)}m` : ""}.
        </p>
      </div>
      {overCap.length > 0 && (
        <p role="alert" className="problem problem-warn">
          Your squad has {overCap.map(([club, count]) => `${count} from ${club}`).join(", ")}, over
          the three-per-club limit. FPL lets you keep it, but any transfer must fix it, so this plan
          does.
        </p>
      )}
      {n > 0 && (
        <ol className="transfers" aria-label="Transfers">
          {plan.transfers.map((t) => (
            <li className="transfer" key={t.out.player_id}>
              <span className="player out">
                <PlayerPhoto
                  code={codes.get(t.out.player_id)}
                  name={t.out.web_name}
                  team={t.out.team}
                  size={40}
                />
                <span className="player-text">
                  <span className="transfer-label">Out</span>
                  <Link className="player-name" to={`/players/${t.out.player_id}`}>
                    {t.out.web_name}
                  </Link>
                  <span className="player-meta">
                    <ClubBadge team={t.out.team} />
                    <span className="pos">
                      sells £{t.selling_price.toFixed(1)}m · {fixed(t.out.predicted_points, 1)} pts
                    </span>
                  </span>
                </span>
              </span>
              <span className="transfer-arrow" aria-hidden="true">
                →
              </span>
              <span className="player in">
                <PlayerPhoto
                  code={codes.get(t.into.player_id)}
                  name={t.into.web_name}
                  team={t.into.team}
                  size={40}
                />
                <span className="player-text">
                  <span className="transfer-label">In</span>
                  <Link className="player-name" to={`/players/${t.into.player_id}`}>
                    {t.into.web_name}
                  </Link>
                  <span className="player-meta">
                    <ClubBadge team={t.into.team} />
                    <span className="pos">
                      costs £{t.into.price.toFixed(1)}m · {fixed(t.into.predicted_points, 1)} pts
                    </span>
                  </span>
                </span>
              </span>
            </li>
          ))}
        </ol>
      )}
    </>
  );
}
