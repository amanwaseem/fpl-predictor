import { useEffect } from "react";
import { Link, NavLink, Route, Routes, useLocation, useNavigationType } from "react-router-dom";
import { SITE_NAME } from "./brand";
import { REPOSITORY } from "./log";
import { Gameweek, LatestGameweek } from "./pages/Gameweek";
import { Home } from "./pages/Home";
import { NotFound } from "./pages/NotFound";
import { Optimise } from "./pages/Optimise";
import { Player } from "./pages/Player";

export function App() {
  // A new page starts at the top, as a link to another site would; Back and
  // Forward leave the browser to restore where the reader was.
  const { pathname } = useLocation();
  const navigation = useNavigationType();
  useEffect(() => {
    if (navigation !== "POP") window.scrollTo(0, 0);
  }, [pathname, navigation]);

  return (
    <>
      <header className="site-header">
        <div className="wrap">
          <Link to="/" className="brand">
            <span className="brand-dot" aria-hidden="true" />
            {SITE_NAME}
          </Link>
          <nav className="site-nav" aria-label="Main">
            <NavLink to="/" end>
              Home
            </NavLink>
            <NavLink to="/gameweeks">Predictions</NavLink>
            <NavLink to="/optimise">My team</NavLink>
          </nav>
        </div>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/gameweeks" element={<LatestGameweek />} />
          <Route path="/gameweeks/:gameweek" element={<Gameweek />} />
          <Route path="/players/:playerId" element={<Player />} />
          <Route path="/optimise" element={<Optimise />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
      <footer className="site-footer">
        <div className="wrap">
          <p>
            Every prediction is published before its deadline and never edited afterwards, so the
            track record is the real one. An independent fan project, not affiliated with the
            Premier League or Fantasy Premier League.
          </p>
          <nav className="footer-links" aria-label="More">
            <a href={REPOSITORY}>Prediction log on GitHub</a>
            <a href="/docs">API for developers</a>
          </nav>
        </div>
      </footer>
    </>
  );
}
