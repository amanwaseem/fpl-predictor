import { Link } from "react-router-dom";
import { useModels } from "../api/queries";
import { REPOSITORY } from "../log";
import { modelAbout, modelName, roleName } from "../models";
import { useTitle } from "../useTitle";

// How the site works, for FPL managers rather than developers (#176).
export function About() {
  useTitle("How it works");
  const models = useModels();
  const logged = (models.data?.models ?? []).filter((m) => m.role && m.role !== "retired");

  return (
    <div className="wrap page prose">
      <p className="eyebrow">How it works</p>
      <h1>Predictions you can check</h1>
      <p className="lede">
        Before every FPL deadline, each model here predicts how many points every Premier League
        player will score. The predictions are published before the deadline, never changed, and
        scored once the gameweek is over.
      </p>

      <section className="section" aria-labelledby="reading">
        <h2 id="reading">Reading a prediction</h2>
        <p>
          A prediction is a player&rsquo;s <strong>expected points</strong> for the gameweek: what
          the player would score on average if the gameweek were played many times. A 6
          doesn&rsquo;t mean 6 points this week. It means a better bet than a 4. Most weeks a few
          players blank and a few haul, and the predictions are about who is likelier to.
        </p>
        <p>
          On <Link to="/gameweeks">Predictions</Link>, each player has predicted points and minutes
          for the whole gameweek, so a double gameweek counts both matches. Once the gameweek is
          scored, the actual points sit beside them. A player with a status flag (a doubt, an injury
          or a suspension) has the prediction scaled by FPL&rsquo;s chance of playing.
        </p>
      </section>

      <section className="section" aria-labelledby="my-team">
        <h2 id="my-team">Advice for your team</h2>
        <p>
          <Link to="/optimise">My team</Link> reads your squad from FPL using your team ID, which is
          public, so there&rsquo;s no sign-in. It then works out the transfers, starting XI and
          captain that score the most predicted points within FPL&rsquo;s rules. A transfer that
          costs a hit is only suggested when it gains more than the 4 points it costs. Your team ID
          is remembered in your browser only, and nowhere else.
        </p>
        <p>
          FPL keeps your selling prices and free transfers private, so they are estimated unless you
          give them. If the advice suggests a sale you can&rsquo;t afford, set your free transfers
          and check your selling prices in the FPL app.
        </p>
      </section>

      <section className="section" aria-labelledby="models">
        <h2 id="models">The models</h2>
        <ul>
          {logged.map((m) => (
            <li key={m.model_version}>
              <strong>{modelName(m.model_version)}</strong>
              {roleName(m.role) && roleName(m.role) !== modelName(m.model_version)
                ? ` (${roleName(m.role)?.toLowerCase()})`
                : ""}
              : {modelAbout(m.model_version) ?? m.model_version}
            </li>
          ))}
        </ul>
        <p>
          The baseline is deliberately simple. A model is only worth using if it beats it, and the{" "}
          <Link to="/">track record on the home page</Link> shows by how much each one does.
        </p>
      </section>

      <section className="section" aria-labelledby="trust">
        <h2 id="trust">Why you can trust the track record</h2>
        <p>
          Every prediction is committed to a{" "}
          <a href={`${REPOSITORY}/tree/main/predictions`}>public log on GitHub</a> before the
          deadline. Git&rsquo;s history shows when each one was made, and a check refuses any change
          to a prediction once it&rsquo;s there. A model can&rsquo;t quietly drop its bad weeks: a
          missed deadline stays missed. Each gameweek page links to the proof that its predictions
          were published in time.
        </p>
      </section>

      <section className="section" aria-labelledby="limits">
        <h2 id="limits">What it can&rsquo;t do</h2>
        <ul>
          <li>
            Predictions are made from data taken a few hours before the deadline. Team news after
            that, such as a late injury or a manager&rsquo;s press conference, isn&rsquo;t in them.
          </li>
          <li>
            They run slightly low, mostly for regular starters, and defenders above all, whose
            defensive contribution points the models don&rsquo;t fully expect.{" "}
            <a href={`${REPOSITORY}/blob/main/reports/bias-2026-27.md`}>The full analysis</a>{" "}
            explains why, and what was tried.
          </li>
          <li>
            A point or two either way is normal for a single player in a single week. Predictions
            are most useful for comparing players, not for promising a score.
          </li>
        </ul>
      </section>

      <p className="note small">
        An independent fan project, not affiliated with the Premier League or Fantasy Premier
        League. Free, with no accounts, no ads and no tracking.
      </p>
    </div>
  );
}
