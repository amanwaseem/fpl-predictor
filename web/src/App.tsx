import { NavLink, Route, Routes } from "react-router-dom";
import { Gameweek, LatestGameweek } from "./pages/Gameweek";
import { NotFound } from "./pages/NotFound";
import { Player } from "./pages/Player";
import { Scorecard } from "./pages/Scorecard";

export function App() {
  return (
    <>
      <header className="site-header">
        <NavLink to="/" className="brand">
          fpl-predictor
        </NavLink>
        <nav>
          <NavLink to="/" end>
            Scorecard
          </NavLink>
          <NavLink to="/gameweeks">Predictions</NavLink>
          <a href="/docs">API</a>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Scorecard />} />
          <Route path="/gameweeks" element={<LatestGameweek />} />
          <Route path="/gameweeks/:gameweek" element={<Gameweek />} />
          <Route path="/players/:playerId" element={<Player />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
      <footer className="site-footer">
        Every prediction is committed before its deadline and never edited.{" "}
        <a href="https://github.com/amanwaseem/fpl-predictor">The log is on GitHub.</a>
      </footer>
    </>
  );
}
