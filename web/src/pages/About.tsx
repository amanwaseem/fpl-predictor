import { Link } from "react-router-dom";
import { REPOSITORY } from "../log";
import { useTitle } from "../useTitle";

// How the site works, as the questions managers ask (#200): short answers,
// in the footer rather than the menu. The models and why to trust the record
// are on Track record.
export function About() {
  useTitle("How it works");
  return (
    <div className="wrap page prose faq">
      <p className="eyebrow">How it works</p>
      <h1>Questions and answers</h1>
      <p className="lede">
        Points predictions for every Premier League player, published before each FPL deadline and
        never changed afterwards.
      </p>

      <h2>What does &ldquo;5.2 points&rdquo; mean?</h2>
      <p>
        What the player would score on average if the gameweek were played many times. It
        isn&rsquo;t a promise for this week: a 6 is a better bet than a 4, and a double gameweek
        counts both matches.
      </p>

      <h2>Where do I find my team ID?</h2>
      <p>
        Sign in at fantasy.premierleague.com and open the Points tab. The address ends like{" "}
        <code>/entry/1234567/event/9</code>: the number after <code>entry</code> is your team ID.
        Put it in <Link to="/optimise">My team</Link>.
      </p>

      <h2>Do you need my password?</h2>
      <p>
        No. Your squad is public on FPL, so the team ID is all it takes. It&rsquo;s remembered in
        your browser only, and nowhere else.
      </p>

      <h2>When do new predictions come out?</h2>
      <p>
        About a day before each deadline, once the gameweek before it is over and its bonus points
        are confirmed. On a tight turnaround, as at Christmas, they come later, but always before
        the deadline.
      </p>

      <h2>Why might the advice be wrong?</h2>
      <p>
        Team news after the predictions are made, such as a late injury, isn&rsquo;t in them. FPL
        keeps selling prices and free transfers private, so they&rsquo;re estimated unless you give
        them. And a point or two either way is normal for one player in one week.
      </p>

      <h2>Is this official FPL?</h2>
      <p>
        No. It&rsquo;s an independent fan project, not affiliated with the Premier League or Fantasy
        Premier League. Free, with no accounts, no ads and no tracking.
      </p>

      <h2>How good are the predictions?</h2>
      <p>
        Every one is scored once its gameweek is over, misses included.{" "}
        <Link to="/track-record">The track record</Link> shows how far off they are and how each
        model compares, and the <a href={`${REPOSITORY}/tree/main/predictions`}>public log</a>{" "}
        proves when each was made.
      </p>
    </div>
  );
}
