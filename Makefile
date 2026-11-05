# Shortcuts for the local database and the checks. Each command runs through
# `uv run --locked`, in the environment uv.lock describes (#76).
#
#   make lint           # ruff check, ruff format --check, mypy: what CI's lint job runs
#   make test           # the suite, by pytest
#   make up migrate     # start Postgres and bring the schema up to date
#   make seed           # load data/raw/LATEST and the committed log into it
#   make reconcile      # check the database's copy of the log against git
#   make test-db        # the database tests, against fpl_test
#   make serve          # the API at http://127.0.0.1:8000/docs, from the checkout,
#                       # and the site at / once `make web` has built it
#   make web            # build the web frontend into web/dist (needs Node 24)
#   make openapi        # regenerate docs/openapi.json after changing the API
#   make fuzz           # schemathesis against a running `make serve`

FPL_DB_PORT ?= 5432
DATABASE_URL ?= postgresql+psycopg://fpl:fpl@localhost:$(FPL_DB_PORT)/fpl
TEST_DATABASE_URL ?= postgresql+psycopg://fpl:fpl@localhost:$(FPL_DB_PORT)/fpl_test
export FPL_DB_PORT DATABASE_URL
RUN := uv run --locked

.PHONY: up down migrate seed reconcile serve web openapi fuzz lint test test-db psql

up:
	docker compose up -d --wait db

down:
	docker compose down

migrate:
	$(RUN) alembic upgrade head

seed: migrate
	$(RUN) fpl-load
	$(RUN) fpl-project

reconcile:
	$(RUN) fpl-reconcile

serve:
	$(if $(wildcard web/dist/index.html),FPL_WEB_DIR=web/dist) $(RUN) fpl-serve --reload

web:
	cd web && npm ci && npm run build

openapi:
	$(RUN) python tools/export_openapi.py

fuzz:
	$(RUN) schemathesis --config-file tests/api/schemathesis.toml run http://127.0.0.1:8000/openapi.json --checks all

lint:
	$(RUN) ruff check .
	$(RUN) ruff format --check .
	$(RUN) mypy

test:
	$(RUN) pytest

test-db: up
	TEST_DATABASE_URL=$(TEST_DATABASE_URL) FPL_REQUIRE_DB=1 $(RUN) pytest tests/db/test_db*.py

psql:
	docker compose exec db psql -U fpl fpl
