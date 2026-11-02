# 0025. The frontend is served by the API

- **Status:** Accepted
- **Date:** 2026-11-01
- **Issues:** #121, #10, #75

## Context

#10 planned a React frontend over the hosted API, deployed to Cloudflare
Pages. That is a second host and a second deploy for a site of a few pages.
It is also a new third-party account, which the project avoids where what it
already has will do. And it puts the site and the API on two origins, so the
API would need CORS.

The API already runs on Fly from one image, deployed on every green `main`.

## Decision

- **One image, one origin.** `web/` (Vite, React, TypeScript) is built in a
  stage of the API's Dockerfile, and its static files are copied into the
  image. `fpl.api.site` serves them at `/` from `FPL_WEB_DIR`.
- **The API keeps every path it owns.** `/v1`, `/docs`, `/openapi.json`,
  `/healthz` and `/readyz` answer as before, 404 problems included. Any
  other GET is a file of the build or a client-side route, which gets
  `index.html`. The catch-all is not in the OpenAPI document.
- **Typed from the contract.** The client's types are generated from
  `docs/openapi.json`, and CI fails when they are stale, so an API change the
  site does not follow is a type error.
- **Without `FPL_WEB_DIR` there is no site**, so the CLIs and the Python tests
  never need Node.

## Consequences

- The site ships with the API on every deploy, with nothing to configure.
- The image gains a Node build stage, and CI a `web` job: types, build, and
  Playwright against `fpl-serve` serving the build.
- The site and the API share Fly's single machine. For a few static files
  this is nothing; the API's own caching headers apply to `/v1` as before.

## Revisit if

- The site needs server-side rendering, or traffic that a CDN in front of
  Fly would not absorb.
