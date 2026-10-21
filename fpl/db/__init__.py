"""The Postgres database: schema, connection and migrations (#77).

The database holds what the snapshots and the committed log say, in a form
that can be queried. It is rebuilt from them, never the other way round.
"""
