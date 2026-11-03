// The site is built into dist/ and served by the API itself (ADR 0025): one
// origin, so the client calls /v1 relatively and needs no CORS. In
// development, Vite proxies the API's paths to a local `make serve`, or to
// any API given as API_TARGET: `API_TARGET=https://<the deployment> npm run dev`
// works on the site against live data without a local database.
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const target = process.env.API_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: Object.fromEntries(
      ["/v1", "/docs", "/openapi.json", "/healthz", "/readyz"].map((path) => [
        path,
        { target, changeOrigin: true },
      ]),
    ),
  },
});
