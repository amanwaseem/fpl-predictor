import { Link } from "react-router-dom";

export function NotFound() {
  return (
    <section>
      <h1>Not found</h1>
      <p>
        There is no page here. <Link to="/">Back to the start.</Link>
      </p>
    </section>
  );
}
