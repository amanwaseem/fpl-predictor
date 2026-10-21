# Shortcuts for the local database. Run with .venv active.
#
#   make up migrate     # start Postgres and bring the schema up to date
#   make seed           # load data/raw/LATEST and the committed log into it
#   make reconcile      # check the database's copy of the log against git
#   make test-db        # the database tests, against fpl_test
#   make serve          # the API at http://127.0.0.1:8000/docs, from the checkout
#   make openapi        # regenerate docs/openapi.json after changing the API
#   make fuzz           # schemathesis against a running `make serve`

FPL_DB_PORT ?= 5432
DATABASE_URL ?= postgresql+psycopg://fpl:fpl@localhost:$(FPL_DB_PORT)/fpl
TEST_DATABASE_URL ?= postgresql+psycopg://fpl:fpl@localhost:$(FPL_DB_PORT)/fpl_test
export FPL_DB_PORT DATABASE_URL

.PHONY: up down migrate seed reconcile serve openapi fuzz test test-db psql

up:
	docker compose up -d --wait db

down:
	docker compose down

migrate:
	alembic upgrade head

seed: migrate
	fpl-load
	fpl-project

reconcile:
	fpl-reconcile

serve:
	fpl-serve --reload

openapi:
	python tools/export_openapi.py

fuzz:
	schemathesis run http://127.0.0.1:8000/openapi.json --checks all

test:
	python -m unittest discover tests

test-db: up
	TEST_DATABASE_URL=$(TEST_DATABASE_URL) FPL_REQUIRE_DB=1 python -m unittest discover -s tests -t . -p "test_db*.py"

psql:
	docker compose exec db psql -U fpl fpl
