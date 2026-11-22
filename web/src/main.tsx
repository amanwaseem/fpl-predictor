import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ApiError } from "./api/client";
import { App } from "./App";
// Self-hosted fonts (#193): bundled by Vite and served from our own origin, so
// no visitor request goes to a font CDN and the CSP stays default-src 'self'.
// Each @font-face carries a unicode-range, so a browser fetches only what a
// page's text needs: Latin, plus Latin Extended for names like Muharemović.
import "@fontsource-variable/inter/wght.css";
import "@fontsource/barlow-condensed/700.css";
import "@fontsource/barlow-condensed/700-italic.css";
import "./styles.css";

// The data changes about once a week; the API's own Cache-Control is five
// minutes. A 4xx problem is the answer, not a failure, so it is not retried.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000,
      retry: (failures, error) =>
        failures < 1 && !(error instanceof ApiError && error.status < 500),
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
