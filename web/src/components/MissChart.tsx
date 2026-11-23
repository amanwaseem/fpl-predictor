// The Track record chart (#195): the average miss per player in each
// gameweek, our predictions against going by form, as inline SVG with no
// chart library. A gameweek our predictions missed breaks the line and is
// marked missed, as the scorecard marks it (ADR 0004). The scorecard tables
// on the same page are its table view, and each scored week is a link to its
// predictions, so a keyboard reaches every figure the pointer does.
import { useEffect, useRef, useState, type CSSProperties, type RefObject } from "react";
import { Link } from "react-router-dom";
import type { Week } from "../scorecard";

// `className` picks the line's colour in the stylesheet: "ours" or "form".
export type Series = { name: string; className: string; weeks: Week[] };

const HEIGHT = 240;
// Room for the scale on the left, and two lines of labels below the plot.
const MARGIN = { top: 12, right: 8, bottom: 44, left: 34 };

export function MissChart({ series, missed }: { series: Series[]; missed: number[] }) {
  const box = useRef<HTMLDivElement>(null);
  const width = useWidth(box);
  const [active, setActive] = useState<number | null>(null);
  // On a touch screen the first tap on a week shows its figures and the second
  // opens it; a tap has no hover to show them first.
  const firstTap = useRef(false);

  const scored = series.flatMap((s) => s.weeks);
  const all = [...scored.map((w) => w.gameweek), ...missed];
  const first = Math.min(...all);
  const gameweeks = range(first, Math.max(...all));
  // The top of the scale: the worst week, rounded up to the next half point.
  const top = Math.max(0.5, Math.ceil(Math.max(...scored.map((w) => w.mae)) * 2) / 2);
  const ticks = range(0, top * 2).map((i) => i / 2);

  const plotWidth = Math.max(0, width - MARGIN.left - MARGIN.right);
  const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom;
  const band = plotWidth / gameweeks.length;
  const x = (gw: number) => MARGIN.left + (gw - first + 0.5) * band;
  const y = (mae: number) => MARGIN.top + plotHeight * (1 - mae / top);
  // "GW10" needs about 36px and "10" about 22px. Where the weeks are narrower
  // than that, only every second, fifth or tenth week is labelled.
  const short = band < 36;
  const step = [1, 2, 5, 10].find((s) => s * band >= (short ? 22 : 36)) ?? 10;

  const at = (s: Series, gw: number) => s.weeks.find((w) => w.gameweek === gw);
  const values = (gw: number) =>
    series.flatMap((s) => {
      const week = at(s, gw);
      return week ? [{ series: s, mae: week.mae }] : [];
    });
  const describe = (gw: number) =>
    `GW${gw}: ${values(gw)
      .map((v) => `${v.series.name.toLowerCase()} ${v.mae.toFixed(2)}`)
      .join(", ")} points off per player`;

  return (
    <figure className="miss-chart">
      <ul className="chart-legend">
        {series.length > 1 &&
          series.map((s) => (
            <li key={s.name}>
              <i className={`key ${s.className}`} />
              {s.name}
            </li>
          ))}
        {missed.length > 0 && (
          <li>
            <i className="key key-missed" />
            Missed deadline
          </li>
        )}
      </ul>
      <div
        className="chart"
        ref={box}
        // A touch's pointer leaves as the finger lifts: the readout stays.
        onPointerLeave={(e) => e.pointerType !== "touch" && setActive(null)}
      >
        {width > 0 && (
          <svg
            width={width}
            height={HEIGHT}
            role="group"
            aria-label="Average miss per player, each gameweek"
          >
            <g aria-hidden="true">
              {ticks.map((t) => (
                <g key={t}>
                  <line
                    className="gridline"
                    x1={MARGIN.left}
                    x2={width - MARGIN.right}
                    y1={y(t)}
                    y2={y(t)}
                  />
                  <text className="tick" x={MARGIN.left - 8} y={y(t)} dy="0.32em" textAnchor="end">
                    {t === 0 ? "0" : t.toFixed(1)}
                  </text>
                </g>
              ))}
              {missed.map((gw) => (
                <rect
                  key={gw}
                  className="missed-week"
                  x={x(gw) - band / 2 + 1}
                  y={MARGIN.top}
                  width={Math.max(0, band - 2)}
                  height={plotHeight}
                  rx={4}
                />
              ))}
              {active !== null && (
                <line
                  className="crosshair"
                  x1={x(active)}
                  x2={x(active)}
                  y1={MARGIN.top}
                  y2={MARGIN.top + plotHeight}
                />
              )}
              {/* Going by form first, so our line is drawn over it where they meet. */}
              {[...series].reverse().map((s) => (
                <g key={s.name} className={`series ${s.className}`}>
                  {runs(s.weeks).map((run) => (
                    <path
                      key={run[0].gameweek}
                      d={run
                        .map((w, i) => `${i ? "L" : "M"}${x(w.gameweek)},${y(w.mae)}`)
                        .join(" ")}
                    />
                  ))}
                  {s.weeks.map((w) => (
                    <circle key={w.gameweek} cx={x(w.gameweek)} cy={y(w.mae)} r={4} />
                  ))}
                </g>
              ))}
              {gameweeks
                .filter((gw) => step === 1 || gw % step === 0)
                .map((gw) => (
                  <text
                    key={gw}
                    className="tick"
                    x={x(gw)}
                    y={MARGIN.top + plotHeight + 18}
                    textAnchor="middle"
                  >
                    {short ? gw : `GW${gw}`}
                  </text>
                ))}
              {!short &&
                missed.map((gw) => (
                  <text
                    key={gw}
                    className="tick tick-missed"
                    x={x(gw)}
                    y={MARGIN.top + plotHeight + 34}
                    textAnchor="middle"
                  >
                    missed
                  </text>
                ))}
            </g>
            {/* A week with no figures clears the readout, rather than leaving
                the week before's up beside it. */}
            {gameweeks
              .filter((gw) => values(gw).length === 0)
              .map((gw) => (
                <rect
                  key={gw}
                  className="hit"
                  aria-hidden="true"
                  x={x(gw) - band / 2}
                  y={MARGIN.top}
                  width={band}
                  height={plotHeight + MARGIN.bottom}
                  onPointerEnter={() => setActive(null)}
                />
              ))}
            {gameweeks
              .filter((gw) => values(gw).length > 0)
              .map((gw) => (
                <Link
                  key={gw}
                  to={`/gameweeks/${gw}`}
                  aria-label={describe(gw)}
                  onPointerEnter={(e) => e.pointerType !== "touch" && setActive(gw)}
                  onPointerDown={(e) => {
                    firstTap.current = e.pointerType === "touch" && active !== gw;
                  }}
                  onClick={(e) => {
                    if (!firstTap.current) return;
                    firstTap.current = false;
                    e.preventDefault();
                    setActive(gw);
                  }}
                  onFocus={() => setActive(gw)}
                  onBlur={() => setActive(null)}
                >
                  <rect
                    className="hit"
                    x={x(gw) - band / 2}
                    y={MARGIN.top}
                    width={band}
                    height={plotHeight + MARGIN.bottom}
                    rx={6}
                  />
                </Link>
              ))}
          </svg>
        )}
        {active !== null && width > 0 && (
          <Tip
            gameweek={active}
            values={values(active)}
            // Beside the crosshair, on the side with more room, and in the
            // half of the plot away from the week's points.
            style={{
              ...(x(active) > width / 2
                ? { right: width - x(active) + 10 }
                : { left: x(active) + 10 }),
              top:
                y(Math.max(...values(active).map((v) => v.mae))) < MARGIN.top + plotHeight / 2
                  ? MARGIN.top + plotHeight / 2
                  : MARGIN.top,
            }}
          />
        )}
      </div>
    </figure>
  );
}

