import { ApiError } from "../api/client";

// An error as the API words it (problem details), or a plain one for a network failure.
export function Problem({ error }: { error: Error }) {
  const title = error instanceof ApiError ? (error.problem.title ?? "Error") : "Could not reach the API";
  return (
    <div role="alert" className="problem">
      <strong>{title}</strong>
      {error.message !== title && <p>{error.message}</p>}
    </div>
  );
}
