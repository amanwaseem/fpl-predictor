// A k6 load test of POST /v1/optimise (#83). The README has the numbers.
//
//   FPL_SOLVER_RATE=off fpl-serve --port 8000     # the rate limit would 429 it
//   k6 run loadtest/optimise.js                   # BASE_URL=... for elsewhere
//
// Two scenarios, one after the other, on a real GW8 squad (squad.json, a legal
// squad within £97.0m from the committed baseline entry):
//
//   solved:  every request differs in its bank, so each is a cache miss and
//            runs the solver: three solves, for transfers from an owned squad.
//   cached:  the same request again and again, served from Postgres.
import http from "k6/http";
import { check } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:8000";
const squad = JSON.parse(open("./squad.json"));

export const options = {
  scenarios: {
    solved: {
      executor: "constant-vus", vus: 4, duration: "30s",
      exec: "solved", tags: { scenario: "solved" },
    },
    cached: {
      executor: "constant-vus", vus: 4, duration: "30s", startTime: "35s",
      exec: "cached", tags: { scenario: "cached" },
    },
  },
  thresholds: {
    "http_req_failed": ["rate<0.01"],
    "http_req_duration{scenario:solved}": ["p(95)<2000"],
    "http_req_duration{scenario:cached}": ["p(95)<200"],
  },
  summaryTrendStats: ["med", "p(95)", "p(99)", "max"],
};

function post(body) {
  const response = http.post(`${BASE_URL}/v1/optimise`, JSON.stringify(body),
    { headers: { "Content-Type": "application/json" } });
  check(response, { "200": (r) => r.status === 200 });
}

export function solved() {
  // A bank no other request has had: £0.0m-£99.9m by VU and iteration.
  const bank = ((__VU * 7919 + __ITER) % 1000) / 10;
  post({ ...squad, bank });
}

export function cached() {
  post(squad);
}
