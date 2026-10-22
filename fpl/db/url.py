"""Whether a DATABASE_URL can work, checked before connecting, without printing it.

A URL that cannot work otherwise fails far from its cause. On the first
deploy (#82), a secret holding `postgresql://` and a password, with no user,
host or database, surfaced as `failed to resolve host '***'`: a DNS error,
pointing away from the mistake. Diagnosing it printed the password.

So the shape is checked first, and every problem is named by what is wrong,
never by what is there: no message here contains any part of the URL, because
any part of it may be the password. tests/test_db_url.py checks that with a
URL made of sentinel values.

Standard library only: the deploy workflow runs this through
tools/check_database_url.py before any dependency is installed.
"""

from urllib.parse import urlsplit

SCHEMES = frozenset({"postgres", "postgresql", "postgresql+psycopg"})
EXAMPLE = "postgresql://user:password@host/database?sslmode=require"


def url_problems(url):
    """Everything that stops `url` working, as phrases that quote none of it."""
    found = []
    stripped = url.strip().strip("'\"")
    if stripped != url:
        found.append("it has quotes or spaces around it; set the connection string alone")
    if any(c.isspace() for c in stripped):
        found.append("it contains whitespace")
    if "://" not in stripped:
        return found + ["it has no scheme; it should start with postgresql://"]

    try:
        parts = urlsplit(stripped)
        parts.port  # noqa: B018 - reading it is the check: a non-numeric port raises
    except ValueError:
        return found + ["it cannot be read as a URL; check the port is a number"]

    if parts.scheme not in SCHEMES:
        found.append("its scheme is not postgresql")
    if not parts.username:
        found.append("it has no user")
        if "@" not in parts.netloc and parts.hostname:
            # The deploy-day mistake: with nothing before an @, whatever follows
            # :// is read as the host, so a pasted password becomes a hostname.
            found.append("with no user@ before it, what follows :// is being read as the "
                         "host, so a password pasted there looks like one")
    if not parts.password:
        found.append("it has no password")
    if not parts.hostname:
        found.append("it has no host")
    elif "-pooler" in parts.hostname:
        found.append("its host is a pooled (-pooler) endpoint; use the direct connection "
                     "string, which migrations need (docs/deploy.md)")
    if not parts.path.strip("/"):
        found.append("it names no database")
    return found


def describe_problems(found, name="DATABASE_URL"):
    """One message for a list of problems, saying why the value is not shown."""
    return (f"{name} is set but cannot be used: {'; '.join(found)}.\n"
            f"Its value is not shown, since it holds a password. It should look like "
            f"{EXAMPLE}")
