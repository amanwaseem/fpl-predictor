import { Link } from "react-router-dom";
import { useTitle } from "../components/bits";

export function NotFound() {
  useTitle("Not found");
  return (
    <div className="wrap page">
      <p className="eyebrow">404</p>
      <h1>Not found</h1>
      <p className="lede">This page has been substituted off. Try one of these instead:</p>
      <div className="hero-actions">
        <Link className="button" to="/">
          Home
        </Link>
        <Link className="button button-ghost" to="/gameweeks">
          Predictions
        </Link>
      </div>
    </div>
  );
}
