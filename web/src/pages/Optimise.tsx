import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api/client";
import { Problem } from "../components/Problem";
import { SquadView } from "../components/SquadView";
import { fixed, formatUtc, positiveInt, signed } from "../format";

type Plan = Schemas["Plan"];

export function Optimise() {
  const [params, setParams] = useSearchParams();
  const models = useQuery({ queryKey: ["models"], queryFn: () => unwrap(api.GET("/v1/models")) });
  const live = (models.data?.models ?? []).filter((m) => m.role && m.role !== "retired");
  const main = live.find((m) => m.role === "main") ?? live[0];

  const team = positiveInt(params.get("team") ?? undefined);
  const model = params.get("model") ?? main?.model_version;
  const freeParam = params.get("free");
  const free = freeParam !== null && /^[0-5]$/.test(freeParam) ? Number(freeParam) : undefined;

  const [teamInput, setTeamInput] = useState(params.get("team") ?? "");
  const [freeInput, setFreeInput] = useState(freeParam ?? "");

  const advice = useQuery({
    queryKey: ["advice", team, model, free],
    enabled: team !== null && !!model,
    queryFn: () =>
      unwrap(
        api.GET("/v1/entries/{fpl_team_id}/advice", {
          params: { path: { fpl_team_id: team! }, query: { model: model!, free_transfers: free } },
        }),
      ),
  });

  const fresh = useMutation({
    mutationFn: () => unwrap(api.POST("/v1/optimise", { body: { model: model! } })),
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const next = new URLSearchParams();
    next.set("team", teamInput.trim());
    if (model) next.set("model", model);
    if (freeInput !== "") next.set("free", freeInput);
    setParams(next);
  };

  return (
    <section>
      <h1>Optimise my team</h1>
      <p className="lede">
        The best transfers, starting XI and captain for your FPL team, from a model's latest
        committed predictions. Your team ID is the number in the address of your points page on
        the FPL site.
      </p>

      <form className="controls" onSubmit={submit}>
        <label>
          FPL team ID
          <input
            inputMode="numeric"
            pattern="[0-9]*"
            required
            value={teamInput}
            onChange={(e) => setTeamInput(e.target.value)}
          />
        </label>
        <label>
          Model
          <select
            value={model ?? ""}
            onChange={(e) => {
              const next = new URLSearchParams(params);
              next.set("model", e.target.value);
              setParams(next);
            }}
          >
            {live.map((m) => (
              <option key={m.model_version} value={m.model_version}>
                {m.model_version} ({m.role})
              </option>
            ))}
          </select>
        </label>
        <label>
          Free transfers
          <select value={freeInput} onChange={(e) => setFreeInput(e.target.value)}>
            <option value="">Estimate</option>
            {[0, 1, 2, 3, 4, 5].map((n) => (
              <option key={n}>{n}</option>
            ))}
          </select>
        </label>
        <button type="submit" className="button">
          Get advice
        </button>
      </form>

      {models.isError && <Problem error={models.error} />}
      {advice.isFetching && <p aria-busy="true">Reading your team and solving…</p>}
      {advice.isError && <Problem error={advice.error} />}
      {advice.data && (
        <section aria-labelledby="advice">
          <h2 id="advice">
            Gameweek {advice.data.gameweek}, from <code>{advice.data.model_version}</code>
          </h2>
          {advice.data.deadline_passed && (
            <p role="alert" className="problem">
              The deadline for gameweek {advice.data.gameweek} has passed (
              {formatUtc(advice.data.deadline_utc)}). This is what the model would have advised.
            </p>
          )}
          <p className="note">
            Your squad as of gameweek {advice.data.picks_gameweek}. FPL keeps two things private,
            so they are estimated:
          </p>
          <ul className="note">
            <li>Selling prices: {advice.data.estimates.selling_prices}</li>
            <li>Free transfers: {advice.data.estimates.free_transfers}</li>
          </ul>
          {advice.data.plan && <PlanView plan={advice.data.plan} />}
          <h3>Your XI</h3>
          <SquadView squad={advice.data.squad} label="Advised squad" />
        </section>
      )}

      <section aria-labelledby="fresh">
        <h2 id="fresh">Best squad from scratch</h2>
        <p className="note">
          The best fifteen within £100m under every FPL rule, for wildcards and free hits.
        </p>
        <button
          type="button"
          className="button"
          disabled={!model || fresh.isPending}
          onClick={() => fresh.mutate()}
        >
          {fresh.isPending ? "Solving…" : `Pick the best squad from ${model ?? "…"}`}
        </button>
        {fresh.isError && <Problem error={fresh.error} />}
        {fresh.data && (
          <>
            <p className="note">
              Gameweek {fresh.data.gameweek}, from <code>{fresh.data.model_version}</code>.
            </p>
            <SquadView squad={fresh.data.squad} label="Best squad" />
          </>
        )}
      </section>
    </section>
  );
}

function PlanView({ plan }: { plan: Plan }) {
  return (
    <>
      <h3>Transfers</h3>
      {plan.transfers.length === 0 ? (
        <p>Keep your squad: no transfer gains enough to be worth making.</p>
      ) : (
        <div className="table-scroll">
          <table aria-label="Transfers">
            <thead>
              <tr>
                <th scope="col">Out</th>
                <th scope="col" className="num">Sells for</th>
                <th scope="col">In</th>
                <th scope="col" className="num">Costs</th>
                <th scope="col" className="num">Predicted gain</th>
              </tr>
            </thead>
            <tbody>
              {plan.transfers.map((t) => (
                <tr key={t.out.player_id}>
                  <td>
                    {t.out.web_name} <span className="muted">{t.out.team}</span>
                  </td>
                  <td className="num">£{t.selling_price.toFixed(1)}m</td>
                  <th scope="row">
                    {t.into.web_name} <span className="muted">{t.into.team}</span>
                  </th>
                  <td className="num">£{t.into.price.toFixed(1)}m</td>
                  <td className="num">{signed(t.into.predicted_points - t.out.predicted_points, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="note">
        {plan.free_transfers} free transfer{plan.free_transfers === 1 ? "" : "s"}
        {plan.hits ? `, ${plan.hits} hit${plan.hits === 1 ? "" : "s"} for −${plan.hit_points} points` : ""}.
        Gain after hits: {signed(plan.gain, 2)} points. Bank £{fixed(plan.bank, 1)}m, then £
        {fixed(plan.bank_after, 1)}m.
      </p>
    </>
  );
}
