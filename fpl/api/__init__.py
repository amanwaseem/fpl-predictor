"""The read API: FastAPI over the Postgres database, under /v1 (#81).

schemas.py   what each route returns
queries.py   the SQL behind each route
routes.py    the routes themselves
problems.py  errors as RFC 9457 problem details
app.py       the application: routes, errors, caching
"""
