# Shortcuts for the local database. Run with .venv active.
#
#   make up migrate     # start Postgres and bring the schema up to date
#   make test-db        # the database tests, against fpl_test

FPL_DB_PORT ?= 5432
DATABASE_URL ?= postgresql+psycopg://fpl:fpl@localhost:$(FPL_DB_PORT)/fpl
TEST_DATABASE_URL ?= postgresql+psycopg://fpl:fpl@localhost:$(FPL_DB_PORT)/fpl_test
export FPL_DB_PORT DATABASE_URL

.PHONY: up down migrate test test-db psql

up:
	docker compose up -d --wait db

down:
	docker compose down

migrate:
	alembic upgrade head

test:
	python -m unittest discover tests

test-db: up
	TEST_DATABASE_URL=$(TEST_DATABASE_URL) FPL_REQUIRE_DB=1 python -m unittest tests.test_db

psql:
	docker compose exec db psql -U fpl fpl
