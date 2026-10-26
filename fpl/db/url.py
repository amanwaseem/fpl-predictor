"""Whether a DATABASE_URL can work, checked before connecting, without printing it.

A URL that cannot work otherwise fails far from its cause. On the first
deploy (#82), a secret holding `postgresql://` and a password, with no user,
host or database, surfaced as `failed to resolve host '***'`: a DNS error,
pointing away from the mistake. Diagnosing it printed the password.

Two levels, because libpq fills in a great deal itself:

- **Anywhere** (`url_problems(url)`), only what cannot connect is refused: no
  scheme or one that is not Postgres, a URL that cannot be read, a port that
  is not a port, quotes or spaces around it, and whitespace outside the query
  string. A missing user, password, host or database is fine: libpq takes
  them from the environment, ~/.pgpass, a Unix socket, or its defaults.
- **For the hosted database** (`complete=True`), a user, password, host and
  database must each be given, in the URL or its query string, since nothing
  else can supply them. The deploy checks its secret this way, and so does
  every command on Fly, where FPL_HOSTED is set (fly.toml).

`pooled()` is separate, for migrations alone: Neon's `-pooler` endpoint
suits the API, but migrations need the direct connection.

Every problem is named by what is wrong, never by what is there: no message
quotes any part of the URL, since any part of it may be the password.
tests/test_db_url.py checks that with a URL made of sentinel values.

Standard library only: the deploy workflow runs this through
tools/check_database_url.py before any dependency is installed.
"""

from urllib.parse import parse_qs, urlsplit

POSTGRES = frozenset({"postgres", "postgresql"})
EXAMPLE = "postgresql://user:password@host/database?sslmode=require"


class DatabaseURLError(ValueError):
    """A DATABASE_URL that is missing or cannot work. Commands exit with it."""


def scheme_of(url):
    """The URL's scheme, lowercased, or None if it has none."""
    scheme, separator, _ = url.partition("://")
    return scheme.lower() if separator else None


def is_postgres(scheme):
    """postgres, postgresql, or postgresql with any driver: postgresql+psycopg."""
    return scheme is not None and scheme.split("+", 1)[0] in POSTGRES


def _port_problem(netloc):
    """What is wrong with the port in a netloc, if anything; None for no port."""
    hosts = netloc.rpartition("@")[2]
    if "," in hosts:
        # libpq's several hosts, h1:5432,h2:5432: each checked alone.
        return next(filter(None, map(_port_problem, hosts.split(","))), None)
    if hosts.startswith("["):
        port = hosts.partition("]")[2].removeprefix(":")
    else:
        port = hosts.partition(":")[2]
    if not port:
        return None
    if not port.isdigit():
        return "its port is not a number"
    if not 0 < int(port) < 65536:
        return "its port is not between 1 and 65535"
    return None


def url_problems(url, complete=False):
    """Everything that stops `url` working, as phrases that quote none of it.

    With `complete`, also every part a hosted database needs and nothing else
    can supply: a user, password, host and database.
    """
    found = []
    stripped = url.strip().strip("'\"")
    if stripped != url:
        found.append("it has quotes or spaces around it; set the connection string alone")
    scheme = scheme_of(stripped)
    if scheme is None:
        return found + ["it has no scheme; it should start with postgresql://"]
    if not is_postgres(scheme):
        found.append("its scheme is not postgresql")

    try:
        parts = urlsplit(stripped)
    except ValueError:
        # urlsplit's only refusal: an IPv6 address with no closing bracket.
        return found + ["it cannot be read as a URL; check any [IPv6] address is closed"]
    # Whitespace breaks the host, user and path, but libpq options in the query
    # string, such as options=-c search_path=x, may carry it.
    if any(c.isspace() for c in parts.netloc + parts.path):
        found.append("it contains whitespace outside the query string")
    port = _port_problem(parts.netloc)
    if port:
        found.append(port)

    if complete:
        query = parse_qs(parts.query)

        def given(part, key):
            return bool(part) or bool(query.get(key, [""])[0])

        if not given(parts.username, "user"):
            found.append("it has no user")
            if "@" not in parts.netloc and parts.hostname:
                # The deploy-day mistake: with nothing before an @, whatever
                # follows :// is read as the host, so a pasted password
                # becomes a hostname.
                found.append(
                    "with no user@ before it, what follows :// is being read as "
                    "the host, so a password pasted there looks like one"
                )
        if not given(parts.password, "password"):
            found.append("it has no password")
        if not given(parts.netloc.rpartition("@")[2], "host"):
            found.append("it has no host")
        if not given(parts.path.strip("/"), "dbname"):
            found.append("it names no database")
    return found


def pooled(url):
    """Whether `url` is Neon's pooled endpoint, which migrations should not use."""
    try:
        return "-pooler" in (urlsplit(url.strip().strip("'\"")).hostname or "")
    except ValueError:
        return False


POOLED = (
    "its host is a pooled (-pooler) endpoint; migrations need the direct "
    "connection string (docs/deploy.md)"
)


def describe_problems(found, name="DATABASE_URL"):
    """One message for a list of problems, saying why the value is not shown."""
    return (
        f"{name} is set but cannot be used: {'; '.join(found)}.\n"
        f"Its value is not shown, since it holds a password. It should look like "
        f"{EXAMPLE}"
    )
