// The site is built into dist/ and served by the API itself (ADR 0025): one
// origin, so the client calls /v1 relatively and needs no CORS. In
// development, Vite proxies /v1 to a local `make serve`.
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/v1": "http://127.0.0.1:8000" },
  },
});
