# Security

## Reporting a vulnerability

Please report security problems privately, through GitHub's
[private vulnerability reporting](https://github.com/amanwaseem/fpl-predictor/security/advisories/new),
rather than in a public issue. Say what you found, how to reproduce it, and
what it could let someone do.

This is a one-person project, so replies are best effort: expect an
acknowledgement within a week, and a fix, or a reason there will not be one,
soon after.

## Scope

- The live site and API at [fpl-predictor-aman.fly.dev](https://fpl-predictor-aman.fly.dev).
- This repository: its code, its workflows, and the integrity of the
  prediction log in `predictions/`.

Not in scope: the Fantasy Premier League API itself, which this project only
reads from, and load or denial-of-service testing against the live site,
which runs on a single small machine.

## Supported versions

Only what is on `main`, which is what is deployed.
