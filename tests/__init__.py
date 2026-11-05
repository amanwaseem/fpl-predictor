"""The test suite, laid out like the package it tests.

Each folder mirrors part of fpl/ (api, db, ingest, models, log, evaluation,
optimise), plus tools/ for the scripts in tools/ and project/ for checks on
the repository itself: the README, the ADRs, packaging and layout. Shared
helpers live in support/.

Every folder is a package, so a test imports another's helpers by its full
name (`from tests.support import fixtures`) and `pytest` from the repository
root finds them all.
"""
