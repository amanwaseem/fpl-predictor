import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../api/client";
import { Problem } from "../components/Problem";
import { formatUtc } from "../format";

export function Home() {
  const status = useQuery({
    queryKey: ["status"],
    queryFn: () => unwrap(api.GET("/v1/status")),
  });

  return (
    <section>
      <h1>FPL points, predicted in public</h1>
      <p className="lede">
        Each gameweek, every model's predictions for every player are committed before the
        deadline, then scored against what happened.
      </p>
      {status.isPending && <p aria-busy="true">Loading…</p>}
      {status.isError && <Problem error={status.error} />}
      {status.data && (
        <dl className="facts" aria-label="Status">
          <div>
            <dt>Season</dt>
            <dd>{status.data.season}</dd>
          </div>
          <div>
            <dt>Next gameweek</dt>
            <dd>{status.data.next_gameweek ?? "none scheduled"}</dd>
          </div>
          <div>
            <dt>Next deadline</dt>
            <dd>{formatUtc(status.data.next_deadline_utc)}</dd>
          </div>
          <div>
            <dt>Entries for it</dt>
            <dd>{status.data.next_gameweek_entries.join(", ") || "none yet"}</dd>
          </div>
          <div>
            <dt>Latest settled gameweek</dt>
            <dd>{status.data.latest_settled_gameweek ?? "none yet"}</dd>
          </div>
        </dl>
      )}
    </section>
  );
}
