import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, api, unwrap, type Schemas } from "../api/client";
import { useModels, usePhotoCodes, useStatus } from "../api/queries";
import { Chip, ClubBadge, Loading, PlayerPhoto } from "../components/bits";
import { useTitle } from "../useTitle";
import { Problem } from "../components/Problem";
import { SquadView } from "../components/SquadView";
import { fixed, formatLocal, positiveInt, signed } from "../format";
import { liveModels, modelName } from "../models";
import { forgetTeam, saveTeam, savedTeam, type MyTeam } from "../remember";
import { ENTRY_WINDOW_HOURS } from "../week";

type Plan = Schemas["Plan"];

export function Optimise() {
  useTitle("My team");
  const [params, setParams] = useSearchParams();
  const models = useModels();
  const status = useStatus();
  const live = liveModels(models.data?.models);
  const main = live.find((m) => m.role === "main") ?? live[0];

  // What is on screen comes from the URL alone; the form is applied on submit.
  // Advice comes from the main model (#194). A link may still name another,
  // which is kept for its own team; with none known, the API's default is used.
  const team = positiveInt(params.get("team") ?? undefined);
  const linked = params.get("model");
  const model = linked ?? main?.model_version;
  // Named only when it is known not to be the main model.
  const notMain = (version: string) =>
    linked !== null && main !== undefined && version !== main.model_version;
  const withoutModel = () => {
    const next = new URLSearchParams(params);
    next.delete("model");
    return `?${next}`;
  };
  // Free transfers, if the reader gave them, and whether to take hits (#198).
  const freeParam = params.get("free");
  const free = freeParam !== null && /^[0-5]$/.test(freeParam) ? Number(freeParam) : undefined;
  const noHits = params.get("hits") === "0";
  const maxHits = noHits ? 0 : undefined;
  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  };

  // The team box follows the URL, so back and forward leave the two agreeing,
  // and only the URL: the models list arriving must not clear what was typed.
  // Reset while rendering, not in an effect: React's pattern for state that
  // follows a prop (react.dev, "You might not need an effect"). Compared as
  // text, so a new params object for the same URL changes nothing.
  const [typed, setTyped] = useState(params.get("team") ?? "");
  const [changing, setChanging] = useState(false);
  const search = params.toString();
  const [formSearch, setFormSearch] = useState(search);
  if (search !== formSearch) {
    setFormSearch(search);
    setTyped(params.get("team") ?? "");
    setChanging(false);
  }

  // The reader's own team, remembered in this browser (#176). My team with no
  // team in the link shows it, however it was reached; a link with a team
  // shows that one and leaves the remembered one alone. Only a team the reader
  // asked for here is remembered, once advice for it has come back.
  const [saved, setSaved] = useState<MyTeam | null>(savedTeam);
  useEffect(() => {
    if (params.has("team") || saved === null) return;
    const next = new URLSearchParams(params);
    next.set("team", String(saved.team));
    setParams(next, { replace: true });
  }, [params, saved, setParams]);
  // A team asked for on the form, remembered once advice for it comes back.
  const toRemember = useRef<number | null>(null);
  // Only said to be remembered if it was: storage may refuse.
  const remember = (id: number) => {
    if (saveTeam({ team: id })) setSaved({ team: id });
  };
  const queryClient = useQueryClient();
  // Forgetting takes the team off the page only if it is the remembered one,
  // and in place: Back must not bring it straight back.
  const forget = () => {
    forgetTeam();
    setSaved(null);
    if (saved !== null && team === saved.team) {
      const next = new URLSearchParams(params);
      next.delete("team");
      next.delete("free");
      setParams(next, { replace: true });
    }
  };

  // Between a deadline and the next gameweek's predictions there is no advice
  // worth giving: last week's would be stale (#198). The status says so; if it
  // cannot be read, advice is asked for as before.
  const between = !!status.data && status.data.next_gameweek_entries.length === 0;
  const asking =
    team !== null && (params.has("model") || !models.isPending) && !status.isPending && !between;
  const advice = useQuery({
    queryKey: ["advice", team, model, free, maxHits],
    // With no model in the link, wait for the models list to name the main
    // one, rather than ask once for the API's default and again for main.
    enabled: asking,
    // Asking costs an FPL API call and a solve: not again just because the
    // reader came back to the tab. "Get my advice" asks again.
    refetchOnWindowFocus: false,
    // A failure here may be the FPL API's; asking it again at once rarely helps.
    retry: false,
    queryFn: async () => {
      const data = await unwrap(
        api.GET("/v1/entries/{fpl_team_id}/advice", {
          params: {
            path: { fpl_team_id: team! },
            query: { model, free_transfers: free, max_hits: maxHits },
          },
        }),
      );
      if (toRemember.current === team) {
        toRemember.current = null;
        remember(team!);
      }
      return data;
    },
  });
  // Advice whose deadline has gone is last week's: not shown as advice.
  const stale = advice.data?.deadline_passed ?? false;

  const fresh = useMutation({
    mutationFn: () => unwrap(api.POST("/v1/optimise", { body: { model } })),
  });

  // Photos for the picks, from the same entry's predictions.
  const adviceCodes = usePhotoCodes(advice.data?.gameweek, advice.data?.model_version);
  const freshCodes = usePhotoCodes(fresh.data?.gameweek, fresh.data?.model_version);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const asked = positiveInt(typed.trim());
    const next = new URLSearchParams();
    next.set("team", typed.trim());
    // A linked model goes with the linked team: another team gets main advice.
    // Free transfers are a team's own; whether to take hits is the reader's.
    if (asked === team) {
      if (linked) next.set("model", linked);
      if (freeParam !== null) next.set("free", freeParam);
    }
    if (noHits) next.set("hits", "0");
    // Advice already cached for this request is shown without a fetch, so the
    // team is remembered now; otherwise once the fetch succeeds.
    const nextFree = next.get("free");
    const cachedKey = [
      "advice",
      asked,
      next.get("model") ?? main?.model_version,
      nextFree === null ? undefined : Number(nextFree),
      maxHits,
    ];
    if (asked !== null && queryClient.getQueryData(cachedKey) !== undefined) remember(asked);
    else toRemember.current = asked;
    setChanging(false);
    // The same team again is asked again: the squad may have changed on FPL
    // since, and the answer is cached for five minutes. No new history entry
    // for it either; and only once it can be asked, as refetch ignores
    // `enabled`.
    if (asked === team && asking) void advice.refetch();
    else setParams(next);
  };

  // The box shows on a first visit, when the reader asks to change team, and
  // when the team asked for could not be read, so it can be corrected.
  const showForm = team === null || changing || advice.isError;

  return (
    <div className="wrap page">
      <div className="page-head">
        <div>
          <p className="eyebrow">My team</p>
          <h1>{team === null ? "Get advice for your team" : "Your advice"}</h1>
          {team === null ? (
            <p className="lede">
              Your best transfers, starting XI and captain this gameweek, from our latest
              predictions.
            </p>
          ) : (
            <p className="team-line">
              Team {team} ·{" "}
              <button
                type="button"
                className="link-button"
                aria-expanded={showForm}
                onClick={() => setChanging(!changing)}
              >
                change
              </button>
            </p>
          )}
          {saved && (
            <p className="muted small remembered">
              Remembered in this browser: team {saved.team}.{" "}
              <button type="button" className="link-button" onClick={forget}>
                Forget my team
              </button>
            </p>
          )}
        </div>
      </div>

      {showForm && (
        <form className="card team-form" onSubmit={submit}>
          <div className="controls">
            <label className="field">
              Your FPL team ID
              <input
                aria-label="FPL team ID"
                inputMode="numeric"
                pattern="[0-9]*"
                placeholder="e.g. 1234567"
                required
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
              />
            </label>
            <button type="submit" className="button">
              Get my advice
            </button>
          </div>
          <ol className="help-steps small">
            <li>Sign in at fantasy.premierleague.com and open the Points tab.</li>
            <li>
              The address ends like <code>/entry/1234567/event/9</code>: the number after{" "}
              <code>entry</code> is your team ID.
            </li>
            <li>
              No password is needed: your squad is public on FPL. Your team ID is remembered in this
              browser only.
            </li>
          </ol>
        </form>
      )}

      {models.isError && <Problem error={models.error} />}
      {team !== null && (between || stale) && status.data && <Between status={status.data} />}
      {advice.isFetching && <Loading what="Reading your team and working out the best moves" />}
      {advice.isError && <TeamProblem error={advice.error} team={team} />}
      {advice.data && !stale && (
        <section className="section" aria-labelledby="advice">
          <div className="section-head">
            <h2 id="advice">Your advice for gameweek {advice.data.gameweek}</h2>
            <span className="muted small">
              {notMain(advice.data.model_version) && (
                <>
                  From the {modelName(advice.data.model_version)} model (
                  <Link to={withoutModel()}>see our main advice</Link>) ·{" "}
                </>
              )}
              Deadline {formatLocal(advice.data.deadline_utc)}
            </span>
          </div>
          {advice.data.plan && (
            <PlanView
              plan={advice.data.plan}
              codes={adviceCodes}
              freeGiven={advice.data.estimates.free_transfers === "given"}
              onFree={(n) => setParam("free", n === null ? null : String(n))}
              noHits={noHits}
              onNoHits={(on) => setParam("hits", on ? "0" : null)}
            />
          )}
          <h3 className="section">Your best XI</h3>
          <SquadView squad={advice.data.squad} label="Advised squad" codes={adviceCodes} />
          <details className="help after">
            <summary>How we worked this out</summary>
            <p>
              Based on your squad as of gameweek {advice.data.picks_gameweek}. FPL keeps selling
              prices and free transfers private, so unless you gave them they were estimated:
            </p>
            <ul>
              <li>Selling prices: {sentence(advice.data.estimates.selling_prices)}</li>
              <li>
                Free transfers:{" "}
                {advice.data.estimates.free_transfers === "given"
                  ? "as you gave them."
                  : sentence(advice.data.estimates.free_transfers)}
              </li>
            </ul>
          </details>
        </section>
      )}

      <section className="section card" aria-labelledby="fresh">
        <div className="section-head">
          <div>
            <h2 id="fresh">Playing a wildcard or free hit?</h2>
            <p className="muted small" style={{ margin: 0 }}>
              A wildcard rebuilds your squad for the rest of the season; a free hit, for one
              gameweek only. Either way, here is the best fifteen within £100m under every FPL rule,
              picked from scratch for this gameweek.
            </p>
          </div>
          <button
            type="button"
            className="button"
            disabled={fresh.isPending || between}
            onClick={() => fresh.mutate()}
          >
            {fresh.isPending ? "Picking…" : "Pick the best squad"}
          </button>
        </div>
        {between && (
          <p className="muted small">
            The best squad for the next gameweek comes with its predictions.
          </p>
        )}
        {fresh.isError && <Problem error={fresh.error} />}
        {fresh.data && (
          <>
            <p className="muted small">
              Gameweek {fresh.data.gameweek}
              {notMain(fresh.data.model_version)
                ? `, from the ${modelName(fresh.data.model_version)} model`
                : ""}
              .{" "}
              <Link
                to={
                  linked
                    ? `/gameweeks/${fresh.data.gameweek}?model=${fresh.data.model_version}`
                    : `/gameweeks/${fresh.data.gameweek}`
                }
              >
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

// Between a deadline and the next predictions: when the advice comes, and a
// way to see how the gameweek just gone is going.
function Between({ status }: { status: Schemas["Status"] }) {
  const [now] = useState(() => Date.now());
  const next = status.next_gameweek;
  const deadline = status.next_deadline_utc;
  if (!next || !deadline)
    return (
      <section className="section card">
        <h2>The season is over</h2>
        <p className="muted">Advice comes back with next season&rsquo;s first predictions.</p>
      </section>
    );
  const arrives = Date.parse(deadline) - ENTRY_WINDOW_HOURS * 3_600_000;
  const day = new Date(arrives).toLocaleDateString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
  return (
    <section className="section card" aria-labelledby="between">
      <h2 id="between">
        GW{next} advice arrives {arrives > now ? day : "before the deadline"}
      </h2>
      <p className="muted">
        It comes with our gameweek {next} predictions, about a day before the{" "}
        {formatLocal(deadline)} deadline. Come back then.
      </p>
      {next > 1 && (
        <Link to={`/gameweeks/${next - 1}`}>See how gameweek {next - 1} is going →</Link>
      )}
    </section>
  );
}

function PlanView({
  plan,
  codes,
  freeGiven,
  onFree,
  noHits,
  onNoHits,
}: {
  plan: Plan;
  codes: Map<number, number | null>;
  freeGiven: boolean;
  onFree: (n: number | null) => void;
  noHits: boolean;
  onNoHits: (on: boolean) => void;
}) {
  const overCap = Object.entries(plan.over_cap);
  const n = plan.transfers.length;
  const [choosing, setChoosing] = useState(false);
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
          Bank £{fixed(plan.bank, 1)}m{n ? `, then £${fixed(plan.bank_after, 1)}m` : ""}.
        </p>
        {/* The two things a manager may know better than the estimate (#198). */}
        <div className="plan-controls">
          <span>
            {freeGiven ? "With" : "Assuming"} {plan.free_transfers} free transfer
            {plan.free_transfers === 1 ? "" : "s"} ·{" "}
            <button
              type="button"
              className="link-button"
              aria-expanded={choosing}
              onClick={() => setChoosing(!choosing)}
            >
              change
            </button>
          </span>
          <label className="switch">
            <input type="checkbox" checked={noHits} onChange={(e) => onNoHits(e.target.checked)} />
            Don&rsquo;t take hits
          </label>
        </div>
        {choosing && (
          <div className="chips" role="group" aria-label="Free transfers">
            {[0, 1, 2, 3, 4, 5].map((k) => (
              <Chip
                key={k}
                pressed={freeGiven && plan.free_transfers === k}
                onClick={() => {
                  setChoosing(false);
                  onFree(k);
                }}
              >
                {k}
              </Chip>
            ))}
            <Chip
              pressed={!freeGiven}
              onClick={() => {
                setChoosing(false);
                onFree(null);
              }}
            >
              Work it out
            </Chip>
          </div>
        )}
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

// The API's estimate notes are fragments ("purchase price from ..."): as a sentence.
function sentence(text: string) {
  return text.endsWith(".") ? text : `${text}.`;
}

// The ways asking for advice usually fails, in a manager's words rather than
// the API's: a mistyped team ID, or FPL not answering. Anything else as it is.
function TeamProblem({ error, team }: { error: Error; team: number | null }) {
  if (error instanceof ApiError) {
    const unknownTeam =
      // Only the team's own record: a 404 for its picks or history is not this.
      (error.status === 404 && (error.problem.detail ?? "").endsWith(`entry/${team}/.`)) ||
      (error.status === 422 &&
        (error.problem.errors ?? []).some((e) => JSON.stringify(e.loc).includes("fpl_team_id")));
    if (unknownTeam) {
      return (
        <div role="alert" className="problem">
          <strong>No FPL team has the ID {team ?? "given"}</strong>
          <p>
            Check the number after <code>/entry/</code> in the address of your FPL Points page: see
            &ldquo;Where do I find my team ID?&rdquo; above.
          </p>
        </div>
      );
    }
    if (error.status === 502 || error.status === 504) {
      return (
        <div role="alert" className="problem">
          <strong>FPL isn&rsquo;t answering right now</strong>
          <p>
            Your squad is read from FPL, which is sometimes down around deadlines and while it
            updates between gameweeks. Try again in a few minutes.
          </p>
        </div>
      );
    }
  }
  return <Problem error={error} />;
}