// The readout for one week: the figure first, then whose it is. The link
// under the pointer already carries the same words for a screen reader.
function Tip({
  gameweek,
  values,
  style,
}: {
  gameweek: number;
  values: { series: Series; mae: number }[];
  style: CSSProperties;
}) {
  return (
    <div className="chart-tip" aria-hidden="true" style={style}>
      <strong>GW{gameweek}</strong>
      {values.map((v) => (
        <span className="chart-tip-row" key={v.series.name}>
          <i className={`key ${v.series.className}`} />
          <b>{v.mae.toFixed(2)}</b>
          {v.series.name}
        </span>
      ))}
    </div>
  );
}

// Runs of consecutive gameweeks, so a line breaks where a week is missing.
function runs(weeks: Week[]): Week[][] {
  const out: Week[][] = [];
  for (const week of [...weeks].sort((a, b) => a.gameweek - b.gameweek)) {
    const run = out.at(-1);
    if (run && run[run.length - 1].gameweek === week.gameweek - 1) run.push(week);
    else out.push([week]);
  }
  return out;
}

function range(from: number, to: number): number[] {
  return Array.from({ length: to - from + 1 }, (_, i) => from + i);
}

// The element's width, kept current as it resizes, so the chart is drawn at
// its real size and its labels stay the size of the page's text.
function useWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) setWidth(entry.contentRect.width);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}
